"""An existing install's circuit entities move to their circuits' devices at setup.

Releases before 2.3.0 put every circuit entity on the panel's device, named
"Kitchen Outlets Power". Setup now moves each one to its circuit's own device
and names it by its label, so Home Assistant composes the same name from the
device. Along the way it hands back names an older release wrote, moves a
circuit's area to its device where that cannot put a breaker in a room, trims a
circuit name the user's own name began with, and afterwards raises one notice
listing what changed.

Each case seeds the registry the way 2.3.0b4 left it, with every circuit entity
on the panel's device and holding the ID base that release stored, then runs
the integration's real `async_setup_entry` over a replayed capture
(`captures_replay.ReplayClient`): the real coordinator, platforms and
registries.

Only the entities a setup builds are converted by it. A row whose entity no
platform adds that time stays on the panel's device as it was, and converts at
the setup that builds its entity again.

The suite runs on Home Assistant 2026.10.4, where an entity the user renamed
still displays that name whole. Cases about renamed entities therefore assert
the stored fields (`name`, `device_id`, `original_name`, `area_id`), and their
composed names are checked on 2026.11 by hand.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Mapping
import contextlib
from dataclasses import dataclass, replace
from typing import Final
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot
from span_panel_api.protocol import SchemaAdapter

from custom_components.span_panel.circuit_device_conversion import (
    AreaChange,
    ConversionReport,
    ConvertedEntity,
    async_convert_circuit_entities,
)
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
    ENABLE_CIRCUIT_NET_ENERGY_SENSORS,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.control_gate import ControlMode
from custom_components.span_panel.conversion_repairs import (
    async_clear_conversion_notice,
    async_raise_conversion_notice,
)
from custom_components.span_panel.extension import is_extension_unique_id
from custom_components.span_panel.helpers import (
    circuit_has_a_breaker_switch,
    circuit_has_a_priority_select,
    construct_circuit_identifier_from_tabs,
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
from custom_components.span_panel.options import CONTROL_MODE

from .captures_replay import CAPTURES, ReplayClient, RetainedTree, capture, replay
from .helpers import PANEL_NAME, add_panel_entry, register_panel_device

CAPTURE: Final = "r202639-d"
"""A capture with switched circuits, one the panel offers no control on, and a charger."""

KITCHEN: Final = "circuit-2"
"""A circuit with a breaker switch and a priority select, named "Kitchen Outlets" here."""

KITCHEN_OUTLETS: Final = "Kitchen Outlets"

LIGHTS: Final = "circuit-1"
"""A second circuit with a breaker switch."""

UNSWITCHED: Final = "circuit-8"
"""A circuit the panel offers no breaker switch on, named "Workshop" here."""

WORKSHOP: Final = "Workshop"

FEED: Final = "circuit-5"
"""The charger's feed circuit, whose sensors live on the charger's device."""

SECOND_PANEL_CAPTURE: Final = "r202639-c"
SECOND_PANEL_NAME: Final = "SPAN Panel 2"

NAMES_MODE: Final[Mapping[str, object]] = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
NUMBERS_MODE: Final[Mapping[str, object]] = {USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: True}

NET_ENERGY_OFF: Final[Mapping[str, object]] = {ENABLE_CIRCUIT_NET_ENERGY_SENSORS: False}
"""The option under which no circuit's Net Energy sensor is built."""

CONTROLS_OFF: Final[Mapping[str, object]] = {CONTROL_MODE: ControlMode.DISABLED.value}
"""The option under which no breaker switch and no priority select is built."""

NONE_LISTED: Final = "—"
"""What the notice shows in place of a list with nothing in it."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


# ---------------------------------------------------------------------------
# An install as 2.3.0b4 left it
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Kind:
    """One kind of circuit entity, as a release before 2.3.0 registered it."""

    domain: str
    label: str
    """What the entity measures, which is its whole name once the circuit has a device."""

    id_suffix: str
    """What its entity ID ends with."""

    @property
    def id_words(self) -> str:
        """Return the words the release put after the circuit in the entity's ID base."""
        return self.id_suffix.replace("_", " ")

    unique_id: Callable[[str, str], str]
    """Its unique ID, from the panel's serial and the circuit's ID."""

    present: Callable[[SpanCircuitSnapshot], bool]
    """Whether the integration creates it for this circuit."""

    disabled_by_default: bool = False


POWER: Final = _Kind(
    "sensor",
    "Power",
    "power",
    lambda serial, circuit_id: build_circuit_unique_id(serial, circuit_id, "instantPowerW"),
    lambda circuit: True,
)
PRODUCED: Final = _Kind(
    "sensor",
    "Produced Energy",
    "produced_energy",
    lambda serial, circuit_id: build_circuit_unique_id(serial, circuit_id, "producedEnergyWh"),
    lambda circuit: True,
)
CONSUMED: Final = _Kind(
    "sensor",
    "Consumed Energy",
    "consumed_energy",
    lambda serial, circuit_id: build_circuit_unique_id(serial, circuit_id, "consumedEnergyWh"),
    lambda circuit: True,
)
NET: Final = _Kind(
    "sensor",
    "Net Energy",
    "net_energy",
    lambda serial, circuit_id: build_circuit_unique_id(serial, circuit_id, "netEnergyWh"),
    lambda circuit: True,
)
CURRENT: Final = _Kind(
    "sensor",
    "Current",
    "current",
    lambda serial, circuit_id: build_circuit_unique_id(serial, circuit_id, "current"),
    lambda circuit: circuit.current_a is not None,
)
BREAKER_RATING: Final = _Kind(
    "sensor",
    "Breaker Rating",
    "breaker_rating",
    lambda serial, circuit_id: build_circuit_unique_id(serial, circuit_id, "breaker_rating"),
    lambda circuit: circuit.breaker_rating_a is not None,
    disabled_by_default=True,
)
BREAKER: Final = _Kind(
    "switch", "Breaker", "breaker", build_switch_unique_id, circuit_has_a_breaker_switch
)
PRIORITY: Final = _Kind(
    "select",
    "Circuit Priority",
    "circuit_priority",
    build_select_unique_id,
    circuit_has_a_priority_select,
)

KINDS: Final = (POWER, PRODUCED, CONSUMED, NET, CURRENT, BREAKER_RATING, BREAKER, PRIORITY)
"""Every circuit entity, in the order the platforms registered them."""


@dataclass(frozen=True, slots=True)
class _Stored:
    """The fields of a registry row the conversion may write, and the two it must not."""

    entity_id: str
    unique_id: str
    device_id: str | None
    name: str | None
    original_name: str | None
    area_id: str | None


def _stored(row: er.RegistryEntry) -> _Stored:
    return _Stored(
        row.entity_id, row.unique_id, row.device_id, row.name, row.original_name, row.area_id
    )


@dataclass(frozen=True, slots=True)
class _Install:
    """One panel's entry, its replay, and the single device its entities start on."""

    hass: HomeAssistant
    entry: MockConfigEntry
    adapter: SchemaAdapter
    panel_device_id: str

    @property
    def snapshot(self) -> SpanPanelSnapshot:
        """What the panel publishes."""
        return self.adapter.build_snapshot()

    @property
    def serial(self) -> str:
        return self.snapshot.serial_number

    @property
    def circuits(self) -> list[str]:
        """The circuits whose entities started on the panel's device."""
        feeds = {evse.feed_circuit_id for evse in self.snapshot.evse.values()}
        return [circuit_id for circuit_id in self.snapshot.circuits if circuit_id not in feeds]

    def kinds(self, circuit_id: str) -> list[_Kind]:
        """Return the entities the integration creates for a circuit."""
        circuit = self.snapshot.circuits[circuit_id]
        return [kind for kind in KINDS if kind.present(circuit)]

    def row(self, circuit_id: str, kind: _Kind) -> er.RegistryEntry:
        """Return the registry row of one of a circuit's entities."""
        registry = er.async_get(self.hass)
        unique_id = kind.unique_id(self.serial, circuit_id)
        entity_id = registry.async_get_entity_id(kind.domain, DOMAIN, unique_id)
        assert entity_id is not None, f"no {kind.domain} entity {unique_id}"
        row = registry.async_get(entity_id)
        assert row is not None
        return row

    def rows(self, circuit_id: str) -> list[er.RegistryEntry]:
        """Return every row of a circuit's entities."""
        return [self.row(circuit_id, kind) for kind in self.kinds(circuit_id)]

    def all_rows(self) -> list[er.RegistryEntry]:
        """Return every circuit entity's row."""
        return [row for circuit_id in self.circuits for row in self.rows(circuit_id)]

    def update(
        self,
        circuit_id: str,
        kind: _Kind,
        *,
        name: str | None = None,
        area_id: str | None = None,
    ) -> er.RegistryEntry:
        """Rename an entity or give it an area, as its user would in Home Assistant."""
        registry = er.async_get(self.hass)
        row = self.row(circuit_id, kind)
        if name is not None:
            row = registry.async_update_entity(row.entity_id, name=name)
        if area_id is not None:
            row = registry.async_update_entity(row.entity_id, area_id=area_id)
        return row

    def find_circuit_device(self, circuit_id: str) -> dr.ChildDeviceEntry | None:
        """Return the circuit's own device, if it has one."""
        return dr.async_get(self.hass).async_get_child_device_by_identifier(
            (DOMAIN, build_circuit_device_identifier(self.serial, circuit_id)),
            self.entry.entry_id,
        )

    def circuit_device(self, circuit_id: str) -> dr.ChildDeviceEntry:
        """Return the circuit's own device."""
        device = self.find_circuit_device(circuit_id)
        assert device is not None, f"{circuit_id} has no device of its own"
        return device

    def full_name(self, row: er.RegistryEntry) -> str:
        """Return the name Home Assistant displays the entity under."""
        return er.async_get_full_entity_name(self.hass, row)

    def full_names(self) -> dict[str, str]:
        """Return every circuit entity's displayed name, by entity ID."""
        return {row.entity_id: self.full_name(row) for row in self.all_rows()}

    def notice(self) -> ir.IssueEntry | None:
        """Return this entry's conversion notice, if one is raised."""
        return ir.async_get(self.hass).async_get_issue(
            DOMAIN, f"circuit_devices_converted_{self.entry.entry_id}"
        )

    def notice_lists(self) -> dict[str, str]:
        """Return what the raised notice lists."""
        notice = self.notice()
        assert notice is not None, "no conversion notice was raised"
        placeholders = notice.translation_placeholders
        assert placeholders is not None
        return dict(placeholders)

    def convert(self, snapshot: SpanPanelSnapshot | None = None) -> ConversionReport:
        """Run the conversion alone, without the setup around it."""
        return async_convert_circuit_entities(
            self.hass, self.entry, snapshot or self.snapshot, self.panel_device_id
        )


def _named(tree: RetainedTree, names: Mapping[str, str]) -> RetainedTree:
    """Name circuits in the tree, as the SPAN app would have named them."""
    for circuit_id, name in names.items():
        tree[circuit_id]["info/name"] = name
    return tree


def _tree() -> RetainedTree:
    """Return the capture with a switched "Kitchen Outlets" and a "Workshop" with no switch."""
    return _named(capture(CAPTURE).tree(), {KITCHEN: KITCHEN_OUTLETS, UNSWITCHED: WORKSHOP})


def _unpublished(tree: RetainedTree, circuit_id: str, published_property: str) -> RetainedTree:
    """Take a property out of the tree, as a panel that stopped publishing it for a circuit."""
    del tree[circuit_id][published_property]
    return tree


async def _change_options(install: _Install, options: Mapping[str, object]) -> None:
    """Save new options for a panel that is set up, which reloads it."""
    install.hass.config_entries.async_update_entry(install.entry, options=dict(options))
    await install.hass.async_block_till_done()
    assert install.entry.state is ConfigEntryState.LOADED


def _install(
    hass: HomeAssistant,
    tree: RetainedTree,
    *,
    options: Mapping[str, object] = NAMES_MODE,
    entry_id: str = "entry-conversion",
    panel_name: str = PANEL_NAME,
) -> _Install:
    """Add an entry for the tree's panel, with its registry as 2.3.0b4 left it.

    One device, the panel's, carrying every circuit entity under the name the
    release gave it, "Kitchen Outlets Power", and the ID its naming mode built:
    from the circuit's name, or from its breaker positions. A charger's feed
    circuit's sensors are left out: they were on the charger's device.

    Each row holds the ID base the release stored, "Kitchen Outlets power", and
    no suggested ID, so Home Assistant composed its ID then and composes it
    again for "Recreate entity IDs". A row seeded with a suggested ID instead
    would be offered that ID whatever became of its base.
    """
    adapter = replay(tree)
    snapshot = adapter.build_snapshot()
    serial = snapshot.serial_number
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.0.2.10",
            "device_name": panel_name,
            CONF_EBUS_BROKER_HOST: f"{serial}.local",
            CONF_EBUS_BROKER_USERNAME: "replay-user",
            CONF_EBUS_BROKER_PASSWORD: "replay-password",
            CONF_EBUS_BROKER_PORT: 8883,
        },
        options=dict(options),
        title=panel_name,
        entry_id=entry_id,
        unique_id=serial,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, serial)}, name=panel_name
    )
    install = _Install(hass, entry, adapter, panel.id)

    registry = er.async_get(hass)
    for circuit_id in install.circuits:
        circuit = snapshot.circuits[circuit_id]
        identifier = (
            construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)
            if options[USE_CIRCUIT_NUMBERS]
            else circuit.name
        )
        for kind in install.kinds(circuit_id):
            registry.async_get_or_create(
                kind.domain,
                DOMAIN,
                kind.unique_id(serial, circuit_id),
                config_entry=entry,
                device_id=panel.id,
                has_entity_name=True,
                original_name=f"{circuit.name} {kind.label}",
                object_id_base=f"{identifier} {kind.id_words}",
                disabled_by=(
                    er.RegistryEntryDisabler.INTEGRATION if kind.disabled_by_default else None
                ),
            )
    return install


@contextlib.asynccontextmanager
async def _set_up(*installs: _Install) -> AsyncIterator[Mapping[str, _Stored]]:
    """Set the entries up over their replays, and unload them afterwards.

    Yields each entity's stored fields as they stood when the platforms were
    forwarded. The platforms would move an entity to the device its device
    info names by themselves, so only what was true before they ran shows the
    conversion at work.

    Home Assistant sets up every entry of a domain with the first, so two
    panels are set up together, each over its own replay. The replays stay
    patched in for the whole run, so a case can reload an entry and have it
    set up over the same panel.
    """
    hass = installs[0].hass
    clients = {install.serial: ReplayClient(install.adapter) for install in installs}
    at_forward: dict[str, _Stored] = {}
    forward = hass.config_entries.async_forward_entry_setups

    def client_for(host: str, serial: str, *args: object, **kwargs: object) -> ReplayClient:
        return clients[serial]

    async def recording_forward(entry: ConfigEntry, platforms: Iterable[Platform | str]) -> None:
        at_forward.update(
            {
                row.entity_id: _stored(row)
                for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            }
        )
        await forward(entry, platforms)

    with (
        patch("custom_components.span_panel.SpanMqttClient", side_effect=client_for),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=recording_forward
        ),
    ):
        assert await hass.config_entries.async_setup(installs[0].entry.entry_id) is True
        await hass.async_block_till_done()
        for install in installs:
            assert install.entry.state is ConfigEntryState.LOADED, install.entry.title
        try:
            yield at_forward
        finally:
            for install in installs:
                await hass.config_entries.async_unload(install.entry.entry_id)
            await hass.async_block_till_done()


def _area(hass: HomeAssistant, name: str) -> str:
    """Create an area and return its ID."""
    return ar.async_get(hass).async_create(name).id


# ---------------------------------------------------------------------------
# The move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tree", [pytest.param(captured.tree, id=captured.name) for captured in CAPTURES]
)
async def test_entities_move_to_their_circuit_device_with_label_names(
    hass: HomeAssistant, tree: Callable[[], RetainedTree]
) -> None:
    """Every circuit entity ends on its circuit's device, named by its label alone.

    Every capture, so every circuit a real panel publishes is converted. No
    entity ID or unique ID changes.
    """
    install = _install(hass, tree())
    before = {row.unique_id: row.entity_id for row in install.all_rows()}
    assert before, "the capture seeded no circuit entity"

    async with _set_up(install) as at_forward:
        for circuit_id in install.circuits:
            device = install.circuit_device(circuit_id)
            assert device.parent_device_id == install.panel_device_id
            for kind in install.kinds(circuit_id):
                row = install.row(circuit_id, kind)
                assert row.device_id == device.id, row.entity_id
                assert row.original_name == kind.label, row.entity_id
                assert row.name is None, row.entity_id
                # Already so when the platforms were forwarded: the conversion's
                # work, which they then had nothing to add to.
                assert at_forward[row.entity_id] == _stored(row), row.entity_id
        assert {row.unique_id: row.entity_id for row in install.all_rows()} == before


async def test_never_renamed_full_names_are_unchanged(hass: HomeAssistant) -> None:
    """A name the user never changed displays as it did, so there is nothing to tell them."""
    install = _install(hass, _tree())
    before = install.full_names()
    power = install.row(KITCHEN, POWER).entity_id
    assert before[power] == "SPAN Panel Kitchen Outlets Power"

    async with _set_up(install):
        assert install.full_names() == before
        assert install.notice() is None


async def test_renamed_entity_keeps_its_name_on_the_circuit_device(hass: HomeAssistant) -> None:
    """A name that does not start with the circuit's name is the user's, and is left alone."""
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, name="Fridge Power")

    async with _set_up(install):
        row = install.row(KITCHEN, POWER)
        assert row.name == "Fridge Power"
        assert row.device_id == install.circuit_device(KITCHEN).id
        assert row.original_name == "Power"


async def test_a_name_starting_with_the_circuit_is_trimmed(hass: HomeAssistant) -> None:
    """The device now says "Kitchen Outlets", so the user's name stops saying it too."""
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, name="Kitchen Outlets Power usage")

    async with _set_up(install):
        row = install.row(KITCHEN, POWER)
        assert row.name == "Power usage"
        assert row.device_id == install.circuit_device(KITCHEN).id


async def test_a_name_that_is_the_circuit_name_is_emptied(hass: HomeAssistant) -> None:
    """Nothing remains of it, so the entity is named by its device alone, as it was displayed.

    Stored empty, as Home Assistant's own migration stores a name equal to the
    device's, and not cleared: a cleared name would display the label after it.
    """
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, name=KITCHEN_OUTLETS)

    async with _set_up(install):
        row = install.row(KITCHEN, POWER)
        assert row.name == ""
        assert install.full_name(row) == "SPAN Panel Kitchen Outlets"


async def test_an_older_release_name_is_released_not_trimmed(hass: HomeAssistant) -> None:
    """A name 2.0.8 wrote is the integration's to take back, not a user's name to trim.

    Trimmed, it would be stored as "Power" and never recognised again as the
    integration's own.
    """
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, name="Kitchen Outlets Power")
    install.update(KITCHEN, CONSUMED, name="Kitchen Outlets Energy Consumed")

    async with _set_up(install) as at_forward:
        for kind in (POWER, CONSUMED):
            row = install.row(KITCHEN, kind)
            assert row.name is None
            assert at_forward[row.entity_id].name is None


async def test_disabled_entities_are_converted(hass: HomeAssistant) -> None:
    """A disabled entity is moved and renamed like any other, and stays disabled.

    Its platform builds it, and Home Assistant registers it again before
    declining to add it, so its row gets the ID base that goes with its
    circuit's device and is still offered the ID it has.
    """
    install = _install(hass, _tree())
    registry = er.async_get(hass)
    registry.async_update_entity(
        install.row(KITCHEN, POWER).entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    install.update(KITCHEN, BREAKER_RATING, name="Kitchen Outlets Rating")

    async with _set_up(install) as at_forward:
        device = install.circuit_device(KITCHEN)
        power = install.row(KITCHEN, POWER)
        rating = install.row(KITCHEN, BREAKER_RATING)
        assert (power.device_id, power.original_name) == (device.id, "Power")
        assert power.disabled_by is er.RegistryEntryDisabler.USER
        assert (rating.device_id, rating.original_name) == (device.id, "Breaker Rating")
        assert rating.name == "Rating"
        assert rating.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert at_forward[power.entity_id] == _stored(power)
        assert at_forward[rating.entity_id] == _stored(rating)
        assert power.object_id_base == "power"
        assert registry.async_regenerate_entity_id(power) == power.entity_id


async def test_numbers_mode_install_converts_with_ids_unchanged(hass: HomeAssistant) -> None:
    """A circuit-numbers install keeps its circuit-number IDs and gets the same devices."""
    install = _install(hass, _tree(), options=NUMBERS_MODE)
    before = {row.unique_id: row.entity_id for row in install.all_rows()}
    assert install.row(KITCHEN, POWER).entity_id == "sensor.span_panel_circuit_11_power"

    async with _set_up(install) as at_forward:
        assert {row.unique_id: row.entity_id for row in install.all_rows()} == before
        device = install.circuit_device(KITCHEN)
        assert device.name == KITCHEN_OUTLETS
        for kind in install.kinds(KITCHEN):
            row = install.row(KITCHEN, kind)
            assert (row.device_id, row.original_name) == (device.id, kind.label)
            assert at_forward[row.entity_id] == _stored(row)


# ---------------------------------------------------------------------------
# What is not a circuit entity on the panel's device
# ---------------------------------------------------------------------------


async def test_an_orphaned_circuit_entity_stays_on_the_panel(hass: HomeAssistant) -> None:
    """An entity of a circuit the panel no longer publishes is left exactly as it is."""
    install = _install(hass, _tree())
    garage = _area(hass, "Garage")
    registry = er.async_get(hass)
    orphan = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        build_circuit_unique_id(install.serial, "circuit-77", "instantPowerW"),
        config_entry=install.entry,
        device_id=install.panel_device_id,
        has_entity_name=True,
        original_name="Hot Tub Power",
    )
    orphan = registry.async_update_entity(orphan.entity_id, name="Hot Tub Watts", area_id=garage)
    before = _stored(orphan)

    async with _set_up(install):
        row = registry.async_get(orphan.entity_id)
        assert row is not None
        assert _stored(row) == before
        assert install.find_circuit_device("circuit-77") is None


async def test_extension_entities_stay_on_the_panel(hass: HomeAssistant) -> None:
    """A circuit-scoped extension entity is never re-homed, whatever circuit it names."""
    install = _install(hass, _tree())
    unique_id = f"span_{install.serial}_adopted_circuit_{KITCHEN}/vendor-node/reading"
    assert is_extension_unique_id(unique_id)
    registry = er.async_get(hass)
    extension = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=install.entry,
        device_id=install.panel_device_id,
        has_entity_name=True,
        original_name="Vendor Node Reading",
    )
    extension = registry.async_update_entity(extension.entity_id, name="Kitchen Outlets Reading")
    before = _stored(extension)

    async with _set_up(install):
        row = registry.async_get(extension.entity_id)
        assert row is not None
        assert _stored(row) == before


async def test_an_entity_the_integration_does_not_create_stays_on_the_panel(
    hass: HomeAssistant,
) -> None:
    """A row naming a circuit is converted only if it is one of the circuit's own entities.

    Its unique ID names the circuit, but no circuit entity is built with it, so
    the conversion has no label to give it and leaves it alone.
    """
    install = _install(hass, _tree())
    registry = er.async_get(hass)
    stranger = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"span_{install.serial}_{KITCHEN}_energy_imported",
        config_entry=install.entry,
        device_id=install.panel_device_id,
        has_entity_name=True,
        original_name="Kitchen Outlets Imported Energy",
    )
    before = _stored(stranger)

    async with _set_up(install):
        row = registry.async_get(stranger.entity_id)
        assert row is not None
        assert _stored(row) == before


async def test_another_integration_s_entity_on_the_panel_device_is_left_alone(
    hass: HomeAssistant,
) -> None:
    """Only this entry's rows are converted, whatever else is linked to the panel's device.

    A helper built on a circuit's sensor is linked to the sensor's device and
    belongs to its own config entry. This one even carries the sensor's unique
    id, under its own platform.
    """
    install = _install(hass, _tree())
    helper_entry = MockConfigEntry(domain="helper_platform", entry_id="entry-helper")
    helper_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    helper = registry.async_get_or_create(
        "sensor",
        "helper_platform",
        POWER.unique_id(install.serial, KITCHEN),
        config_entry=helper_entry,
        device_id=install.panel_device_id,
        original_name="Kitchen Outlets Daily Energy",
    )
    before = _stored(helper)

    report = install.convert()

    row = registry.async_get(helper.entity_id)
    assert row is not None
    assert _stored(row) == before
    assert helper.entity_id not in {converted.entity_id for converted in report.entities}
    assert install.row(KITCHEN, POWER).device_id == install.circuit_device(KITCHEN).id


async def test_a_charger_feed_circuits_sensors_are_left_for_the_charger_device(
    hass: HomeAssistant,
) -> None:
    """A feed circuit's sensors belong on the charger's device, so the conversion skips them.

    They are on the panel's device only where the install predates its charger.
    The sensor platform moves them, and converting them first would leave an
    empty device for the circuit behind.
    """
    install = _install(hass, _tree())
    garage = _area(hass, "Garage")
    registry = er.async_get(hass)
    feed = install.snapshot.circuits[FEED]
    sensor = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        POWER.unique_id(install.serial, FEED),
        config_entry=install.entry,
        device_id=install.panel_device_id,
        has_entity_name=True,
        original_name=f"{feed.name} Power",
    )
    registry.async_update_entity(sensor.entity_id, name=f"{feed.name} Charging", area_id=garage)

    report = install.convert()

    row = registry.async_get(sensor.entity_id)
    assert row is not None
    assert (row.device_id, row.name, row.area_id) == (
        install.panel_device_id,
        f"{feed.name} Charging",
        garage,
    )
    assert sensor.entity_id not in {converted.entity_id for converted in report.entities}
    assert install.find_circuit_device(FEED) is None


# ---------------------------------------------------------------------------
# What no platform builds this setup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [NAMES_MODE, NUMBERS_MODE], ids=["names", "numbers"])
@pytest.mark.parametrize(
    ("options", "unpublished", "left"),
    [
        pytest.param(NET_ENERGY_OFF, None, (NET,), id="net-energy-switched-off"),
        pytest.param({}, "meter/current", (CURRENT,), id="current-no-longer-published"),
        pytest.param({}, "breaker/rating", (BREAKER_RATING,), id="rating-no-longer-published"),
        pytest.param(CONTROLS_OFF, None, (BREAKER, PRIORITY), id="controls-switched-off"),
    ],
)
async def test_an_entity_no_platform_builds_stays_as_it_was(
    hass: HomeAssistant,
    mode: Mapping[str, object],
    options: Mapping[str, object],
    unpublished: str | None,
    left: tuple[_Kind, ...],
) -> None:
    """A row whose entity this setup does not build is left on the panel's device, untouched.

    Only a platform adding the entity gives its row the ID base that goes with
    its circuit's device. Moved without that, the row would keep the base
    2.3.0b4 stored, "Kitchen Outlets net energy", under a device now named
    "Kitchen Outlets", and "Recreate entity IDs" would offer it
    `..._kitchen_outlets_kitchen_outlets_net_energy`. Left where it is, it is
    offered the ID it has, as before the upgrade. The circuit's other entities
    convert as usual.
    """
    install = _install(hass, _tree(), options={**mode, **options})
    if unpublished is not None:
        install = replace(install, adapter=replay(_unpublished(_tree(), KITCHEN, unpublished)))
    registry = er.async_get(hass)
    before = {kind: install.row(KITCHEN, kind) for kind in left}
    for row in before.values():
        assert row.object_id_base is not None
        assert registry.async_regenerate_entity_id(row) == row.entity_id
    built = [kind for kind in install.kinds(KITCHEN) if kind not in left]
    assert built

    async with _set_up(install) as at_forward:
        for kind, row_before in before.items():
            row = install.row(KITCHEN, kind)
            assert row == row_before, row.entity_id
            assert row.device_id == install.panel_device_id
            assert at_forward[row.entity_id] == _stored(row_before)
            assert registry.async_regenerate_entity_id(row) == row.entity_id
        device = install.circuit_device(KITCHEN)
        for kind in built:
            assert install.row(KITCHEN, kind).device_id == device.id, kind.label


async def test_an_entity_left_on_the_panel_converts_once_a_platform_builds_it(
    hass: HomeAssistant,
) -> None:
    """The setup that builds the entity again converts its row, and the add gives it its base.

    The conversion runs at every setup on what is still on the panel's device,
    so the row waits there for as long as its entity is not built, and is
    still offered its own ID once it has moved.
    """
    install = _install(hass, _tree(), options={**NAMES_MODE, **NET_ENERGY_OFF})
    registry = er.async_get(hass)

    async with _set_up(install) as at_forward:
        assert install.row(KITCHEN, NET).device_id == install.panel_device_id

        await _change_options(install, NAMES_MODE)

        row = install.row(KITCHEN, NET)
        assert (row.device_id, row.original_name) == (
            install.circuit_device(KITCHEN).id,
            "Net Energy",
        )
        assert at_forward[row.entity_id] == _stored(row)
        assert row.object_id_base == "net energy"
        assert registry.async_regenerate_entity_id(row) == row.entity_id
        assert install.convert().empty


async def test_a_control_the_panel_stopped_offering_is_left_for_its_platform_to_withdraw(
    hass: HomeAssistant,
) -> None:
    """A breaker switch and a priority select the panel offers no longer are not converted.

    Their platforms build neither and remove both rows. The switch is still the
    circuit's switch while it is there, so its area decides the circuit's.
    """
    install = _install(hass, _tree())
    kitchen, garage = _area(hass, "Kitchen"), _area(hass, "Garage")
    registry = er.async_get(hass)
    controls = {
        kind: registry.async_get_or_create(
            kind.domain,
            DOMAIN,
            kind.unique_id(install.serial, UNSWITCHED),
            config_entry=install.entry,
            device_id=install.panel_device_id,
            has_entity_name=True,
            original_name=f"{WORKSHOP} {kind.label}",
            object_id_base=f"{WORKSHOP} {kind.id_words}",
        )
        for kind in (BREAKER, PRIORITY)
    }
    controls[BREAKER] = registry.async_update_entity(controls[BREAKER].entity_id, area_id=kitchen)
    install.update(UNSWITCHED, POWER, area_id=garage)

    async with _set_up(install) as at_forward:
        for row in controls.values():
            assert at_forward[row.entity_id] == _stored(row)
            assert registry.async_get(row.entity_id) is None
        assert install.circuit_device(UNSWITCHED).area_id == kitchen
        assert install.row(UNSWITCHED, POWER).area_id is None


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="everything-built"),
        pytest.param(NET_ENERGY_OFF, id="net-energy-switched-off"),
        pytest.param(CONTROLS_OFF, id="controls-switched-off"),
        pytest.param({**NET_ENERGY_OFF, **CONTROLS_OFF}, id="both-switched-off"),
    ],
)
async def test_recreate_offers_every_circuit_entity_the_id_it_has(
    hass: HomeAssistant, options: Mapping[str, object]
) -> None:
    """On a circuit-names install no circuit entity is offered another ID, converted or not.

    The guard against the conversion and the platforms disagreeing about what a
    setup builds: a row moved to a circuit's device that no platform then adds
    keeps the base that names the circuit, and is offered the circuit's name
    twice.
    """
    install = _install(hass, _tree(), options={**NAMES_MODE, **options})
    registry = er.async_get(hass)
    before = {row.unique_id: row.entity_id for row in install.all_rows()}

    async with _set_up(install):
        rows = install.all_rows()
        assert {row.unique_id: row.entity_id for row in rows} == before
        for row in rows:
            assert registry.async_regenerate_entity_id(row) == row.entity_id


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


async def test_switch_area_moves_to_the_device(hass: HomeAssistant) -> None:
    """The breaker switch's area becomes the circuit's, and its entities share it."""
    install = _install(hass, _tree())
    kitchen, garage = _area(hass, "Kitchen"), _area(hass, "Garage")
    install.update(KITCHEN, BREAKER, area_id=kitchen)
    install.update(KITCHEN, PRODUCED, area_id=kitchen)
    power = install.update(KITCHEN, POWER, area_id=garage)

    async with _set_up(install):
        assert install.circuit_device(KITCHEN).area_id == kitchen
        assert [row.area_id for row in install.rows(KITCHEN)] == [None] * len(install.rows(KITCHEN))
        lists = install.notice_lists()
        assert lists["area_changes"] == "- Kitchen Outlets Power: Garage → Kitchen"
        assert lists["circuits_left_in_areas"] == NONE_LISTED
        assert power.entity_id == install.row(KITCHEN, POWER).entity_id


async def test_power_area_moves_when_the_circuit_has_no_switch(hass: HomeAssistant) -> None:
    """With no breaker for an area-wide turn-off to reach, the power sensor's area decides.

    Wherever the power sensor comes in the registry: here it is registered
    again, which puts it after the circuit's other entities.
    """
    install = _install(hass, _tree())
    garage, basement = _area(hass, "Garage"), _area(hass, "Basement")
    registry = er.async_get(hass)
    removed = install.row(UNSWITCHED, POWER)
    registry.async_remove(removed.entity_id)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        removed.unique_id,
        config_entry=install.entry,
        device_id=install.panel_device_id,
        has_entity_name=True,
        original_name=removed.original_name,
    )
    assert install.row(UNSWITCHED, POWER).entity_id == removed.entity_id
    install.update(UNSWITCHED, PRODUCED, area_id=basement)
    install.update(UNSWITCHED, POWER, area_id=garage)

    async with _set_up(install):
        assert install.circuit_device(UNSWITCHED).area_id == garage
        assert {row.area_id for row in install.rows(UNSWITCHED)} == {None}
        assert install.notice_lists()["area_changes"] == (
            "- Workshop Produced Energy: Basement → Garage"
        )


async def test_the_first_other_area_moves_when_the_power_sensor_has_none(
    hass: HomeAssistant,
) -> None:
    """Failing the power sensor's, the first area in the registry's order is the circuit's."""
    install = _install(hass, _tree())
    garage, basement = _area(hass, "Garage"), _area(hass, "Basement")
    install.update(UNSWITCHED, CONSUMED, area_id=basement)
    install.update(UNSWITCHED, PRODUCED, area_id=garage)

    async with _set_up(install):
        assert install.circuit_device(UNSWITCHED).area_id == garage
        assert {row.area_id for row in install.rows(UNSWITCHED)} == {None}


async def test_nothing_moves_when_the_switch_has_no_area(hass: HomeAssistant) -> None:
    """An area moved up would reach the breaker switch, so every area stays where it is."""
    install = _install(hass, _tree())
    garage = _area(hass, "Garage")
    install.update(KITCHEN, POWER, area_id=garage)

    async with _set_up(install):
        assert install.circuit_device(KITCHEN).area_id is None
        assert install.row(KITCHEN, POWER).area_id == garage
        assert install.row(KITCHEN, BREAKER).area_id is None
        lists = install.notice_lists()
        assert lists["circuits_left_in_areas"] == "- Kitchen Outlets"
        assert lists["area_changes"] == NONE_LISTED


@pytest.mark.parametrize(
    ("switch_has_area", "moved"),
    [pytest.param(True, True, id="with-an-area"), pytest.param(False, False, id="without-one")],
)
async def test_a_withdrawn_switch_counts_as_the_circuit_switch(
    hass: HomeAssistant, switch_has_area: bool, moved: bool
) -> None:
    """A switch the panel stopped offering comes back with its area, so it still decides.

    The integration removes such a switch and Home Assistant keeps its row among
    the deleted ones, to restore when the panel offers control again. Read as
    no switch at all, the circuit would take its power sensor's area, and the
    restored switch would follow it there.
    """
    install = _install(hass, _tree())
    kitchen, garage = _area(hass, "Kitchen"), _area(hass, "Garage")
    registry = er.async_get(hass)
    switch = registry.async_get_or_create(
        "switch",
        DOMAIN,
        BREAKER.unique_id(install.serial, UNSWITCHED),
        config_entry=install.entry,
        device_id=install.panel_device_id,
        has_entity_name=True,
        original_name="Workshop Breaker",
    )
    if switch_has_area:
        registry.async_update_entity(switch.entity_id, area_id=kitchen)
    registry.async_remove(switch.entity_id)
    install.update(UNSWITCHED, POWER, area_id=garage)

    async with _set_up(install):
        device = install.circuit_device(UNSWITCHED)
        power = install.row(UNSWITCHED, POWER)
        if moved:
            assert (device.area_id, power.area_id) == (kitchen, None)
        else:
            assert (device.area_id, power.area_id) == (None, garage)


async def test_other_areas_are_cleared_renamed_entities_included(hass: HomeAssistant) -> None:
    """Once the device has the area, no entity keeps one of its own, whoever named it."""
    install = _install(hass, _tree())
    kitchen, garage, basement = (_area(hass, name) for name in ("Kitchen", "Garage", "Basement"))
    install.update(KITCHEN, BREAKER, area_id=kitchen)
    install.update(KITCHEN, POWER, name="Fridge Power", area_id=garage)
    install.update(KITCHEN, CONSUMED, area_id=basement)

    async with _set_up(install):
        assert {row.area_id for row in install.rows(KITCHEN)} == {None}
        power = install.row(KITCHEN, POWER)
        assert power.name == "Fridge Power"
        assert install.notice_lists()["area_changes"] == (
            "- Fridge Power: Garage → Kitchen\n- Kitchen Outlets Consumed Energy: Basement → Kitchen"
        )


async def test_the_trim_skips_an_entity_that_keeps_its_area(hass: HomeAssistant) -> None:
    """An entity with an area of its own displays without its device, so its name is whole.

    Its circuit's areas did not move, because its breaker switch has none. The
    circuit's other entities, which have no area of their own, are trimmed.
    """
    install = _install(hass, _tree())
    garage = _area(hass, "Garage")
    install.update(KITCHEN, POWER, name="Kitchen Outlets Power usage", area_id=garage)
    install.update(KITCHEN, CURRENT, name="Kitchen Outlets Amps")

    async with _set_up(install):
        power = install.row(KITCHEN, POWER)
        assert (power.name, power.area_id) == ("Kitchen Outlets Power usage", garage)
        assert install.row(KITCHEN, CURRENT).name == "Amps"


async def test_a_late_entity_joins_the_area_its_circuit_device_has(hass: HomeAssistant) -> None:
    """An entity converted after its circuit's others gives up its area for the device's.

    The device's area was settled when the circuit's first entities were
    converted. Read as the circuit's first area instead, the late entity's own
    would move the device, and every entity on it, to another room.
    """
    install = _install(hass, _tree(), options={**NAMES_MODE, **NET_ENERGY_OFF})
    garage, basement = _area(hass, "Garage"), _area(hass, "Basement")
    install.update(UNSWITCHED, POWER, area_id=garage)
    install.update(UNSWITCHED, NET, area_id=basement)

    async with _set_up(install):
        assert install.circuit_device(UNSWITCHED).area_id == garage
        assert install.row(UNSWITCHED, NET).area_id == basement
        assert install.notice_lists()["area_changes"] == NONE_LISTED

        await _change_options(install, NAMES_MODE)

        device = install.circuit_device(UNSWITCHED)
        net = install.row(UNSWITCHED, NET)
        assert device.area_id == garage
        assert (net.device_id, net.area_id) == (device.id, None)
        assert install.notice_lists() == {
            "panel": PANEL_NAME,
            "renamed": NONE_LISTED,
            "area_changes": "- Workshop Net Energy: Basement → Garage",
            "circuits_left_in_areas": NONE_LISTED,
        }


async def test_an_area_the_user_gave_a_circuit_device_outlasts_a_late_entity(
    hass: HomeAssistant,
) -> None:
    """A device the user has put in an area stays there, and a late entity joins it.

    The circuit's breaker switch had no area, so the conversion moved none and
    the user chose the device's since.
    """
    install = _install(hass, _tree(), options={**NAMES_MODE, **NET_ENERGY_OFF})
    kitchen, basement = _area(hass, "Kitchen"), _area(hass, "Basement")
    install.update(KITCHEN, NET, area_id=basement)

    async with _set_up(install):
        device = install.circuit_device(KITCHEN)
        assert device.area_id is None
        dr.async_get(hass).async_update_child_device(device.id, area_id=kitchen)

        await _change_options(install, NAMES_MODE)

        net = install.row(KITCHEN, NET)
        assert install.circuit_device(KITCHEN).area_id == kitchen
        assert (net.device_id, net.area_id) == (device.id, None)
        lists = install.notice_lists()
        assert lists["area_changes"] == "- Kitchen Outlets Net Energy: Basement → Kitchen"
        assert lists["circuits_left_in_areas"] == NONE_LISTED


@pytest.mark.parametrize(
    "device_moved", [pytest.param(False, id="device-where-the-switch-is"), True]
)
async def test_a_late_breaker_switch_stays_in_the_area_it_is_in(
    hass: HomeAssistant, device_moved: bool
) -> None:
    """A breaker switch converted after its circuit's others never changes area.

    Still on the panel's device, it decided the circuit's area. If the user has
    since put the circuit's device in another, the device stays there and the
    switch keeps its own: taking the device's would put the breaker in a room
    it was never in, and moving the device would undo the user's choice.
    """
    install = _install(hass, _tree(), options={**NAMES_MODE, **CONTROLS_OFF})
    kitchen, garage = _area(hass, "Kitchen"), _area(hass, "Garage")
    install.update(KITCHEN, BREAKER, area_id=kitchen)

    async with _set_up(install):
        device = install.circuit_device(KITCHEN)
        assert device.area_id == kitchen
        assert install.row(KITCHEN, BREAKER).device_id == install.panel_device_id
        if device_moved:
            dr.async_get(hass).async_update_child_device(device.id, area_id=garage)

        await _change_options(install, NAMES_MODE)

        switch, select = install.row(KITCHEN, BREAKER), install.row(KITCHEN, PRIORITY)
        assert (switch.device_id, select.device_id) == (device.id, device.id)
        if device_moved:
            assert install.circuit_device(KITCHEN).area_id == garage
            assert switch.area_id == kitchen
        else:
            assert install.circuit_device(KITCHEN).area_id == kitchen
            assert switch.area_id is None
        assert select.area_id is None
        assert install.notice_lists()["area_changes"] == NONE_LISTED


async def test_the_report_lists_a_late_breaker_switch_that_joins_its_device_s_area(
    hass: HomeAssistant,
) -> None:
    """A switch with no area, moved onto a device given one since, is in a room it was not in.

    The one move that puts a breaker in an area, so the report names the
    switch for a notice of its own. The circuit's other controls, moved with
    it, are not breakers.
    """
    install = _install(hass, _tree(), options={**NAMES_MODE, **CONTROLS_OFF})
    kitchen = _area(hass, "Kitchen")
    assert install.convert().breaker_switches_joining_areas == ()
    dr.async_get(hass).async_update_child_device(
        install.circuit_device(KITCHEN).id, area_id=kitchen
    )
    hass.config_entries.async_update_entry(install.entry, options=dict(NAMES_MODE))

    report = install.convert()

    switch = install.row(KITCHEN, BREAKER)
    assert (switch.device_id, switch.area_id) == (install.circuit_device(KITCHEN).id, None)
    assert report.breaker_switches_joining_areas == (switch.entity_id,)


async def test_the_report_lists_no_breaker_switch_converted_with_its_circuit(
    hass: HomeAssistant,
) -> None:
    """At the upgrade a switch with no area leaves its circuit's device without one."""
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, area_id=_area(hass, "Garage"))
    install.update(LIGHTS, BREAKER, area_id=_area(hass, "Hallway"))

    report = install.convert()

    assert install.circuit_device(KITCHEN).area_id is None
    assert install.circuit_device(LIGHTS).area_id is not None
    assert report.breaker_switches_joining_areas == ()


# ---------------------------------------------------------------------------
# Run again
# ---------------------------------------------------------------------------


async def test_second_setup_does_nothing(hass: HomeAssistant) -> None:
    """Once converted, a setup changes no row and adds nothing to the notice."""
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, area_id=_area(hass, "Garage"))
    install.update(KITCHEN, CURRENT, name="Kitchen Outlets Amps")

    async with _set_up(install):
        rows = [_stored(row) for row in install.all_rows()]
        lists = install.notice_lists()

        assert await hass.config_entries.async_reload(install.entry.entry_id) is True
        await hass.async_block_till_done()

        assert [_stored(row) for row in install.all_rows()] == rows
        assert install.notice_lists() == lists
        assert install.convert().empty


async def test_an_interrupted_conversion_finishes_next_setup(hass: HomeAssistant) -> None:
    """Circuits still on the panel's device are converted, and those already moved are left.

    The first run stands in for a conversion cut short: it converts only some
    of the circuits. The setup after it finishes the rest and reports on those.
    """
    install = _install(hass, _tree())
    garage = _area(hass, "Garage")
    install.update(KITCHEN, POWER, area_id=garage)
    install.update(LIGHTS, POWER, area_id=garage)
    snapshot = install.snapshot
    lights_name = snapshot.circuits[LIGHTS].name

    first = install.convert(replace(snapshot, circuits={LIGHTS: snapshot.circuits[LIGHTS]}))

    assert [converted.entity_id for converted in first.entities] == [
        row.entity_id for row in install.rows(LIGHTS)
    ]
    moved = [_stored(row) for row in install.rows(LIGHTS)]
    assert install.row(KITCHEN, POWER).device_id == install.panel_device_id

    async with _set_up(install):
        assert [_stored(row) for row in install.rows(LIGHTS)] == moved
        for circuit_id in install.circuits:
            device = install.circuit_device(circuit_id)
            assert {row.device_id for row in install.rows(circuit_id)} == {device.id}
        lists = install.notice_lists()
        assert lists["renamed"] == "- Kitchen Outlets Power → Power"
        assert lists["circuits_left_in_areas"] == "- Kitchen Outlets"
        assert lights_name not in lists["renamed"]


async def test_two_same_named_circuits_keep_their_ids(hass: HomeAssistant) -> None:
    """Two circuits called "Lights" become two devices, and neither is offered a new ID."""
    tree = _named(capture(CAPTURE).tree(), {LIGHTS: "Lights", KITCHEN: "Lights"})
    install = _install(hass, tree)
    before = {row.unique_id: row.entity_id for row in install.all_rows()}
    assert install.row(LIGHTS, POWER).entity_id == "sensor.span_panel_lights_power"
    assert install.row(KITCHEN, POWER).entity_id == "sensor.span_panel_lights_power_2"

    async with _set_up(install) as at_forward:
        first, second = install.circuit_device(LIGHTS), install.circuit_device(KITCHEN)
        assert first.id != second.id
        assert (first.name, second.name) == ("Lights", "Lights")
        for circuit_id, device in ((LIGHTS, first), (KITCHEN, second)):
            for row in install.rows(circuit_id):
                assert row.device_id == device.id
                assert at_forward[row.entity_id].device_id == device.id
        assert {row.unique_id: row.entity_id for row in install.all_rows()} == before
        registry = er.async_get(hass)
        for row in [*install.rows(LIGHTS), *install.rows(KITCHEN)]:
            assert registry.async_regenerate_entity_id(row) == row.entity_id


def _two_panels(hass: HomeAssistant) -> tuple[_Install, _Install, str]:
    """Seed two panels that each have a "Kitchen Outlets" whose power sensor has its own area.

    Returns the two installs and the second panel's "Kitchen Outlets" circuit.
    """
    first = _install(hass, _tree())
    second_tree = capture(SECOND_PANEL_CAPTURE).tree()
    other_kitchen = next(
        circuit_id
        for circuit_id, circuit in replay(second_tree).build_snapshot().circuits.items()
        if circuit_has_a_breaker_switch(circuit)
    )
    second_tree[other_kitchen]["info/name"] = KITCHEN_OUTLETS
    second = _install(
        hass, second_tree, entry_id="entry-conversion-2", panel_name=SECOND_PANEL_NAME
    )
    garage = _area(hass, "Garage")
    first.update(KITCHEN, POWER, area_id=garage)
    second.update(other_kitchen, POWER, area_id=garage)
    return first, second, other_kitchen


async def test_two_panels_convert_independently(hass: HomeAssistant) -> None:
    """Each panel's circuits get devices under that panel, and each panel its own notice."""
    first, second, other_kitchen = _two_panels(hass)

    async with _set_up(first, second):
        mine, theirs = first.circuit_device(KITCHEN), second.circuit_device(other_kitchen)
        assert mine.id != theirs.id
        assert (mine.name, theirs.name) == (KITCHEN_OUTLETS, KITCHEN_OUTLETS)
        assert mine.parent_device_id == first.panel_device_id
        assert theirs.parent_device_id == second.panel_device_id
        assert {row.device_id for row in first.rows(KITCHEN)} == {mine.id}
        assert {row.device_id for row in second.rows(other_kitchen)} == {theirs.id}
        for install, panel_name in ((first, PANEL_NAME), (second, SECOND_PANEL_NAME)):
            assert install.notice_lists() == {
                "panel": panel_name,
                "renamed": "- Kitchen Outlets Power → Power",
                "area_changes": NONE_LISTED,
                "circuits_left_in_areas": "- Kitchen Outlets",
            }


async def test_one_panel_s_conversion_leaves_another_panel_s_entities_alone(
    hass: HomeAssistant,
) -> None:
    """A conversion reads only its own entry's rows, whatever another panel calls its circuits."""
    first, second, _ = _two_panels(hass)
    untouched = [_stored(row) for row in second.all_rows()]

    report = first.convert()

    assert not report.empty
    assert [_stored(row) for row in second.all_rows()] == untouched
    assert dr.async_entries_for_parent_device(dr.async_get(hass), second.panel_device_id) == []


# ---------------------------------------------------------------------------
# The notice
# ---------------------------------------------------------------------------


async def test_the_notice_lists_only_changed_names(hass: HomeAssistant) -> None:
    """One line for the one name that changed, in a notice the user dismisses once read.

    The power sensor keeps an area of its own, because its circuit's breaker
    switch has none. An entity with its own area displays without its device,
    so its name was "Kitchen Outlets Power" and is now "Power". Every other
    name displays as it did and is not listed.
    """
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, area_id=_area(hass, "Garage"))

    async with _set_up(install):
        notice = install.notice()
        assert notice is not None
        assert notice.translation_key == "circuit_devices_converted"
        assert notice.is_persistent is True
        assert notice.is_fixable is False
        assert notice.severity is ir.IssueSeverity.WARNING
        assert install.notice_lists() == {
            "panel": PANEL_NAME,
            "renamed": "- Kitchen Outlets Power → Power",
            "area_changes": NONE_LISTED,
            "circuits_left_in_areas": "- Kitchen Outlets",
        }


async def test_the_notice_names_the_panel_as_the_user_named_its_device(
    hass: HomeAssistant,
) -> None:
    """The panel is named as Home Assistant shows it, which is the user's name for its device."""
    install = _install(hass, _tree())
    dr.async_get(hass).async_update_device(install.panel_device_id, name_by_user="Garage Panel")
    install.update(KITCHEN, POWER, area_id=_area(hass, "Garage"))

    async with _set_up(install):
        assert install.notice_lists()["panel"] == "Garage Panel"


async def test_the_notice_lists_fifty_names_then_counts_the_rest(hass: HomeAssistant) -> None:
    """A panel's worth of changed names would bury the notice, so the list stops at 50."""
    entry = add_panel_entry(hass, "entry-many", "sp3-242424-001")
    register_panel_device(hass, entry, "sp3-242424-001")
    registry = er.async_get(hass)
    converted = tuple(
        ConvertedEntity(
            registry.async_get_or_create(
                "sensor", DOMAIN, f"uid-{index}", config_entry=entry, original_name=f"New {index}"
            ).entity_id,
            f"Old {index}",
        )
        for index in range(53)
    )

    async_raise_conversion_notice(hass, entry, ConversionReport(converted, (), (), ()))

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "circuit_devices_converted_entry-many")
    assert issue is not None
    assert issue.translation_placeholders is not None
    lines = issue.translation_placeholders["renamed"].split("\n")
    assert lines[:2] == ["- Old 0 → New 0", "- Old 1 → New 1"]
    # A paragraph of its own: straight under the list it would join the last item.
    assert lines[49:] == ["- Old 49 → New 49", "", "and 3 more"]


async def test_the_notice_shows_an_area_it_cannot_name(hass: HomeAssistant) -> None:
    """An area deleted since is shown by its ID, and no area at all as a dash."""
    entry = add_panel_entry(hass, "entry-areas", "sp3-242424-001")
    register_panel_device(hass, entry, "sp3-242424-001")
    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        "sensor", DOMAIN, "uid-areas", config_entry=entry, original_name="Power"
    )
    report = ConversionReport(
        (ConvertedEntity(row.entity_id, "Power"),),
        (AreaChange(row.entity_id, "area_since_deleted", None),),
        (),
        (),
    )

    async_raise_conversion_notice(hass, entry, report)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "circuit_devices_converted_entry-areas")
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["area_changes"] == "- Power: area_since_deleted → —"


async def test_a_conversion_that_changed_nothing_raises_no_notice(hass: HomeAssistant) -> None:
    """A report with nothing in it, as every setup after the first makes, leaves no notice."""
    entry = add_panel_entry(hass, "entry-quiet", "sp3-242424-001")
    register_panel_device(hass, entry, "sp3-242424-001")

    report = ConversionReport((), (), (), ())
    async_raise_conversion_notice(hass, entry, report)

    assert report.empty
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "circuit_devices_converted_entry-quiet") is None
    )


def test_a_report_with_an_entity_in_it_is_not_empty() -> None:
    assert not ConversionReport((ConvertedEntity("sensor.a", "A"),), (), (), ()).empty


async def test_the_notice_is_cleared_on_removal(hass: HomeAssistant) -> None:
    """Home Assistant deletes no issue with its entry, so removing the panel clears the notice."""
    install = _install(hass, _tree())
    install.update(KITCHEN, POWER, area_id=_area(hass, "Garage"))

    async with _set_up(install):
        assert install.notice() is not None

    assert await hass.config_entries.async_remove(install.entry.entry_id)
    await hass.async_block_till_done()

    assert install.notice() is None


async def test_clearing_one_panel_s_notice_leaves_another_s(hass: HomeAssistant) -> None:
    """The notice's ID carries its entry's, so one panel's removal cannot take another's."""
    first = add_panel_entry(hass, "entry-first", "sp3-242424-001")
    second = add_panel_entry(hass, "entry-second", "sp3-242424-002")
    registry = er.async_get(hass)
    for entry in (first, second):
        row = registry.async_get_or_create(
            "sensor", DOMAIN, f"uid-{entry.entry_id}", config_entry=entry, original_name="New"
        )
        async_raise_conversion_notice(
            hass, entry, ConversionReport((ConvertedEntity(row.entity_id, "Old"),), (), (), ())
        )

    async_clear_conversion_notice(hass, first.entry_id)

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "circuit_devices_converted_entry-first") is None
    assert issues.async_get_issue(DOMAIN, "circuit_devices_converted_entry-second") is not None
