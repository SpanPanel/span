"""The adopted/list command reports every curatable row, grouped by the device it renders on.

Three things carry this surface and each fails loudly here if it stops holding:
a row's key is the one the curate command will be handed back, the allowed
choices are computed from the wire rather than offered blind, and a stored record
that no longer fits its declaration is *shown* rather than silently sanitised --
the editor is where a user finds out their assertion went stale.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
from pytest_homeassistant_custom_component.typing import WebSocketGenerator
from span_panel_api import AdoptedDevice, AdoptedProperty, ExtensionProperty, ExtensionSubject

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.adoption import (
    adopted_curation_key,
    adopted_identifier,
    adopted_unique_id,
    async_register_adopted_devices,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay, CurationRecord
from custom_components.span_panel.extension import extension_curation_key, extension_unique_id
from custom_components.span_panel.util import SUB_DEVICE_BESS
from custom_components.span_panel.websocket import async_register_commands

from .factories import SpanPanelSnapshotFactory

if TYPE_CHECKING:
    from span_panel_api import SpanPanelSnapshot

PANEL_SERIAL = "sp3-242424-001"
ENTRY_ID = "span_entry"
ADOPTED_ANCHOR = "generator-1"
ADOPTED_IDENTIFIER = adopted_identifier(PANEL_SERIAL, ADOPTED_ANCHOR)
BESS_IDENTIFIER = f"{PANEL_SERIAL}_{SUB_DEVICE_BESS}"

# One reading, one control and one string, so the three shapes the allowed-choice
# helpers answer differently are all on one device.
POWER = AdoptedProperty(
    node_id="meter", property_id="active-power", datatype="float", unit="W", value="2400"
)
SETPOINT = AdoptedProperty(
    node_id="control",
    property_id="power-setpoint",
    datatype="float",
    unit="W",
    format="0:5000:100",
    settable=True,
    value="1000",
)
LABEL = AdoptedProperty(
    node_id="status", property_id="mode-label", datatype="string", value="idle"
)

GENERATOR = AdoptedDevice(
    device_id=ADOPTED_ANCHOR,
    device_type="energy.ebus.device.generator",
    name="Backup Generator",
    model="GEN-9000",
    properties=(POWER, SETPOINT, LABEL),
)

CELL_TEMPERATURE = ExtensionProperty(
    subject=ExtensionSubject(kind="battery"),
    node_id="battery-2",
    property_id="cell-temperature",
    datatype="float",
    unit="°C",
    value="31.4",
)

POWER_KEY = adopted_curation_key(ADOPTED_IDENTIFIER, POWER)
LABEL_KEY = adopted_curation_key(ADOPTED_IDENTIFIER, LABEL)
SETPOINT_KEY = adopted_curation_key(ADOPTED_IDENTIFIER, SETPOINT)
CELL_TEMPERATURE_KEY = extension_curation_key(CELL_TEMPERATURE.subject, CELL_TEMPERATURE.path)


def _snapshot(
    *,
    devices: tuple[AdoptedDevice, ...] = (GENERATOR,),
    rows: tuple[ExtensionProperty, ...] = (CELL_TEMPERATURE,),
) -> SpanPanelSnapshot:
    """Return a curated snapshot carrying the given adopted devices and extension rows."""
    return replace(
        SpanPanelSnapshotFactory.create_complete(serial_number=PANEL_SERIAL),
        adopted_devices=devices,
        extension_properties=rows,
    )


def _register_cards(hass: HomeAssistant) -> dr.DeviceEntry:
    """Register the panel and its BESS card, as setup leaves them, and return the panel."""
    registry = dr.async_get(hass)
    panel = registry.async_get_or_create(
        config_entry_id=ENTRY_ID,
        identifiers={(DOMAIN, PANEL_SERIAL)},
        name="Span Panel",
    )
    registry.async_get_or_create(
        config_entry_id=ENTRY_ID,
        identifiers={(DOMAIN, BESS_IDENTIFIER)},
        name="Span Panel Battery",
        via_device_id=panel.id,
    )
    return panel


def _setup(
    hass: HomeAssistant,
    snapshot: SpanPanelSnapshot | None = None,
    *,
    overlay: CurationOverlay | None = None,
    register_adopted: bool = True,
) -> dr.DeviceEntry:
    """Bring an entry up the way setup does, and return the panel's device entry.

    In setup's own order: the panel card, then the entry's runtime data, then the
    adopted devices -- `async_register_adopted_devices` is called rather than
    hand-registering, so the identifiers under test are the ones production mints.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id=ENTRY_ID, unique_id=PANEL_SERIAL)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    panel = _register_cards(hass)
    coordinator = MagicMock()
    coordinator.data = snapshot if snapshot is not None else _snapshot()
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel.id,
        curation=overlay if overlay is not None else CurationOverlay.empty(),
    )
    if register_adopted:
        async_register_adopted_devices(
            hass, ENTRY_ID, coordinator.data, panel_device_id=panel.id
        )
    return panel


async def _list(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, device_id: str
) -> dict[str, Any]:
    """Send one adopted/list request over a real websocket and return the reply."""
    async_register_commands(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "span_panel/adopted/list", "device_id": device_id})
    reply: dict[str, Any] = await client.receive_json()
    return reply


def _group(reply: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the one device group with this name, failing the test if it is absent."""
    matched = [group for group in reply["result"]["devices"] if group["name"] == name]
    assert len(matched) == 1, f"expected exactly one {name!r} group in {reply['result']}"
    return matched[0]


def _row(group: dict[str, Any], key: str) -> dict[str, Any]:
    """Return the one row in this group carrying this curation key."""
    matched = [row for row in group["rows"] if row["key"] == key]
    assert len(matched) == 1, f"expected exactly one row keyed {key!r} in {group}"
    return matched[0]


# --- grouping ---------------------------------------------------------------


async def test_rows_are_grouped_by_the_device_they_render_on(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An adopted device and a curated card are two groups, each flagged for what it is.

    The flag is what the editor decides from: an adopted device is a card this
    integration minted from the wire, a curated one is a card the user already
    knows, and their rows are curated identically but presented differently.
    """
    panel = _setup(hass)

    reply = await _list(hass, hass_ws_client, panel.id)

    assert reply["success"] is True
    generator = _group(reply, "Backup Generator")
    battery = _group(reply, "Span Panel Battery")

    assert generator["adopted_device"] is True
    assert battery["adopted_device"] is False

    registry = dr.async_get(hass)
    adopted_card = registry.async_get_device(identifiers={(DOMAIN, ADOPTED_IDENTIFIER)})
    assert adopted_card is not None
    assert generator["device_id"] == adopted_card.id
    bess_card = registry.async_get_device(identifiers={(DOMAIN, BESS_IDENTIFIER)})
    assert bess_card is not None
    assert battery["device_id"] == bess_card.id

    assert [row["key"] for row in generator["rows"]] == [SETPOINT_KEY, POWER_KEY, LABEL_KEY]
    assert [row["key"] for row in battery["rows"]] == [CELL_TEMPERATURE_KEY]


async def test_a_row_carries_the_documented_keys(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The row record's key set is a contract with a card in another repository.

    Asserted exactly rather than by presence, exactly as the topology command's
    circuit record is: a deliberate change updates this list, an accidental one
    fails here rather than in a renderer no test in this repository can reach.
    """
    panel = _setup(hass)

    reply = await _list(hass, hass_ws_client, panel.id)

    assert set(_row(_group(reply, "Backup Generator"), POWER_KEY)) == {
        "key",
        "path",
        "platform",
        "entity_id",
        "datatype",
        "unit",
        "settable",
        "name",
        "curation",
        "allowed_device_classes",
        "allowed_state_classes",
        "stale_fields",
    }


async def test_a_row_reports_the_declaration_it_is_curated_against(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Path, platform, datatype, unit, settability and name, from the wire."""
    panel = _setup(hass)

    reply = await _list(hass, hass_ws_client, panel.id)
    generator = _group(reply, "Backup Generator")

    power = _row(generator, POWER_KEY)
    assert power["path"] == "meter/active-power"
    assert power["platform"] == "sensor"
    assert power["datatype"] == "float"
    assert power["unit"] == "W"
    assert power["settable"] is False
    assert power["name"] == "Active Power"

    setpoint = _row(generator, SETPOINT_KEY)
    assert setpoint["platform"] == "number"
    assert setpoint["settable"] is True

    temperature = _row(_group(reply, "Span Panel Battery"), CELL_TEMPERATURE_KEY)
    assert temperature["path"] == "battery-2/cell-temperature"
    assert temperature["platform"] == "sensor"
    assert temperature["unit"] == "°C"
    # Node-prefixed, exactly as the entity is: a vendor reading sits beside
    # curated ones on the same card and has to disambiguate itself.
    assert temperature["name"] == "Battery 2 Cell Temperature"


# --- the allowed choices ----------------------------------------------------


async def test_a_string_row_admits_no_state_class(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A state class needs a numeric sensor, so a string row offers none.

    Computed server-side so the card never renders an option the curate command
    would refuse.
    """
    panel = _setup(hass)

    reply = await _list(hass, hass_ws_client, panel.id)
    generator = _group(reply, "Backup Generator")

    assert _row(generator, LABEL_KEY)["allowed_state_classes"] == []
    assert _row(generator, POWER_KEY)["allowed_state_classes"] == [
        state_class.value for state_class in SensorStateClass
    ]


async def test_a_control_row_admits_neither_class(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A settable numeric surfaces as a control, and a control carries prominence only."""
    panel = _setup(hass)

    reply = await _list(hass, hass_ws_client, panel.id)
    setpoint = _row(_group(reply, "Backup Generator"), SETPOINT_KEY)

    assert setpoint["allowed_state_classes"] == []
    assert setpoint["allowed_device_classes"] == []


async def test_the_declared_unit_constrains_the_device_classes(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Core's own unit map decides, so a watt row offers power and never temperature."""
    panel = _setup(hass)

    reply = await _list(hass, hass_ws_client, panel.id)

    power = _row(_group(reply, "Backup Generator"), POWER_KEY)
    assert "power" in power["allowed_device_classes"]
    assert "temperature" not in power["allowed_device_classes"]

    temperature = _row(_group(reply, "Span Panel Battery"), CELL_TEMPERATURE_KEY)
    assert "temperature" in temperature["allowed_device_classes"]
    assert "power" not in temperature["allowed_device_classes"]


# --- what the store holds ---------------------------------------------------


async def test_a_stored_record_is_reported_as_stored(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The editor opens on what the user asserted, field for field."""
    panel = _setup(
        hass,
        overlay=CurationOverlay(
            {
                POWER_KEY: CurationRecord(
                    state_class=SensorStateClass.MEASUREMENT,
                    device_class="power",
                    promote=True,
                )
            }
        ),
    )

    reply = await _list(hass, hass_ws_client, panel.id)
    generator = _group(reply, "Backup Generator")

    assert _row(generator, POWER_KEY)["curation"] == {
        "state_class": "measurement",
        "device_class": "power",
        "entity_category": "none",
    }
    assert _row(generator, POWER_KEY)["stale_fields"] == []
    # A row nobody has curated carries an empty record rather than a null.
    assert _row(generator, LABEL_KEY)["curation"] == {}


async def test_a_record_the_wire_outgrew_is_shown_and_named_stale(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Shown as stored *and* marked, because the editor is where the user finds out.

    Entity construction reads the same record through `for_row`, which drops what
    no longer fits. The editor must not: a silently sanitised record would show
    the user an assertion they never made, with no way to see that theirs was
    dropped. So the stored fields are reported verbatim beside the names of the
    ones the current declaration refuses.
    """
    panel = _setup(
        hass,
        overlay=CurationOverlay(
            {LABEL_KEY: CurationRecord(state_class=SensorStateClass.TOTAL_INCREASING)}
        ),
    )

    reply = await _list(hass, hass_ws_client, panel.id)
    label = _row(_group(reply, "Backup Generator"), LABEL_KEY)

    assert label["curation"] == {"state_class": "total_increasing"}
    assert label["stale_fields"] == ["state_class"]


# --- the registry side ------------------------------------------------------


async def test_entity_id_is_the_registry_row_or_null(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A row not yet in the registry reports null rather than a guessed id.

    Which is an ordinary state for both halves: an adopted entity is created
    disabled and a vendor extension arrives on the setup after its card does, so
    a row the user can curate now may have no entity until the next reload.
    """
    panel = _setup(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        adopted_unique_id(ADOPTED_IDENTIFIER, POWER),
        suggested_object_id="backup_generator_active_power",
    )

    reply = await _list(hass, hass_ws_client, panel.id)
    generator = _group(reply, "Backup Generator")

    assert _row(generator, POWER_KEY)["entity_id"] == "sensor.backup_generator_active_power"
    assert _row(generator, LABEL_KEY)["entity_id"] is None
    battery = _group(reply, "Span Panel Battery")
    assert _row(battery, CELL_TEMPERATURE_KEY)["entity_id"] is None


async def test_the_platform_a_row_already_holds_decides_its_entity(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An extension row resolves its platform through the registry, never the datatype alone.

    The domain is baked into `entity_id`, so a row born a sensor stays one however
    the publisher relabels it -- and the list has to report the platform the
    entity actually has, or the editor offers choices for a row that is not there.
    """
    unique_id = extension_unique_id(
        PANEL_SERIAL,
        CELL_TEMPERATURE.subject,
        CELL_TEMPERATURE.node_id,
        CELL_TEMPERATURE.property_id,
    )
    assert unique_id is not None
    panel = _setup(
        hass,
        _snapshot(rows=(replace(CELL_TEMPERATURE, datatype="boolean", unit=None),)),
    )
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, unique_id, suggested_object_id="battery_2_cell_temperature"
    )

    reply = await _list(hass, hass_ws_client, panel.id)
    temperature = _row(_group(reply, "Span Panel Battery"), CELL_TEMPERATURE_KEY)

    assert temperature["platform"] == "sensor"
    assert temperature["entity_id"] == "sensor.battery_2_cell_temperature"


async def test_a_device_with_no_card_yet_is_grouped_under_a_null_id(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A device that arrived after setup has no card until the next reload, and still lists.

    Its rows are curatable now -- the store is keyed on the wire address, not on a
    registry id -- so hiding them would make the user wait a reload to assert
    something the store would happily hold.
    """
    panel = _setup(hass, register_adopted=False)

    reply = await _list(hass, hass_ws_client, panel.id)
    generator = _group(reply, "Backup Generator")

    assert generator["device_id"] is None
    assert generator["adopted_device"] is True
    assert [row["key"] for row in generator["rows"]] == [SETPOINT_KEY, POWER_KEY, LABEL_KEY]


async def test_an_extension_row_waits_for_the_card_it_belongs_on(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """No card, no group -- the same deferral the entity builders make.

    An extension property hangs off a device this integration models, so a
    subject whose card is not registered yet has nowhere to render and no name to
    group under. It appears at the next reload, exactly as its entity does.
    """
    panel = _setup(
        hass,
        _snapshot(rows=(replace(CELL_TEMPERATURE, subject=ExtensionSubject(kind="pv")),)),
    )

    reply = await _list(hass, hass_ws_client, panel.id)

    assert [group["name"] for group in reply["result"]["devices"]] == ["Backup Generator"]


# --- refusals ---------------------------------------------------------------


async def test_requires_admin(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_admin_user: MockUser,
) -> None:
    """Curation is an admin act, refused before any device is resolved."""
    hass_admin_user.groups = []
    async_register_commands(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "span_panel/adopted/list", "device_id": "any-device-id"}
    )

    reply = await client.receive_json()
    assert reply["success"] is False
    assert reply["error"]["code"] == "unauthorized"


async def test_device_not_found(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An id the registry does not hold is refused by that name."""
    _setup(hass)

    reply = await _list(hass, hass_ws_client, "nonexistent")

    assert reply["error"]["code"] == "device_not_found"


async def test_not_span_panel(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    """Another integration's device is refused before its entry is touched."""
    other = MockConfigEntry(domain="other_domain", data={}, entry_id="other_entry")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="other_entry",
        identifiers={("other_domain", "other_serial")},
    )

    reply = await _list(hass, hass_ws_client, device.id)

    assert reply["error"]["code"] == "not_span_panel"


async def test_a_span_identifier_no_span_entry_owns(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A device row that outlived its entry is refused rather than followed.

    The entry's domain is checked rather than assumed, because a device row may
    carry entries from more than one integration and the first is not necessarily
    ours. Topology answers `not_loaded` here; this command answers
    `not_span_panel`, because resolving the entry and checking its domain is one
    step and a device whose SPAN entry is gone is not a SPAN panel any more.
    """
    other = MockConfigEntry(domain="other_domain", data={}, entry_id="other_entry")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="other_entry",
        identifiers={(DOMAIN, PANEL_SERIAL)},
    )

    reply = await _list(hass, hass_ws_client, device.id)

    assert reply["error"]["code"] == "not_span_panel"


async def test_not_panel_device(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    """A sub-device id is refused: the panel is the handle for the whole entry.

    Sub-devices are exactly what this command *reports*, so passing one is the
    ordinary mistake, and it earns its own code rather than an empty list.
    """
    _setup(hass)
    bess = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, BESS_IDENTIFIER)})
    assert bess is not None

    reply = await _list(hass, hass_ws_client, bess.id)

    assert reply["error"]["code"] == "not_panel_device"


async def test_not_loaded(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    """An entry that is not set up has no overlay and no snapshot to report."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id=ENTRY_ID, unique_id=PANEL_SERIAL)
    entry.add_to_hass(hass)
    panel = _register_cards(hass)

    reply = await _list(hass, hass_ws_client, panel.id)

    assert reply["error"]["code"] == "not_loaded"


async def test_runtime_data_is_not_ours(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A LOADED entry whose runtime data is not ours answers not_loaded, not an attribute error.

    `loaded_runtime_data` is the one place that decides this, per AGENTS.md's
    runtime-data guard: core deletes the attribute on unload, and what is there
    is whatever put it there.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id=ENTRY_ID, unique_id=PANEL_SERIAL)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = object()
    panel = _register_cards(hass)

    reply = await _list(hass, hass_ws_client, panel.id)

    assert reply["error"]["code"] == "not_loaded"


async def test_no_data(hass: HomeAssistant, hass_ws_client: WebSocketGenerator) -> None:
    """A panel that has not answered yet has nothing to list."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id=ENTRY_ID, unique_id=PANEL_SERIAL)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    panel = _register_cards(hass)
    coordinator = MagicMock()
    coordinator.data = None
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=panel.id,
        curation=CurationOverlay.empty(),
    )

    reply = await _list(hass, hass_ws_client, panel.id)

    assert reply["error"]["code"] == "no_data"


async def test_a_panel_with_nothing_adopted_lists_nothing(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """An empty list, not a refusal: most panels publish neither kind of row."""
    panel = _setup(hass, _snapshot(devices=(), rows=()))

    reply = await _list(hass, hass_ws_client, panel.id)

    assert reply["success"] is True
    assert reply["result"] == {"devices": []}


async def test_device_id_is_required_by_the_schema(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Constrained at the schema rather than checked in the handler.

    A missing device id is refused by the websocket layer, so the handler never
    runs and never has to answer for a request shape voluptuous can reject.
    """
    async_register_commands(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "span_panel/adopted/list"})

    reply = await client.receive_json()
    assert reply["success"] is False
    assert reply["error"]["code"] == "invalid_format"
