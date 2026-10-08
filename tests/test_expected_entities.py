"""Each captured panel produces exactly the devices, entities and topology on file for it.

Every capture in `tests/fixtures/captures/` is replayed through the pinned
library and set up through the integration's real `async_setup_entry`: the real
coordinator, every platform the integration forwards, the real registries and
the real translations. The MQTT client is the one thing replaced, by a static
replay of the capture: no reconnect, offline, connection, schema-change or
streaming path runs, so availability and offline behaviour stay with their own
tests. Once set up, the coordinator's listeners are updated once, which is what
the first streamed snapshot does on a live panel, so every state is settled.

What that setup registers is compared, row for row, with
`tests/fixtures/expected_entities/<stem>.json`:

    {"devices":  {identifier: {name, manufacturer, model, hw_version,
                               sw_version, serial_number, via_device}},
     "entities": {unique_id: {platform, entity_id, original_name,
                              translation_key, device, device_class,
                              state_class, unit_of_measurement,
                              entity_category, disabled_by, hidden_by,
                              state}}}

and what `span_panel/panel_topology` answers for it with
`tests/fixtures/topology/<stem>.json`, which the card renders in its own suite.

**The files are the record, and changing one is the decision.** A change that
moves any of these -- an entity added, removed, renamed or re-homed, disabled or
hidden by default, given another class, unit or category, reading another state;
a device renamed or re-described; a topology field gained or lost -- fails here
until the file is regenerated in the same diff, where a reviewer reads it:

    pytest tests/test_expected_entities.py --update-capture-fixtures -k "<stem>]"

The closing bracket matches the end of a test id, so `-k "main32]"` selects that
one stem and not every stem that begins with it.

The recorded `state` is the point of "a value the panel did not publish reads
unknown, never 0": unknown, a measured 0 and a value are three different rows.
`None` is no state at all, which is what an entity disabled by default has.

The entity ids are a fresh installation's, as Home Assistant derives them from
the device and entity names; an existing installation keeps the ids its
registry already holds, whatever these say. A changed `entity_id` here is a
changed name a new user sees, and a changed `unique_id` is an entity every
existing user loses.

**Three variants of the r202639 capture are derived here rather than vendored**,
so they cannot drift from it:

- one without a battery, the shape of a panel that has none: the battery and
  everything beneath it go, and the panel publishes no backup forecast, which
  only a battery gives it, while still declaring the properties;
- one whose battery is declared but has not published its state of charge, its
  state of energy or its power yet, as at startup or while the battery is
  offline;
- one whose panel has not published its power flows, nor one drawing circuit its
  power: the replay that holds "unknown, never 0" to a row, because without it
  no enabled numeric reading is ever unpublished.

Each is the capture with topics dropped and nothing invented.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Final
from unittest.mock import patch

from homeassistant.const import CONF_HOST, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator
from span_panel_api import SpanBatterySnapshot

from custom_components.span_panel.config_flow import STEP_USER_DATA_SCHEMA
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
    ENABLE_ENERGY_DIP_COMPENSATION,
    NEW_INSTALL_ENERGY_DIP_COMPENSATION,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.control_gate import DEFAULT_ALLOW_CONTEXTLESS_CONTROL
from custom_components.span_panel.id_builder import build_circuit_unique_id
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.options import (
    ALLOW_CONTEXTLESS_CONTROL,
    ENERGY_DISPLAY_PRECISION,
    POWER_DISPLAY_PRECISION,
)
from custom_components.span_panel.runtime import loaded_runtime_data
from custom_components.span_panel.util import SUB_DEVICE_BESS, classify_sub_device_identifier

from .captures_replay import (
    CAPTURES,
    DIGESTS,
    ReplayClient,
    RetainedTree,
    capture,
    description,
    devices_of_type,
    panel_device_id,
    replay,
    snapshot,
    without_device,
    without_topics,
)

FIXTURES: Final = Path(__file__).parent / "fixtures"
EXPECTED_ENTITIES: Final = FIXTURES / "expected_entities"
TOPOLOGY: Final = FIXTURES / "topology"

BATTERY_TYPE: Final = "energy.ebus.device.bess"
R202639_CAPTURE: Final = "main32_r202639"
UNVALUED_BATTERY_TOPICS: Final = ("soc/soc", "soc/soe", "meter/active-power")
"""The battery's state of charge, state of energy and power: what it publishes once it is up."""

BACKUP_FORECAST_NODE: Final = "shed-forecast"
"""The panel's backup forecast, which it can only make for a battery it has."""

POWER_FLOWS_NODE: Final = "power-flows"
"""The panel's own power-flow figures: battery, grid, PV and site."""

CIRCUIT_TYPE: Final = "energy.ebus.device.circuit"
CIRCUIT_POWER_TOPIC: Final = "meter/active-power"

PANEL_NAME: Final = "Span Panel"
"""What the config flow names a first panel, in the entry's title and data."""

_USER_STEP_DEFAULTS: Final[Mapping[str, object]] = STEP_USER_DATA_SCHEMA({})
"""What the config flow's first step fills in when a user changes nothing."""

FRESH_INSTALL_OPTIONS: Final[Mapping[str, object]] = {
    # The naming flags the flow writes when no naming pattern was chosen.
    USE_DEVICE_PREFIX: True,
    USE_CIRCUIT_NUMBERS: False,
    POWER_DISPLAY_PRECISION: _USER_STEP_DEFAULTS[POWER_DISPLAY_PRECISION],
    ENERGY_DISPLAY_PRECISION: _USER_STEP_DEFAULTS[ENERGY_DISPLAY_PRECISION],
    ENABLE_ENERGY_DIP_COMPENSATION: NEW_INSTALL_ENERGY_DIP_COMPENSATION,
    ALLOW_CONTEXTLESS_CONTROL: DEFAULT_ALLOW_CONTEXTLESS_CONTROL,
}
"""The options the config flow writes when a user accepts every default."""


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """The switch platform's relock and debounce timers may outlive a test."""
    return True


# ---------------------------------------------------------------------------
# The replays
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Replay:
    """A tree to set up, under the stem its fixtures are filed by."""

    stem: str
    tree: Callable[[], RetainedTree]


def _battery(tree: RetainedTree) -> str:
    (battery,) = devices_of_type(tree, BATTERY_TYPE)
    return battery


def _published_on(tree: RetainedTree, device_id: str, node: str) -> list[str]:
    """Every topic of one node a device publishes a value on."""
    return [topic for topic in tree[device_id] if topic.startswith(f"{node}/")]


def _no_battery() -> RetainedTree:
    tree = capture(R202639_CAPTURE).tree()
    tree = without_device(tree, _battery(tree))
    panel = panel_device_id(tree)
    return without_topics(tree, panel, *_published_on(tree, panel, BACKUP_FORECAST_NODE))


def _battery_unvalued() -> RetainedTree:
    tree = capture(R202639_CAPTURE).tree()
    return without_topics(tree, _battery(tree), *UNVALUED_BATTERY_TOPICS)


def _drawing_circuit(tree: RetainedTree) -> str:
    """The first circuit, by id, publishing a non-zero power, so a 0 could never pass for its absence."""
    return next(
        circuit
        for circuit in sorted(devices_of_type(tree, CIRCUIT_TYPE))
        if float(tree[circuit].get(CIRCUIT_POWER_TOPIC, "0")) != 0
    )


def _unpublished_readings() -> RetainedTree:
    tree = capture(R202639_CAPTURE).tree()
    panel = panel_device_id(tree)
    circuit = _drawing_circuit(tree)
    tree = without_topics(tree, panel, *_published_on(tree, panel, POWER_FLOWS_NODE))
    return without_topics(tree, circuit, CIRCUIT_POWER_TOPIC)


REPLAYS: Final = (
    *(Replay(captured.stem, captured.tree) for captured in CAPTURES),
    Replay(f"{R202639_CAPTURE}-no-battery", _no_battery),
    Replay(f"{R202639_CAPTURE}-battery-unvalued", _battery_unvalued),
    Replay(f"{R202639_CAPTURE}-unpublished-readings", _unpublished_readings),
)


def _ids(replays: tuple[Replay, ...]) -> list[str]:
    return [item.stem for item in replays]


# ---------------------------------------------------------------------------
# Installing a replay for real
# ---------------------------------------------------------------------------


async def _install(hass: HomeAssistant, tree: RetainedTree, stem: str) -> MockConfigEntry:
    """Set the tree up the way a fresh installation of it is set up, its states settled."""
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
        options=dict(FRESH_INSTALL_OPTIONS),
        title=PANEL_NAME,
        entry_id=f"entry-{stem}",
        unique_id=serial,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.span_panel.SpanMqttClient", return_value=ReplayClient(adapter)):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        # What the first streamed snapshot does on a live panel. Without it an
        # entity whose state is written only from a coordinator update reads
        # whatever an unrelated update happened to leave, which varies by run.
        runtime = loaded_runtime_data(entry)
        assert runtime is not None
        runtime.coordinator.async_update_listeners()
        await hass.async_block_till_done()
    return entry


def _device_identifier(device: dr.AnyDeviceEntry) -> str:
    """The device's one identifier in this domain: the stable name of the card an entity is on."""
    (identifier,) = (name for domain, name in device.identifiers if domain == DOMAIN)
    return identifier


def _text(value: object) -> str | None:
    """A registry value as the file records it: its string form, or null."""
    return None if value is None else str(value)


def _device_rows(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, dict[str, str | None]]:
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    identifiers = {device.id: _device_identifier(device) for device in devices}
    rows: dict[str, dict[str, str | None]] = {}
    for device in devices:
        assert isinstance(device, dr.DeviceEntry), f"{device.id} is a child device entry"
        rows[identifiers[device.id]] = {
            "name": device.name,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "hw_version": device.hw_version,
            "sw_version": device.sw_version,
            "serial_number": device.serial_number,
            "via_device": identifiers.get(device.via_device_id) if device.via_device_id else None,
        }
    return rows


def _entity_rows(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, dict[str, str | None]]:
    devices = dr.async_get(hass)
    rows: dict[str, dict[str, str | None]] = {}
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        assert entity.unique_id not in rows, f"two platforms register {entity.unique_id}"
        device = devices.async_get(entity.device_id) if entity.device_id else None
        state = hass.states.get(entity.entity_id)
        rows[entity.unique_id] = {
            "platform": entity.domain,
            "entity_id": entity.entity_id,
            "original_name": entity.original_name,
            "translation_key": entity.translation_key,
            "device": _device_identifier(device) if device is not None else None,
            "device_class": _text(entity.original_device_class),
            "state_class": _text((entity.capabilities or {}).get("state_class")),
            "unit_of_measurement": _text(entity.unit_of_measurement),
            "entity_category": _text(entity.entity_category),
            "disabled_by": _text(entity.disabled_by),
            "hidden_by": _text(entity.hidden_by),
            "state": state.state if state is not None else None,
        }
    return rows


def _registered(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, object]:
    return {"devices": _device_rows(hass, entry), "entities": _entity_rows(hass, entry)}


def _normalised(value: object, aliases: Mapping[str, str]) -> object:
    """`value` with every registry id replaced by the stable name of what it identifies."""
    if isinstance(value, dict):
        return {
            aliases.get(str(key), str(key)): _normalised(item, aliases)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalised(item, aliases) for item in value]
    if isinstance(value, str):
        return aliases.get(value, value)
    return value


async def _topology(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, entry: MockConfigEntry
) -> object:
    """What `span_panel/panel_topology` answers for the panel, registry ids made stable.

    Device registry ids are minted afresh by every installation, so each one is
    replaced by its device's identifier wherever it appears, as a key or as a
    value. The card treats them as opaque handles, so the substitution changes
    nothing it renders.
    """
    devices = dr.async_get(hass)
    panel = devices.async_get_device_by_identifier((DOMAIN, str(entry.unique_id)), entry.entry_id)
    assert panel is not None
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "span_panel/panel_topology", "device_id": panel.id})
    response = await client.receive_json()
    assert response["success"], response
    aliases = {
        device.id: _device_identifier(device)
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
    }
    return _normalised(response["result"], aliases)


def _serialised(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _compare_or_update(path: Path, actual: object, update: bool) -> object:
    """Return what is on file at `path`, first rewriting it from `actual` if asked to.

    Asked by `--update-capture-fixtures`; a regenerated file then compares equal by construction.
    """
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_serialised(actual), encoding="utf-8")
    assert path.exists(), (
        f"{path.relative_to(FIXTURES.parent.parent)} does not exist. Generate it with "
        "--update-capture-fixtures, then read every line of it before committing."
    )
    expected: object = json.loads(path.read_text(encoding="utf-8"))
    return expected


def _section(record: object, name: str) -> Mapping[str, object]:
    section = record.get(name) if isinstance(record, dict) else None
    assert isinstance(section, dict), f"the record has no {name!r} section"
    return section


def _changed_fields(before: object, after: object) -> str:
    """Only the fields of a row that moved, so a one-field change reads as one."""
    if not (isinstance(before, dict) and isinstance(after, dict)):
        return f"{before} -> {after}"
    return ", ".join(
        f"{field}: {before.get(field)!r} -> {after.get(field)!r}"
        for field in sorted(set(before) | set(after))
        if before.get(field) != after.get(field)
    )


def _row_diff(expected: Mapping[str, object], actual: Mapping[str, object]) -> str:
    lines = [f"  - {key}: {expected[key]}" for key in sorted(set(expected) - set(actual))]
    lines += [f"  + {key}: {actual[key]}" for key in sorted(set(actual) - set(expected))]
    lines += [
        f"  ~ {key}: {_changed_fields(expected[key], actual[key])}"
        for key in sorted(set(expected) & set(actual))
        if expected[key] != actual[key]
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The premise
# ---------------------------------------------------------------------------


def _recorded_digests() -> dict[str, str]:
    recorded: dict[str, str] = {}
    for line in DIGESTS.read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        recorded[name] = digest
    return recorded


def test_every_capture_is_the_published_copy() -> None:
    """Byte for byte what the emitter published; the README names the commit.

    Exact both ways: a capture without a recorded digest has no provenance, and a
    digest without its capture describes a file that went away.
    """
    actual = {
        captured.path.name: hashlib.sha256(captured.path.read_bytes()).hexdigest()
        for captured in CAPTURES
    }
    assert actual == _recorded_digests()


def test_no_fixture_outlives_its_replay() -> None:
    """A fixture whose replay is gone would sit on file asserting nothing."""
    stems = set(_ids(REPLAYS))
    for directory in (EXPECTED_ENTITIES, TOPOLOGY):
        orphaned = sorted(path.name for path in directory.glob("*.json") if path.stem not in stems)
        assert not orphaned, f"{directory.name} holds files no replay produces: {orphaned}"


def test_the_battery_less_variant_declares_no_battery_and_forecasts_no_backup() -> None:
    tree = _no_battery()
    panel = panel_device_id(tree)
    nodes = description(tree, panel).get("nodes")

    assert devices_of_type(tree, BATTERY_TYPE) == []
    assert snapshot(tree).battery == SpanBatterySnapshot()
    assert _published_on(tree, panel, BACKUP_FORECAST_NODE) == []
    assert isinstance(nodes, dict) and BACKUP_FORECAST_NODE in nodes, "the declarations stay"


def test_the_unpublished_variant_declares_what_it_does_not_publish() -> None:
    tree = _unpublished_readings()
    panel = panel_device_id(tree)
    circuit = _drawing_circuit(capture(R202639_CAPTURE).tree())
    nodes = description(tree, panel).get("nodes")
    replayed = snapshot(tree)

    assert _published_on(tree, panel, POWER_FLOWS_NODE) == []
    assert isinstance(nodes, dict) and POWER_FLOWS_NODE in nodes, "the declarations stay"
    assert CIRCUIT_POWER_TOPIC not in tree[circuit]
    assert replayed.circuits[circuit].instant_power_w is None
    assert (replayed.power_flow_battery, replayed.power_flow_pv) == (None, None)


def test_the_unvalued_variant_declares_its_battery_and_publishes_neither_reading() -> None:
    tree = _battery_unvalued()
    battery = _battery(tree)

    assert all(topic not in tree[battery] for topic in UNVALUED_BATTERY_TOPICS)
    unvalued = snapshot(tree).battery
    assert (unvalued.soe_percentage, unvalued.soe_kwh, unvalued.power_w) == (None, None, None)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item", REPLAYS, ids=_ids(REPLAYS))
async def test_a_replay_registers_exactly_the_expected_devices_and_entities(
    hass: HomeAssistant, item: Replay, update_capture_fixtures: bool
) -> None:
    entry = await _install(hass, item.tree(), item.stem)
    actual = _registered(hass, entry)

    expected = _compare_or_update(
        EXPECTED_ENTITIES / f"{item.stem}.json", actual, update_capture_fixtures
    )

    moved = {
        name: _row_diff(_section(expected, name), _section(actual, name))
        for name in ("devices", "entities")
    }
    assert actual == expected, (
        f"what {item.stem} registers moved (- gone, + new, ~ changed):\n"
        + "".join(f"{name}:\n{diff}\n" for name, diff in moved.items() if diff)
        + "\nIf the change is deliberate, regenerate the file in the same diff and name the reason."
    )


@pytest.mark.parametrize("item", REPLAYS, ids=_ids(REPLAYS))
async def test_a_replay_answers_the_expected_topology(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    item: Replay,
    update_capture_fixtures: bool,
) -> None:
    entry = await _install(hass, item.tree(), item.stem)
    actual = await _topology(hass, hass_ws_client, entry)

    expected = _compare_or_update(TOPOLOGY / f"{item.stem}.json", actual, update_capture_fixtures)

    assert actual == expected, (
        f"the topology {item.stem} answers moved; "
        "if the change is deliberate, regenerate the file in the same diff and name the reason."
    )


async def test_a_reading_the_panel_did_not_publish_reads_unknown(hass: HomeAssistant) -> None:
    """On every line, an unpublished circuit power is unknown, never 0 and never a stale value."""
    tree = _unpublished_readings()
    entry = await _install(hass, tree, f"{R202639_CAPTURE}-unpublished-readings")
    unique_id = build_circuit_unique_id(
        str(entry.unique_id), _drawing_circuit(capture(R202639_CAPTURE).tree()), "instantPowerW"
    )

    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_UNKNOWN


async def test_a_panel_without_a_battery_gets_no_battery_entity(hass: HomeAssistant) -> None:
    """On every line, a panel with no battery gets no battery card and no entity on one."""
    entry = await _install(hass, _no_battery(), f"{R202639_CAPTURE}-no-battery")

    battery_cards = {
        device.id
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        if classify_sub_device_identifier(_device_identifier(device)) == SUB_DEVICE_BESS
    }
    on_a_battery_card = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if entity.device_id in battery_cards
    ]
    assert (battery_cards, on_a_battery_card) == (set(), [])
