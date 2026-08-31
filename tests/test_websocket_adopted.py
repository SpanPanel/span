"""The adopted commands: list reports every curatable row, curate stores one.

Three things carry the list surface and each fails loudly here if it stops
holding: a row's key is the one the curate command will be handed back, the
allowed choices are computed from the wire rather than offered blind, and a
stored record that no longer fits its declaration is *shown* rather than silently
sanitised -- the editor is where a user finds out their assertion went stale.

Curate is tested against the same fixtures for the reason that matters most about
it: the keys it accepts and the choices it admits are the ones list offered, and
a second derivation of either would let the editor save something the entities
never read. Its refusals are asserted by code, its side effects are asserted to
be exactly three -- the store, the reload, the reply -- and the registry is
asserted to be untouched.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

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
from custom_components.span_panel.curation import (
    CurationOverlay,
    CurationRecord,
    async_load_curation,
)
from custom_components.span_panel.extension import extension_curation_key, extension_unique_id
from custom_components.span_panel.runtime import loaded_runtime_data
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

# Two wire addresses that flatten to one adopted unique_id -- the collision
# `adopted_unique_id` documents as permanent, spelled out. `battery-2/...` sorts
# first because `-` precedes `/`, which is what makes it the claim.
FLATTENS_FIRST = AdoptedProperty(
    node_id="battery-2",
    property_id="cell-temperature",
    datatype="float",
    unit="°C",
    value="31.4",
)
FLATTENS_SECOND = AdoptedProperty(
    node_id="battery",
    property_id="2-cell-temperature",
    datatype="float",
    unit="°C",
    value="31.5",
)
FLATTENS_SECOND_AS_SWITCH = AdoptedProperty(
    node_id="battery",
    property_id="2-cell-temperature",
    datatype="boolean",
    settable=True,
    value="true",
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


async def _curate(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_id: str,
    key: str,
    record: dict[str, Any],
    *,
    scheduled_reload: MagicMock | None = None,
) -> dict[str, Any]:
    """Send one adopted/curate request over a real websocket and return the reply.

    The reload a successful save schedules is patched out in every call. Letting
    it run would set the integration up for real against a panel no test has, and
    what the handler owes is that it *asked* for one -- which the `scheduled_reload`
    a test passes in is how that is asserted.
    """
    async_register_commands(hass)
    client = await hass_ws_client(hass)
    with patch.object(
        hass.config_entries,
        "async_schedule_reload",
        MagicMock() if scheduled_reload is None else scheduled_reload,
    ):
        await client.send_json_auto_id(
            {
                "type": "span_panel/adopted/curate",
                "device_id": device_id,
                "key": key,
                "record": record,
            }
        )
        reply: dict[str, Any] = await client.receive_json()
    return reply


async def _reload_overlay(hass: HomeAssistant) -> None:
    """Re-resolve the entry's overlay from disk, standing in for the patched-out reload.

    A save writes the store and schedules the reload; the overlay every read goes
    through is resolved once per setup and never re-reads the disk. Doing that one
    step by hand is what keeps a follow-up assertion about what reached the store
    rather than about what a handler happened to leave in memory.
    """
    entry = hass.config_entries.async_get_entry(ENTRY_ID)
    assert entry is not None
    runtime_data = loaded_runtime_data(entry)
    assert runtime_data is not None
    runtime_data.curation = await async_load_curation(hass, entry)


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


async def test_one_declaration_claims_a_flattened_id_and_the_other_is_not_listed(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The row that cannot become an entity is not offered as one.

    `adopted_unique_id` is deliberately non-injective, so two wire addresses can
    flatten onto one id; `adoption._create` gives it to the lexically first path
    and skips the other. Listing both would be worse than cosmetic: the editor
    resolves `entity_id` by (platform, unique_id), so the skipped row would
    report the *winner's* entity while carrying its own curation key -- a record
    the user saves against an entity that will never read it, beside a live
    entity_id saying it will.

    The declarations are handed over in the other order, so what decides is the
    sort rather than the order the publisher happened to emit.
    """
    unique_id = adopted_unique_id(ADOPTED_IDENTIFIER, FLATTENS_FIRST)
    assert unique_id == adopted_unique_id(ADOPTED_IDENTIFIER, FLATTENS_SECOND)
    panel = _setup(
        hass,
        _snapshot(
            devices=(replace(GENERATOR, properties=(FLATTENS_SECOND, FLATTENS_FIRST)),),
            rows=(),
        ),
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        suggested_object_id="backup_generator_battery_2_cell_temperature",
    )

    reply = await _list(hass, hass_ws_client, panel.id)
    rows = _group(reply, "Backup Generator")["rows"]

    assert [row["key"] for row in rows] == [
        adopted_curation_key(ADOPTED_IDENTIFIER, FLATTENS_FIRST)
    ]
    assert rows[0]["path"] == "battery-2/cell-temperature"
    assert rows[0]["entity_id"] == "sensor.backup_generator_battery_2_cell_temperature"


async def test_one_id_on_two_platforms_is_two_entities_and_two_rows(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The claim is per platform, because the registry's own uniqueness is.

    An entity is unique by (domain, integration, unique_id), so the same id under
    `sensor` and under `switch` is two entities rather than a collision --
    `adoption._create` claims per platform for exactly that reason. A claim
    scoped to the id alone would drop a row that really does become an entity.
    """
    panel = _setup(
        hass,
        _snapshot(
            devices=(replace(GENERATOR, properties=(FLATTENS_FIRST, FLATTENS_SECOND_AS_SWITCH)),),
            rows=(),
        ),
    )

    reply = await _list(hass, hass_ws_client, panel.id)
    rows = _group(reply, "Backup Generator")["rows"]

    assert [row["platform"] for row in rows] == ["sensor", "switch"]
    assert [row["key"] for row in rows] == [
        adopted_curation_key(ADOPTED_IDENTIFIER, FLATTENS_FIRST),
        adopted_curation_key(ADOPTED_IDENTIFIER, FLATTENS_SECOND_AS_SWITCH),
    ]


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


# --- curate: what a save leaves behind --------------------------------------


async def test_a_saved_record_is_stored_under_the_key_the_list_offered(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The key round-trips: what list offered is what curate resolves and the store holds.

    The whole editor rests on the two commands deriving the same rows, so the
    assertion is deliberately end-to-end -- save through the websocket, re-read
    the overlay off disk, and find the record on the row list reports it against.
    """
    panel = _setup(hass)

    reply = await _curate(
        hass,
        hass_ws_client,
        panel.id,
        POWER_KEY,
        {"state_class": "measurement", "device_class": "power", "entity_category": "none"},
    )

    assert reply["success"] is True
    assert reply["result"] == {
        "record": {
            "state_class": "measurement",
            "device_class": "power",
            "entity_category": "none",
        },
        "warnings": [],
    }

    await _reload_overlay(hass)
    listed = await _list(hass, hass_ws_client, panel.id)
    assert _row(_group(listed, "Backup Generator"), POWER_KEY)["curation"] == {
        "state_class": "measurement",
        "device_class": "power",
        "entity_category": "none",
    }


async def test_a_vendor_row_on_a_modelled_device_is_curated_the_same_way(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Both halves of adoption are one command, because they are one row derivation.

    An extension property hangs off a device this integration models and is keyed
    by subject rather than by an adopted identifier, so a lookup built from only
    the adopted half would refuse a row the editor is showing.
    """
    panel = _setup(hass)
    assert CELL_TEMPERATURE_KEY is not None

    reply = await _curate(
        hass, hass_ws_client, panel.id, CELL_TEMPERATURE_KEY, {"device_class": "temperature"}
    )

    assert reply["result"] == {"record": {"device_class": "temperature"}, "warnings": []}

    await _reload_overlay(hass)
    listed = await _list(hass, hass_ws_client, panel.id)
    assert _row(_group(listed, "Span Panel Battery"), CELL_TEMPERATURE_KEY)["curation"] == {
        "device_class": "temperature"
    }


async def test_a_save_schedules_exactly_one_reload(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Curated metadata reaches an entity by rebuilding it, so a save has to ask for that.

    An entity description is fixed at construction, so the record the user just
    stored does not reach the entity until the entry is set up again. One reload
    per save: the handler neither skips it nor asks twice for one write.
    """
    panel = _setup(hass)
    scheduled = MagicMock()

    reply = await _curate(
        hass,
        hass_ws_client,
        panel.id,
        POWER_KEY,
        {"device_class": "power"},
        scheduled_reload=scheduled,
    )

    assert reply["success"] is True
    scheduled.assert_called_once_with(ENTRY_ID)


async def test_a_refused_record_schedules_no_reload(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Nothing was written, so there is nothing for a reload to pick up."""
    panel = _setup(hass)
    scheduled = MagicMock()

    reply = await _curate(
        hass,
        hass_ws_client,
        panel.id,
        POWER_KEY,
        {"device_class": "temperature"},
        scheduled_reload=scheduled,
    )

    assert reply["error"]["code"] == "incompatible_device_class"
    scheduled.assert_not_called()


async def test_a_save_writes_no_registry_state(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The boundary this module is built on, asserted rather than described.

    A device class and a promotion out of diagnostics both *look* like registry
    acts, and writing them there is how this command would quietly become a
    second, weaker version of Core's own entity-registry update -- one with no
    undo and no user override. The registry hands back the very object it holds,
    and any update replaces that object, so identity is the exact assertion.
    """
    panel = _setup(hass)
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        adopted_unique_id(ADOPTED_IDENTIFIER, POWER),
        suggested_object_id="backup_generator_active_power",
    )
    before = registry.async_get(entity.entity_id)

    reply = await _curate(
        hass,
        hass_ws_client,
        panel.id,
        POWER_KEY,
        {"state_class": "measurement", "device_class": "power", "entity_category": "none"},
    )

    assert reply["success"] is True
    assert registry.async_get(entity.entity_id) is before


# --- curate: the warnings ----------------------------------------------------


async def test_clearing_a_record_that_carried_a_state_class_warns_the_statistics_go(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Statistics are compiled off the state class, so clearing one stops them.

    Advisory rather than a refusal -- the save has already happened and the user
    asked for it -- but it is a consequence the record does not show on its face,
    and core will raise its own `state_class_removed` repair against the
    statistics already collected, so the reply says so first.
    """
    panel = _setup(hass)
    await _curate(
        hass, hass_ws_client, panel.id, POWER_KEY, {"state_class": "total", "device_class": "power"}
    )
    await _reload_overlay(hass)

    reply = await _curate(hass, hass_ws_client, panel.id, POWER_KEY, {})

    assert reply["result"] == {"record": {}, "warnings": ["statistics_removed"]}

    await _reload_overlay(hass)
    listed = await _list(hass, hass_ws_client, panel.id)
    assert _row(_group(listed, "Backup Generator"), POWER_KEY)["curation"] == {}


async def test_clearing_a_record_that_never_carried_one_warns_nothing(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """No state class was in force, so no statistics existed to lose."""
    panel = _setup(hass)
    await _curate(hass, hass_ws_client, panel.id, POWER_KEY, {"device_class": "power"})
    await _reload_overlay(hass)

    reply = await _curate(hass, hass_ws_client, panel.id, POWER_KEY, {})

    assert reply["result"] == {"record": {}, "warnings": []}


async def test_clearing_a_row_nobody_curated_is_accepted_and_warns_nothing(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Clearing what was never set is a no-op rather than a refusal.

    An editor that opens on an uncurated row and saves it unchanged is the
    ordinary case, and it must not be told it did something wrong.
    """
    panel = _setup(hass)

    reply = await _curate(hass, hass_ws_client, panel.id, LABEL_KEY, {})

    assert reply["result"] == {"record": {}, "warnings": []}


async def test_asserting_total_increasing_is_warned_about(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The one state class that reinterprets the reading rather than describing it.

    `total_increasing` has the recorder read a drop of more than a tenth as a
    meter reset and start a new cycle, so asserting it on a reading that
    legitimately falls manufactures consumption. Saved as asked, and flagged.
    """
    panel = _setup(hass)

    reply = await _curate(
        hass, hass_ws_client, panel.id, POWER_KEY, {"state_class": "total_increasing"}
    )

    assert reply["result"] == {
        "record": {"state_class": "total_increasing"},
        "warnings": ["total_increasing"],
    }


# --- curate: refusals --------------------------------------------------------


async def test_a_key_the_panel_does_not_publish_is_refused(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A well-formed key for a row that does not exist earns its own code.

    The store would hold anything -- its keys are wire addresses, not registry
    ids -- so nothing but this check stops a typo becoming a record no entity
    will ever read and no list will ever show.
    """
    panel = _setup(hass)

    reply = await _curate(
        hass,
        hass_ws_client,
        panel.id,
        adopted_curation_key(ADOPTED_IDENTIFIER, FLATTENS_FIRST),
        {"device_class": "temperature"},
    )

    assert reply["success"] is False
    assert reply["error"]["code"] == "unknown_key"


async def test_the_row_the_list_skipped_cannot_be_curated(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """One row derivation, so a row list declines to offer is one curate declines to save.

    The loser of a flattened-id collision never becomes an entity. A record saved
    against it would sit on disk unread forever, which is worse than a refusal
    because the editor would report it back as an assertion in force.
    """
    panel = _setup(
        hass,
        _snapshot(
            devices=(replace(GENERATOR, properties=(FLATTENS_SECOND, FLATTENS_FIRST)),), rows=()
        ),
    )

    reply = await _curate(
        hass,
        hass_ws_client,
        panel.id,
        adopted_curation_key(ADOPTED_IDENTIFIER, FLATTENS_SECOND),
        {"device_class": "temperature"},
    )

    assert reply["error"]["code"] == "unknown_key"


async def test_a_cross_field_refusal_surfaces_the_validators_own_code(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """A device class the declared unit does not admit is refused, by name.

    Membership in the enum is all the schema can know; whether `temperature`
    admits watts is a fact about this row, so `curation` decides it and its code
    reaches the editor unchanged -- the card renders the refusal it can explain,
    not a generic one.
    """
    panel = _setup(hass)

    reply = await _curate(
        hass, hass_ws_client, panel.id, POWER_KEY, {"device_class": "temperature"}
    )

    assert reply["success"] is False
    assert reply["error"]["code"] == "incompatible_device_class"


async def test_the_schemas_alphabet_is_wider_than_any_one_row_admits(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """What the schema cannot know without the row is what the handler answers for.

    The schema takes both platforms' device classes because it has no row to
    narrow them with, and every state class because whether a row is a numeric
    sensor is a fact about the wire. So a binary-only class on a sensor row and a
    state class on a string row both pass the schema and are refused here, each
    by its own code.
    """
    panel = _setup(hass)

    binary_only = await _curate(
        hass, hass_ws_client, panel.id, POWER_KEY, {"device_class": "motion"}
    )
    off_a_string = await _curate(
        hass, hass_ws_client, panel.id, LABEL_KEY, {"state_class": "measurement"}
    )

    assert binary_only["error"]["code"] == "invalid_device_class"
    assert off_a_string["error"]["code"] == "invalid_state_class"


async def test_a_control_row_accepts_prominence_only(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """What list reports as an empty choice list, curate refuses -- the same answer twice.

    A settable numeric surfaces as a control, which carries neither class. The
    editor is told so by the empty `allowed_*` lists; a card that ignored them
    must still be refused rather than storing metadata the entity cannot hold.
    """
    panel = _setup(hass)

    refused = await _curate(hass, hass_ws_client, panel.id, SETPOINT_KEY, {"device_class": "power"})
    accepted = await _curate(
        hass, hass_ws_client, panel.id, SETPOINT_KEY, {"entity_category": "none"}
    )

    assert refused["error"]["code"] == "invalid_field_for_platform"
    assert accepted["result"] == {"record": {"entity_category": "none"}, "warnings": []}


async def test_the_schema_refuses_what_it_can_decide_without_the_row(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Enum membership, the one storable category, and unknown fields never reach the handler.

    Everything statically expressible is constrained at the schema, so the
    handler answers only for what needs the row's declaration. All three come
    back as the websocket layer's own `invalid_format`.
    """
    panel = _setup(hass)

    for record in (
        {"state_class": "invented"},
        {"device_class": "invented"},
        {"entity_category": "diagnostic"},
        {"nonsense": "1"},
    ):
        reply = await _curate(hass, hass_ws_client, panel.id, POWER_KEY, record)
        assert reply["success"] is False, record
        assert reply["error"]["code"] == "invalid_format", record


async def test_the_schema_refuses_a_key_no_curation_scheme_mints(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Keys are wire addresses and become storage keys, so their shape is constrained.

    Both minting schemes produce identifier and path segments only. A key outside
    that alphabet, or an unbounded one, is refused before the handler sees it.
    """
    panel = _setup(hass)

    for key in ("has spaces", "has\\backslash", "x" * 257, ""):
        reply = await _curate(hass, hass_ws_client, panel.id, key, {})
        assert reply["success"] is False, key
        assert reply["error"]["code"] == "invalid_format", key


async def test_curate_requires_admin(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_admin_user: MockUser,
) -> None:
    """A write is an admin act, refused before any device or key is resolved."""
    hass_admin_user.groups = []
    _setup(hass)

    reply = await _curate(hass, hass_ws_client, "any-device-id", POWER_KEY, {})

    assert reply["success"] is False
    assert reply["error"]["code"] == "unauthorized"


async def test_curate_takes_the_panel_handle(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """The same handle and the same refusals as list, because they are one resolution.

    A consumer that learned list's codes must not meet a second set on the
    command it calls next, so curate resolves the panel through the same helper.
    """
    _setup(hass)
    bess = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, BESS_IDENTIFIER)})
    assert bess is not None

    reply = await _curate(hass, hass_ws_client, bess.id, POWER_KEY, {})

    assert reply["error"]["code"] == "not_panel_device"
