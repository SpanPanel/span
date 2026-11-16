"""Each circuit's entities live on a device of its own, a child of the panel's device.

Home Assistant names an entity on a device from the device's name and the
entity's own, so a circuit's device carries the circuit's name and each of its
entities only its label: "SPAN Panel" > "Kitchen Outlets" > "Power". A rename
in the SPAN app renames the device, and a name the user gave the device in Home
Assistant outranks the panel's, for every entity on it at once.

Set up through the integration's real `async_setup_entry` over a replayed
capture (`captures_replay.ReplayClient`): the real coordinator, platforms and
registries. The replay stays patched in for each case's whole run, so the
reload a rename asks for sets the entry up again over the same replay, which
is what lets a case rename a circuit on the panel and watch the integration
follow it.

The charger's feed circuit is the one exception: its sensors stay on the
charger's device, where they have always been, and only its breaker switch and
priority select, where it has them, go on a device of its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import contextlib
from dataclasses import dataclass
from typing import Final
from unittest.mock import MagicMock, patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import Entity
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import SpanPanelSnapshot
from span_panel_api.protocol import SchemaAdapter

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.helpers import (
    circuit_device_name,
    circuit_has_a_breaker_switch,
    circuit_has_a_priority_select,
)
from custom_components.span_panel.id_builder import (
    build_circuit_device_identifier,
    build_circuit_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
)
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.runtime import loaded_runtime_data
from custom_components.span_panel.select import (
    CIRCUIT_PRIORITY_DESCRIPTION,
    SpanPanelCircuitsSelect,
)
from custom_components.span_panel.sensor import create_circuit_sensors
from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

from .captures_replay import (
    CAPTURES,
    HOMIE_PREFIX,
    ReplayClient,
    RetainedTree,
    capture,
    replay,
    retained_payload,
    snapshot as replayed_snapshot,
)
from .factories import (
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
    pv_binding_for,
)
from .helpers import PANEL_NAME, register_panel_device

CHARGER_CAPTURE: Final = "r202639-d"
"""The capture with a charger on a feed circuit, beside ordinary named circuits."""

KITCHEN: Final = "circuit-2"
"""The circuit in that capture these cases name "Kitchen Outlets"."""

KITCHEN_OUTLETS: Final = "Kitchen Outlets"

UNNAMED_CAPTURE: Final = "main32_r202639"
"""The capture with a solar circuit, which these cases leave unnamed."""

CIRCUIT_LABELS: Final = frozenset(
    {
        "Power",
        "Produced Energy",
        "Consumed Energy",
        "Net Energy",
        "Current",
        "Breaker Rating",
        "Breaker",
        "Circuit Priority",
    }
)
"""Every label a circuit entity carries, and nothing else: the device says which circuit."""

NAMES_MODE: Final[Mapping[str, object]] = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
"""The naming flags the config flow writes when a user accepts the default."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


# ---------------------------------------------------------------------------
# A panel set up for real, which a case can rename circuits on
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Panel:
    """One entry set up over a replayed tree, and the replay it reads."""

    hass: HomeAssistant
    entry: MockConfigEntry
    adapter: SchemaAdapter

    @property
    def runtime(self) -> SpanPanelRuntimeData:
        """The entry's runtime data, which every setup replaces."""
        runtime = loaded_runtime_data(self.entry)
        assert runtime is not None, "the entry is not loaded"
        return runtime

    @property
    def snapshot(self) -> SpanPanelSnapshot:
        """What the panel publishes now."""
        return self.adapter.build_snapshot()

    @property
    def serial(self) -> str:
        return self.snapshot.serial_number

    async def poll(self) -> None:
        """One poll: read the panel, then run the reload an entity asked for on the last one."""
        await self.runtime.coordinator.async_refresh()
        await self.hass.async_block_till_done()

    async def rename_on_the_panel(self, circuit_id: str, name: str) -> None:
        """Rename a circuit in the SPAN app and let the integration follow it.

        The new name reaches the replay as the broker delivers it. The first
        poll reads it, and the circuit's entities ask for a reload; the poll
        after it runs that reload, as the next cycle does on a live panel.
        """
        self.adapter.handle_message(
            f"{HOMIE_PREFIX}/{circuit_id}/info/name", retained_payload(name)
        )
        before = self.runtime
        await self.poll()
        await self.poll()
        assert self.runtime is not before, "the rename never reloaded the entry"

    def circuit_device(self, circuit_id: str) -> dr.ChildDeviceEntry:
        """Return the circuit's own device."""
        device = dr.async_get(self.hass).async_get_child_device_by_identifier(
            (DOMAIN, build_circuit_device_identifier(self.serial, circuit_id)),
            self.entry.entry_id,
        )
        assert device is not None, f"{circuit_id} has no device of its own"
        return device

    def entity(self, domain: str, unique_id: str) -> er.RegistryEntry:
        """Return the registry row an entity of this entry is filed under."""
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        assert entity_id is not None, f"no {domain} entity {unique_id}"
        entry = registry.async_get(entity_id)
        assert entry is not None
        return entry

    def circuit_power(self, circuit_id: str) -> er.RegistryEntry:
        """Return a circuit's power sensor."""
        return self.entity(
            "sensor", build_circuit_unique_id(self.serial, circuit_id, "instantPowerW")
        )


def _named(tree: RetainedTree, circuit_id: str, name: str) -> RetainedTree:
    """Name one circuit in the tree, as the SPAN app would have named it."""
    tree[circuit_id]["info/name"] = name
    return tree


def _kitchen_tree() -> RetainedTree:
    return _named(capture(CHARGER_CAPTURE).tree(), KITCHEN, KITCHEN_OUTLETS)


@contextlib.asynccontextmanager
async def _panel(hass: HomeAssistant, tree: RetainedTree) -> AsyncIterator[_Panel]:
    """Set the tree up as a fresh installation, settled past its first reload.

    A fresh install asks for a reload on its entities' first update, because
    none of them had a registry row to remember the circuit's name from. That
    reload is run here, and a poll after it must ask for nothing, so a case
    that renames a circuit next is sure the reload it sees is the rename's.
    """
    adapter = replay(tree)
    serial = adapter.build_snapshot().serial_number
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.0.2.10",
            "device_name": PANEL_NAME,
            CONF_EBUS_BROKER_HOST: f"{serial}.local",
            CONF_EBUS_BROKER_USERNAME: "replay-user",
            CONF_EBUS_BROKER_PASSWORD: "replay-password",
            CONF_EBUS_BROKER_PORT: 8883,
        },
        options=dict(NAMES_MODE),
        title=PANEL_NAME,
        entry_id="entry-circuit-devices",
        unique_id=serial,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.span_panel.SpanMqttClient", return_value=ReplayClient(adapter)):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        panel = _Panel(hass, entry, adapter)
        # What the first streamed snapshot does on a live panel.
        panel.runtime.coordinator.async_update_listeners()
        await hass.async_block_till_done()
        await panel.poll()
        settled = panel.runtime
        await panel.poll()
        assert panel.runtime is settled, "a settled install asked for another reload"
        try:
            yield panel
        finally:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def _unnamed_tree() -> RetainedTree:
    """Return a MAIN 32 capture with its solar circuit and one other circuit left unnamed.

    No capture has an unnamed circuit inside the panel, so this one is made:
    an unnamed solar circuit's device is named "Solar", any other's by its
    breaker positions.
    """
    tree = capture(UNNAMED_CAPTURE).tree()
    circuits = replayed_snapshot(tree).circuits
    solar = next(cid for cid, circuit in circuits.items() if circuit.device_type == "pv")
    other = next(cid for cid, circuit in sorted(circuits.items()) if circuit.device_type != "pv")
    tree = _named(_named(tree, solar, ""), other, "")
    unnamed = replayed_snapshot(tree).circuits
    assert (unnamed[solar].name, unnamed[other].name) == ("", ""), "the replay kept a name"
    return tree


TREES: Final = [
    *(pytest.param(captured.tree, id=captured.name) for captured in CAPTURES),
    pytest.param(_unnamed_tree, id=f"{UNNAMED_CAPTURE}-unnamed"),
]
"""Every capture, and one with circuits no capture leaves unnamed."""


def _feed_circuits(snapshot: SpanPanelSnapshot) -> set[str]:
    """Return the circuits a charger is fed from, whose sensors stay on the charger's device."""
    return {evse.feed_circuit_id for evse in snapshot.evse.values()}


# ---------------------------------------------------------------------------
# The devices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tree", TREES)
async def test_each_circuit_has_a_child_device_named_by_the_circuit(
    hass: HomeAssistant, tree: Callable[[], RetainedTree]
) -> None:
    """One child device of the panel per circuit, under the circuit's identifier and name.

    Every capture and the unnamed variant, so every naming rule is reached:
    meters outside the panel, named circuits, an unnamed solar circuit, an
    unnamed circuit in the panel, and names two circuits share. A charger's
    feed circuit has a device only where it has a breaker switch or a priority
    select to put on it, because its sensors stay on the charger's device.
    """
    async with _panel(hass, tree()) as panel:
        registry = dr.async_get(hass)
        children = dr.async_entries_for_parent_device(registry, panel.runtime.panel_device_id)

        snapshot = panel.snapshot
        feeds = _feed_circuits(snapshot)
        expected = {
            build_circuit_device_identifier(panel.serial, circuit_id): circuit_device_name(
                circuit, circuit_id
            )
            for circuit_id, circuit in snapshot.circuits.items()
            if circuit_id not in feeds
            or circuit_has_a_breaker_switch(circuit)
            or circuit_has_a_priority_select(circuit)
        }
        actual = {
            identifier: device.name
            for device in children
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        }
        assert actual == expected
        assert len(children) == len(expected), "a circuit device carries a second identifier"


async def test_circuit_entities_carry_only_their_label(hass: HomeAssistant) -> None:
    """The entity says what it measures; its device says which circuit, its parent which panel."""
    async with _panel(hass, _kitchen_tree()) as panel:
        power = panel.circuit_power(KITCHEN)

        assert power.original_name == "Power"
        assert power.device_id == panel.circuit_device(KITCHEN).id
        assert er.async_get_full_entity_name(hass, power) == "SPAN Panel Kitchen Outlets Power"

        registry = er.async_get(hass)
        labels = {
            entity.original_name
            for device in dr.async_entries_for_parent_device(
                dr.async_get(hass), panel.runtime.panel_device_id
            )
            for entity in er.async_entries_for_device(
                registry, device.id, include_disabled_entities=True
            )
        }
        assert labels == CIRCUIT_LABELS


async def test_new_install_ids_match_today(hass: HomeAssistant) -> None:
    """Composed from the panel, the circuit's device and the suffix: the ids new installs had."""
    async with _panel(hass, _kitchen_tree()) as panel:
        serial = panel.serial

        assert panel.circuit_power(KITCHEN).entity_id == "sensor.span_panel_kitchen_outlets_power"
        assert (
            panel.entity(
                "sensor", build_circuit_unique_id(serial, KITCHEN, "consumedEnergyWh")
            ).entity_id
            == "sensor.span_panel_kitchen_outlets_consumed_energy"
        )
        assert (
            panel.entity("switch", build_switch_unique_id(serial, KITCHEN)).entity_id
            == "switch.span_panel_kitchen_outlets_breaker"
        )
        assert (
            panel.entity("select", build_select_unique_id(serial, KITCHEN)).entity_id
            == "select.span_panel_kitchen_outlets_circuit_priority"
        )


async def test_evse_feed_sensors_stay_on_the_charger_device(hass: HomeAssistant) -> None:
    """The charger's feed circuit keeps its sensors on the charger's device, under bare labels.

    In this capture the feed circuit has neither a breaker switch nor a priority
    select, so nothing of it goes on a device of its own and it has none.
    """
    async with _panel(hass, capture(CHARGER_CAPTURE).tree()) as panel:
        snapshot = panel.snapshot
        (evse,) = snapshot.evse.values()
        feed = evse.feed_circuit_id
        charger = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, f"{panel.serial}_evse_{evse.node_id}"), panel.entry.entry_id
        )
        assert charger is not None

        power = panel.circuit_power(feed)
        assert power.device_id == charger.id
        assert power.original_name == "Power"
        assert (
            dr.async_get(hass).async_get_child_device_by_identifier(
                (DOMAIN, build_circuit_device_identifier(panel.serial, feed)),
                panel.entry.entry_id,
            )
            is None
        )


# ---------------------------------------------------------------------------
# Renames
# ---------------------------------------------------------------------------


async def test_a_rename_in_the_span_app_renames_the_device(hass: HomeAssistant) -> None:
    """The reload the rename asks for renames the device; nothing else about the circuit moves."""
    async with _panel(hass, _kitchen_tree()) as panel:
        device_before = panel.circuit_device(KITCHEN)
        power_before = panel.circuit_power(KITCHEN)

        await panel.rename_on_the_panel(KITCHEN, "Kitchen")

        device = panel.circuit_device(KITCHEN)
        power = panel.circuit_power(KITCHEN)
        assert device.id == device_before.id
        assert device.name == "Kitchen"
        assert power.device_id == device.id
        assert power.entity_id == power_before.entity_id  # R5: the id did not move
        assert power.original_name == "Power"
        assert er.async_get_full_entity_name(hass, power) == "SPAN Panel Kitchen Power"
        # Issue #252: Recreate follows the panel's new name.
        assert (
            er.async_get(hass).async_regenerate_entity_id(power)
            == "sensor.span_panel_kitchen_power"
        )


async def test_a_device_renamed_by_the_user_keeps_its_name(hass: HomeAssistant) -> None:
    """A name the user gave the circuit's device outranks the panel's; every entity follows it."""
    async with _panel(hass, _kitchen_tree()) as panel:
        devices = dr.async_get(hass)
        devices.async_update_child_device(panel.circuit_device(KITCHEN).id, name_by_user="Galley")

        await panel.rename_on_the_panel(KITCHEN, "Kitchen")

        device = panel.circuit_device(KITCHEN)
        assert (device.name, device.name_by_user) == ("Kitchen", "Galley")
        registry = er.async_get(hass)
        entities = er.async_entries_for_device(registry, device.id)
        assert entities, "the circuit's entities left its device"
        for entity in entities:
            assert entity.original_name is not None
            assert er.async_get_full_entity_name(hass, entity) == (
                f"SPAN Panel Galley {entity.original_name}"
            )
        # The offer is composed from the name the user gave the device.
        assert (
            registry.async_regenerate_entity_id(panel.circuit_power(KITCHEN))
            == "sensor.span_panel_galley_power"
        )


async def test_a_rename_reaches_the_device_when_the_user_named_every_entity(
    hass: HomeAssistant,
) -> None:
    """Names the user gave the circuit's entities do not hold its device on the old name.

    Each entity's own name is the user's and stays as they wrote it (R4). The
    circuit's name is the device's, which only the reload renames, so every
    entity still asks for it when the panel renames the circuit.
    """
    async with _panel(hass, _kitchen_tree()) as panel:
        registry = er.async_get(hass)
        entities = er.async_entries_for_device(registry, panel.circuit_device(KITCHEN).id)
        assert entities, "the circuit has no enabled entity to name"
        named = {entity.entity_id: f"Galley {entity.original_name}" for entity in entities}
        for entity_id, name in named.items():
            registry.async_update_entity(entity_id, name=name)

        await panel.rename_on_the_panel(KITCHEN, "Kitchen")

        assert panel.circuit_device(KITCHEN).name == "Kitchen"
        after = {entity_id: registry.async_get(entity_id) for entity_id in named}
        assert {
            entity_id: entity.name for entity_id, entity in after.items() if entity is not None
        } == named


# ---------------------------------------------------------------------------
# A charger's feed circuit that has controls
# ---------------------------------------------------------------------------

SERIAL: Final = "sp3-circuit-devices-001"
"""A synthetic serial, never a real panel's."""

FEED: Final = "7"


async def _add(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, entities: Sequence[Entity]
) -> None:
    """Add entities through a real `EntityPlatform`, as setup does, one domain at a time."""
    platform = MockEntityPlatform(hass, domain=domain, platform_name=DOMAIN)
    platform.config_entry = entry
    await platform.async_add_entities(entities)
    await hass.async_block_till_done()


async def test_a_feed_circuits_controls_go_on_its_own_device(hass: HomeAssistant) -> None:
    """Its sensors stay on the charger's device; its breaker switch and priority select do not.

    The controls were never on the charger's device -- they shared the panel's
    with every other circuit's -- so they move like every other circuit's.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=FEED,
        name="Garage Charger",
        device_type="evse",
        relative_position="DOWNSTREAM",
        tabs=[7, 9],
    )
    evse = SpanEvseSnapshotFactory.create(node_id="evse-0", feed_circuit_id=FEED)
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number=SERIAL, circuits={FEED: circuit}, evse={"evse-0": evse}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.50", "device_name": PANEL_NAME},
        options=dict(NAMES_MODE),
        title=PANEL_NAME,
        unique_id=SERIAL,
        entry_id="entry-feed-controls",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = snapshot
    coordinator.config_entry = entry
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=register_panel_device(hass, entry, SERIAL),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    assert circuit_has_a_breaker_switch(circuit)
    assert circuit_has_a_priority_select(circuit)

    await _add(hass, entry, "sensor", create_circuit_sensors(coordinator, snapshot, entry))
    await _add(hass, entry, "switch", [SpanPanelCircuitsSwitch(coordinator, FEED, PANEL_NAME)])
    await _add(
        hass,
        entry,
        "select",
        [SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, FEED, PANEL_NAME)],
    )

    devices = dr.async_get(hass)
    charger = devices.async_get_device_by_identifier(
        (DOMAIN, f"{SERIAL}_evse_{evse.node_id}"), entry.entry_id
    )
    own = devices.async_get_child_device_by_identifier(
        (DOMAIN, build_circuit_device_identifier(SERIAL, FEED)), entry.entry_id
    )
    assert charger is not None
    assert own is not None
    assert own.name == "Garage Charger"

    registry = er.async_get(hass)
    power_id = registry.async_get_entity_id(
        "sensor", DOMAIN, build_circuit_unique_id(SERIAL, FEED, "instantPowerW")
    )
    switch_id = registry.async_get_entity_id("switch", DOMAIN, build_switch_unique_id(SERIAL, FEED))
    select_id = registry.async_get_entity_id("select", DOMAIN, build_select_unique_id(SERIAL, FEED))
    assert power_id is not None and switch_id is not None and select_id is not None
    on_device = {
        entity_id: entry_row.device_id
        for entity_id in (power_id, switch_id, select_id)
        if (entry_row := registry.async_get(entity_id)) is not None
    }
    assert on_device == {power_id: charger.id, switch_id: own.id, select_id: own.id}
