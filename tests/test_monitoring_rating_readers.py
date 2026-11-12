"""The breaker rating stays the panel's; the topology adds the monitoring rating.

A user may set the rating current monitoring judges a circuit against. The Breaker Rating
sensor and the topology's `breaker_rating_a` keep reporting what the panel publishes, and
the topology's `monitoring_rating_a` and `monitoring_rating_user_set` come from the panel's
`PackageRatingManager`, so they agree with current monitoring. The energy-counter bound
keeps judging against the panel rating.
"""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Final
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator
from span_panel_api import FieldMetadata, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.counter_plausibility import LIFETIME_HOURS, lifetime_bound_wh
from custom_components.span_panel.id_builder import build_circuit_unique_id
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.package_ratings import service_rating_a
from custom_components.span_panel.sensor import create_circuit_sensors
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
from custom_components.span_panel.sensor_definitions import CIRCUIT_BREAKER_RATING_SENSOR

from .captures_replay import CAPTURES, Capture, snapshot
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .test_expected_entities import _install, _topology

SERVICE_A: Final = 100
PANEL_A: Final = 20.0
MONITORING_A: Final = 50
_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}


def _panel() -> SpanPanelSnapshot:
    return SpanPanelSnapshotFactory.create(
        serial_number="sp3-readers-001",
        circuits={
            "circuit-2": SpanCircuitSnapshotFactory.create(
                circuit_id="circuit-2",
                name="Name of circuit-2",
                tabs=[2],
                breaker_rating_a=PANEL_A,
                breaker_rating_declared=True,
                current_a=5.0,
            )
        },
        main_breaker_rating_a=SERVICE_A,
    )


async def _manager(
    hass: HomeAssistant, snap: SpanPanelSnapshot, *, rated: bool
) -> PackageRatingManager:
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=snap.serial_number)
    entry.add_to_hass(hass)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snap, _DECLARED)
    if rated:
        manager.set_rating("circuit-2", snap, MONITORING_A)
    return manager


def _coordinator(snap: SpanPanelSnapshot, manager: PackageRatingManager) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snap
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.package_rating_manager = manager
    coordinator.config_entry.options = {}
    coordinator.config_entry.data = {}
    coordinator.config_entry.title = "SPAN Panel"
    return coordinator


# -- The sensor ---------------------------------------------------------------


def _breaker_rating_sensor(
    coordinator: MagicMock, snap: SpanPanelSnapshot
) -> SpanCircuitPowerSensor:
    """Build the Breaker Rating sensor as the integration registers it."""
    return SpanCircuitPowerSensor(coordinator, CIRCUIT_BREAKER_RATING_SENSOR, snap, "circuit-2")


async def test_the_sensor_reports_the_panel_rating_whatever_the_monitoring_rating(
    hass: HomeAssistant,
) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=True))

    sensor = _breaker_rating_sensor(coordinator, snap)

    sensor._update_native_value()
    assert sensor.native_value == PANEL_A


async def test_the_sensor_reports_the_panel_rating_without_one(hass: HomeAssistant) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=False))

    sensor = _breaker_rating_sensor(coordinator, snap)

    sensor._update_native_value()
    assert sensor.native_value == PANEL_A


async def test_the_sensor_carries_no_rating_attributes(hass: HomeAssistant) -> None:
    snap = _panel()
    rated = _breaker_rating_sensor(
        _coordinator(snap, await _manager(hass, snap, rated=True)), snap
    ).extra_state_attributes
    plain = _breaker_rating_sensor(
        _coordinator(snap, await _manager(hass, snap, rated=False)), snap
    ).extra_state_attributes

    assert rated is not None and plain is not None
    for attributes in (rated, plain):
        assert "panel_breaker_rating_a" not in attributes
        assert "breaker_rating_user_set" not in attributes
        assert "monitoring_rating_a" not in attributes
        assert "monitoring_rating_user_set" not in attributes
    # A monitoring rating changes nothing the sensor reports.
    assert rated == plain


async def test_the_sensor_is_unknown_where_the_circuit_publishes_no_rating(
    hass: HomeAssistant,
) -> None:
    snap = _panel()
    manager = await _manager(hass, snap, rated=False)
    unrated = replace(
        snap, circuits={"circuit-2": replace(snap.circuits["circuit-2"], breaker_rating_a=None)}
    )
    coordinator = _coordinator(unrated, manager)

    sensor = _breaker_rating_sensor(coordinator, unrated)

    sensor._update_native_value()
    assert sensor.native_value is None


async def test_the_rating_sensor_is_the_plain_circuit_sensor(hass: HomeAssistant) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=True))
    coordinator.client.schema_major = "schema_1"
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})

    entities = create_circuit_sensors(coordinator, snap, entry)

    ratings = [e for e in entities if str(e.unique_id).endswith("_breaker_rating")]
    assert len(ratings) == 1
    assert type(ratings[0]) is SpanCircuitPowerSensor


# -- Counter plausibility keeps the panel rating ---------------------------------


async def test_a_monitoring_rating_does_not_change_the_energy_counter_bound(
    hass: HomeAssistant,
) -> None:
    snap = _panel()
    circuit: SpanCircuitSnapshot = snap.circuits["circuit-2"]
    before = lifetime_bound_wh(circuit)
    manager = await _manager(hass, snap, rated=True)

    assert manager.resolve("circuit-2", snap).judged_a == float(MONITORING_A)
    assert before == PANEL_A * 120.0 * LIFETIME_HOURS
    assert lifetime_bound_wh(snap.circuits["circuit-2"]) == before


# -- The panel topology ----------------------------------------------------------


def _capture_with_headroom(captured: Capture) -> tuple[str, float, int] | None:
    """Return a circuit rated below the service rating, with its rating and the service rating."""
    snap = snapshot(captured.tree())
    service = service_rating_a(snap)
    if service is None:
        return None
    for circuit_id, circuit in sorted(snap.circuits.items()):
        rating = circuit.breaker_rating_a
        if rating is not None and rating < service and not circuit.measures_outside_panel:
            return circuit_id, rating, int(service)
    return None


def _candidate(*, describes_breakers: bool) -> tuple[Capture, tuple[str, float, int]]:
    """The first capture with headroom whose panel does, or does not, describe its multi-load breakers."""
    candidates = [
        (captured, found)
        for captured in CAPTURES
        if snapshot(captured.tree()).publishes_shared_meters is describes_breakers
        and (found := _capture_with_headroom(captured)) is not None
    ]
    assert candidates
    return candidates[0]


def _store_one_rating(
    hass_storage: dict[str, object], entry_id: str, circuit_id: str, panel_a: float, monitoring_a: int
) -> None:
    hass_storage[f"span_panel_package_ratings.{entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"span_panel_package_ratings.{entry_id}",
        "data": {
            "ratings": {
                circuit_id: {
                    "rating_a": monitoring_a,
                    "panel_a_at_set": panel_a,
                    "label": "Synthetic label",
                }
            },
            "unacknowledged": {},
        },
    }


async def test_a_replay_with_one_stored_monitoring_rating_reports_it(
    hass: HomeAssistant,
    hass_storage: dict[str, object],
    hass_ws_client: WebSocketGenerator,
) -> None:
    captured, (circuit_id, panel_a, monitoring_a) = _candidate(describes_breakers=False)
    _store_one_rating(hass_storage, f"entry-{captured.name}", circuit_id, panel_a, monitoring_a)
    tree = captured.tree()
    replayed = snapshot(tree)

    entry = await _install(hass, tree, captured.name)
    topology = await _topology(hass, hass_ws_client, entry)

    assert isinstance(topology, dict)
    assert topology["monitoring_rating_offered"] is True
    rows = topology["circuits"]
    assert isinstance(rows, dict)
    rated = rows[circuit_id]
    assert rated["breaker_rating_a"] == panel_a
    assert rated["monitoring_rating_a"] == float(monitoring_a)
    assert rated["monitoring_rating_user_set"] is True
    for other_id, row in rows.items():
        assert "panel_breaker_rating_a" not in row, other_id
        assert "breaker_rating_user_set" not in row, other_id
        assert row["breaker_rating_a"] == replayed.circuits[other_id].breaker_rating_a, other_id
        if other_id == circuit_id:
            continue
        assert row["monitoring_rating_user_set"] is False, other_id
        assert row["monitoring_rating_a"] == row["breaker_rating_a"], other_id
    assert json.dumps(topology)  # serialisable, as the websocket requires

    # The sensor's identity is the one every installation registers.
    unique_id = build_circuit_unique_id(str(entry.unique_id), circuit_id, "breaker_rating")
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id) is not None


async def test_a_replay_of_a_panel_that_describes_its_breakers_drops_a_stored_rating(
    hass: HomeAssistant,
    hass_storage: dict[str, object],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Its circuits each publish their own rating, so the topology offers no monitoring rating and judges the panel's."""
    captured, (circuit_id, panel_a, monitoring_a) = _candidate(describes_breakers=True)
    entry_id = f"entry-{captured.name}"
    _store_one_rating(hass_storage, entry_id, circuit_id, panel_a, monitoring_a)

    entry = await _install(hass, captured.tree(), captured.name)
    topology = await _topology(hass, hass_ws_client, entry)
    await hass.async_block_till_done()

    assert isinstance(topology, dict)
    assert topology["monitoring_rating_offered"] is False
    rows = topology["circuits"]
    assert isinstance(rows, dict)
    assert rows[circuit_id]["monitoring_rating_user_set"] is False
    assert rows[circuit_id]["monitoring_rating_a"] == panel_a
    stored = hass_storage[f"span_panel_package_ratings.{entry_id}"]
    assert isinstance(stored, dict)
    assert stored["data"]["ratings"] == {}
    assert set(stored["data"]["unacknowledged"]) == {circuit_id}
