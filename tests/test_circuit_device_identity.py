"""A circuit's own device: the identifier it is registered under and the name it shows.

Each circuit becomes a child device of its panel. The identifier is what later
code reads back to tell a circuit's device from the panel's and from every
sub-device's, with no snapshot to hand, and the name is what Home Assistant
composes each circuit entity's full name from. Both are pinned here, before
anything is put on the device.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.helpers import circuit_device_name
from custom_components.span_panel.id_builder import (
    build_circuit_device_identifier,
    circuit_id_from_device_identifier,
)
from custom_components.span_panel.util import (
    ADOPTED_IDENTIFIER_TOKEN,
    SUB_DEVICE_BESS,
    SUB_DEVICE_EVSE,
    SUB_DEVICE_MID,
    SUB_DEVICE_PV,
    circuit_device,
    circuit_device_info,
    circuit_id_of_device,
    classify_sub_device_identifier,
)

from .factories import SpanCircuitSnapshotFactory
from .helpers import add_panel_entry, register_circuit_device, register_panel_device

SERIAL = "sp3-242424-001"
"""A synthetic serial, never a real panel's."""

CIRCUIT_IDS = [
    pytest.param("0dad2f16cd514812ae1807b0457d473e", id="uuid"),
    pytest.param("circuit-7", id="numbered"),
    pytest.param("remote-ct-1", id="remote-ct"),
]
"""The circuit-key forms a panel publishes: a UUID, a numbered key and a meter outside the panel."""


@pytest.mark.parametrize(
    "circuit_id",
    [
        *CIRCUIT_IDS,
        # The id after the prefix is returned whole: an id that itself contains
        # the token is not split on it.
        pytest.param("circuit_7", id="id-containing-the-token"),
    ],
)
def test_identifier_round_trips(circuit_id: str) -> None:
    """The reading end gives back exactly the circuit id the writing end was given.

    The identifier is spelled out rather than rebuilt from the token, because
    the device registry keys the device on it: a changed spelling would orphan
    every circuit device, and its user-set name and area with it.
    """
    identifier = build_circuit_device_identifier(SERIAL, circuit_id)

    assert identifier == f"{SERIAL}_circuit_{circuit_id}"
    assert circuit_id_from_device_identifier(SERIAL, identifier) == circuit_id


@pytest.mark.parametrize(
    "serial",
    [
        pytest.param("sp3-242424-002", id="different-serial"),
        pytest.param("sp3-242424-00", id="serial-a-prefix-of-this-one"),
        pytest.param("sp3-242424-0010", id="serial-extending-this-one"),
        pytest.param(SERIAL.upper(), id="same-serial-other-case"),
    ],
)
def test_another_panels_circuit_device_is_not_this_panels(serial: str) -> None:
    """Only this panel's serial, spelled exactly as given, is read back.

    Two panels can publish the same circuit key, so a circuit device of another
    panel must never answer as one of this panel's circuits. The serial is
    compared unchanged because the registry compares identifiers that way.
    """
    identifier = build_circuit_device_identifier(serial, "circuit-7")

    assert circuit_id_from_device_identifier(SERIAL, identifier) is None


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param(SERIAL, id="panel"),
        pytest.param(f"{SERIAL}_{SUB_DEVICE_BESS}", id="bess"),
        pytest.param(f"{SERIAL}_{SUB_DEVICE_MID}", id="mid"),
        pytest.param(f"{SERIAL}_{SUB_DEVICE_EVSE}_circuit-7", id="evse"),
        pytest.param(f"{SERIAL}_{SUB_DEVICE_PV}", id="solar"),
        pytest.param(f"{SERIAL}_{SUB_DEVICE_PV}_circuit-7", id="pv-inverter"),
        pytest.param(f"{SERIAL}_{ADOPTED_IDENTIFIER_TOKEN}_circuit-7", id="adopted"),
        pytest.param(f"{SERIAL}_circuit_", id="empty-circuit-id"),
        pytest.param(f"{SERIAL}_circuit", id="token-without-an-id"),
    ],
)
def test_an_identifier_that_names_no_circuit_answers_none(identifier: str) -> None:
    """The panel's own device, every sub-device, and a token with no id after it name no circuit.

    The inverse reads the exact form back and never infers a circuit from what an
    identifier looks like, so a sub-device whose key is a circuit's id stays a
    sub-device.
    """
    assert circuit_id_from_device_identifier(SERIAL, identifier) is None


@pytest.mark.parametrize("circuit_id", CIRCUIT_IDS)
def test_identifier_is_not_a_sub_device(circuit_id: str) -> None:
    """A circuit's device never classifies as a BESS, MID, charger or solar card.

    Neither the circuit token nor any circuit-key form a panel publishes carries
    a token the sub-device classifier reads, so nothing that tells devices apart
    by identifier can mistake a circuit's device for hardware of its own.
    """
    assert (
        classify_sub_device_identifier(build_circuit_device_identifier(SERIAL, circuit_id)) is None
    )


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="unnamed"),
        pytest.param("Garage Subpanel", id="named"),
    ],
)
def test_device_name_prefers_remote_ct_label(name: str) -> None:
    """A meter outside the panel is named by its id's label, whatever name it carries.

    It has no breaker space, so it has no positions to fall back on, and its
    entities and topology row are already named by the same label.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="remote-ct-1", name=name, tabs=[], measures_outside_panel=True
    )

    assert circuit_device_name(circuit, "remote-ct-1") == "Remote CT 1"


@pytest.mark.parametrize("device_type", ["circuit", "pv", "evse"])
def test_device_name_uses_the_circuit_name(device_type: str) -> None:
    """The panel's name for the circuit wins over every fallback, whatever the circuit feeds."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="0dad2f16cd514812ae1807b0457d473e",
        name="Kitchen Outlets",
        device_type=device_type,
        tabs=[15],
    )

    assert circuit_device_name(circuit, "0dad2f16cd514812ae1807b0457d473e") == "Kitchen Outlets"


def test_device_name_falls_back_to_solar_for_pv() -> None:
    """An unnamed solar circuit is "Solar", as its entities have been named since v1."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="circuit-30", name="", device_type="pv", tabs=[30, 32]
    )

    assert circuit_device_name(circuit, "circuit-30") == "Solar"


def test_device_name_falls_back_to_tabs() -> None:
    """Any other unnamed circuit is named by its breaker positions, in ascending order.

    An unnamed charger circuit included: its sensors live on the charger's
    device, so its circuit device holds only the breaker switch and priority
    select, whose names already use the positions.
    """
    two_pole = SpanCircuitSnapshotFactory.create(circuit_id="circuit-30", name="", tabs=[32, 30])
    charger = SpanCircuitSnapshotFactory.create(
        circuit_id="circuit-7", name="", device_type="evse", tabs=[7]
    )

    assert circuit_device_name(two_pole, "circuit-30") == "Circuit 30 32"
    assert circuit_device_name(charger, "circuit-7") == "Circuit 7"


async def test_child_device_info_names_the_panel_as_parent(hass: HomeAssistant) -> None:
    """The device info registers a child of the panel device, under the circuit's identifier and name.

    Compared whole, so the builder sets nothing it was not asked to, then
    registered for real, so the registry is shown to accept it as the panel's
    child rather than the dictionary only looking right.
    """
    entry = add_panel_entry(hass, "entry-circuit-device", SERIAL)
    panel_device_id = register_panel_device(hass, entry, SERIAL)
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="circuit-7", name="Kitchen Outlets", tabs=[7]
    )
    identifier = (DOMAIN, f"{SERIAL}_circuit_circuit-7")

    info = circuit_device_info(SERIAL, circuit, "circuit-7", panel_device_id=panel_device_id)

    assert info.as_dict() == {
        "identifiers": {identifier},
        "name": "Kitchen Outlets",
        "parent_device_id": panel_device_id,
    }

    registry = dr.async_get(hass)
    device = registry.async_get_or_create_child(config_entry_id=entry.entry_id, **info)

    assert device.parent_device_id == panel_device_id
    assert device.name == "Kitchen Outlets"
    assert registry.async_get_child_device_by_identifier(identifier, entry.entry_id) == device
    assert dr.async_entries_for_parent_device(registry, panel_device_id) == [device]


async def test_a_circuits_device_is_found_by_its_circuit_and_read_back_to_it(
    hass: HomeAssistant,
) -> None:
    """The registry answers a circuit with its device, and the device with its circuit.

    Both ways round over the real registry, so the lookup and its reading end
    are shown to agree with what `circuit_device_info` registers. A circuit
    with no device registered answers None rather than another circuit's.
    """
    entry = add_panel_entry(hass, "entry-circuit-device", SERIAL)
    panel_device_id = register_panel_device(hass, entry, SERIAL)
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="circuit-7", name="Kitchen Outlets", tabs=[7]
    )
    device = register_circuit_device(hass, entry, SERIAL, circuit, panel_device_id=panel_device_id)
    registry = dr.async_get(hass)

    found = circuit_device(registry, SERIAL, "circuit-7", config_entry_id=entry.entry_id)

    assert found == device
    assert circuit_id_of_device(SERIAL, device) == "circuit-7"
    assert circuit_device(registry, SERIAL, "circuit-9", config_entry_id=entry.entry_id) is None


@pytest.mark.parametrize(
    ("identifiers", "circuit_id"),
    [
        pytest.param({(DOMAIN, f"{SERIAL}_outlet_circuit-7")}, None, id="not-a-circuit-device"),
        pytest.param(
            {(DOMAIN, build_circuit_device_identifier("sp3-242424-002", "circuit-7"))},
            None,
            id="another-panels-circuit",
        ),
        pytest.param(
            {("other_domain", build_circuit_device_identifier(SERIAL, "circuit-7"))},
            None,
            id="another-integrations-identifier",
        ),
        pytest.param(
            {
                ("other_domain", "strip-1_outlet-1"),
                (DOMAIN, build_circuit_device_identifier(SERIAL, "circuit-7")),
            },
            "circuit-7",
            id="beside-another-integrations-identifier",
        ),
    ],
)
async def test_a_child_device_is_a_circuits_only_by_this_panels_identifier(
    hass: HomeAssistant, identifiers: set[tuple[str, str]], circuit_id: str | None
) -> None:
    """A child device names a circuit only through this integration's identifier for this panel.

    Hanging off the panel does not make a child device a circuit's, and
    neither does an identifier of the right shape in another domain or for
    another panel.
    """
    entry = add_panel_entry(hass, "entry-circuit-device", SERIAL)
    device = dr.async_get(hass).async_get_or_create_child(
        config_entry_id=entry.entry_id,
        identifiers=identifiers,
        name="Part",
        parent_device_id=register_panel_device(hass, entry, SERIAL),
    )

    assert circuit_id_of_device(SERIAL, device) == circuit_id
