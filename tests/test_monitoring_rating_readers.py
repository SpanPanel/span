"""The Breaker Rating sensor and the panel topology report the effective rating.

A user may correct a circuit's published rating. The sensor's value, the topology's
`breaker_rating_a` and the two fields beside it come from the panel's
`MonitoringRatingManager`, so they agree with current monitoring. The wire value stays
available as `panel_breaker_rating_a`, and the energy-counter bound keeps judging
against it.
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
from custom_components.span_panel.monitoring_rating_manager import MonitoringRatingManager
from custom_components.span_panel.monitoring_ratings import service_rating_a
from custom_components.span_panel.sensor import create_circuit_sensors
from custom_components.span_panel.sensor_circuit import (
    SpanCircuitBreakerRatingSensor,
    SpanCircuitPowerSensor,
)
from custom_components.span_panel.sensor_definitions import (
    CIRCUIT_BREAKER_RATING_SENSOR,
    CIRCUIT_CURRENT_SENSOR,
    CIRCUIT_SENSORS,
)
from custom_components.span_panel.services import _async_register_monitoring_rating_services

from .captures_replay import CAPTURES, Capture, snapshot
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .test_expected_entities import _install, _topology
from .test_monitoring_rating_services import _loaded_panel

SERVICE_A: Final = 100
PANEL_A: Final = 20.0
MONITORING_A: Final = 50
_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}
POWER: Final = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")


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
) -> MonitoringRatingManager:
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=snap.serial_number)
    entry.add_to_hass(hass)
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snap, _DECLARED)
    if rated:
        manager.set_rating("circuit-2", snap, MONITORING_A)
    return manager


def _coordinator(snap: SpanPanelSnapshot, manager: MonitoringRatingManager) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snap
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.monitoring_rating_manager = manager
    coordinator.config_entry.options = {}
    coordinator.config_entry.data = {}
    coordinator.config_entry.title = "SPAN Panel"
    return coordinator


# -- The sensor ---------------------------------------------------------------


async def test_the_sensor_reports_the_monitoring_rating(hass: HomeAssistant) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=True))

    sensor = SpanCircuitBreakerRatingSensor(
        coordinator, CIRCUIT_BREAKER_RATING_SENSOR, snap, "circuit-2"
    )

    sensor._update_native_value()
    assert sensor.native_value == float(MONITORING_A)


async def test_the_sensor_reports_the_panel_rating_without_one(hass: HomeAssistant) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=False))

    sensor = SpanCircuitBreakerRatingSensor(
        coordinator, CIRCUIT_BREAKER_RATING_SENSOR, snap, "circuit-2"
    )

    sensor._update_native_value()
    assert sensor.native_value == PANEL_A


async def test_the_sensor_carries_both_ratings_as_attributes(hass: HomeAssistant) -> None:
    snap = _panel()
    corrected = SpanCircuitBreakerRatingSensor(
        _coordinator(snap, await _manager(hass, snap, rated=True)),
        CIRCUIT_BREAKER_RATING_SENSOR,
        snap,
        "circuit-2",
    ).extra_state_attributes
    plain = SpanCircuitBreakerRatingSensor(
        _coordinator(snap, await _manager(hass, snap, rated=False)),
        CIRCUIT_BREAKER_RATING_SENSOR,
        snap,
        "circuit-2",
    ).extra_state_attributes

    assert corrected is not None and plain is not None
    assert corrected["panel_breaker_rating_a"] == PANEL_A
    assert corrected["breaker_rating_user_set"] is True
    assert plain["panel_breaker_rating_a"] == PANEL_A
    assert plain["breaker_rating_user_set"] is False
    # The parent's attributes are still there.
    assert corrected["tabs"] == plain["tabs"]


async def test_the_power_and_current_sensors_gain_nothing(hass: HomeAssistant) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=True))

    for description in (POWER, CIRCUIT_CURRENT_SENSOR):
        sensor = SpanCircuitPowerSensor(coordinator, description, snap, "circuit-2")
        attributes = sensor.extra_state_attributes
        assert attributes is not None
        assert "panel_breaker_rating_a" not in attributes, description.key
        assert "breaker_rating_user_set" not in attributes, description.key


async def test_the_rating_sensor_keeps_its_identity(hass: HomeAssistant) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=True))
    plain = SpanCircuitPowerSensor(coordinator, CIRCUIT_BREAKER_RATING_SENSOR, snap, "circuit-2")
    rating = SpanCircuitBreakerRatingSensor(
        coordinator, CIRCUIT_BREAKER_RATING_SENSOR, snap, "circuit-2"
    )

    assert rating.unique_id == plain.unique_id
    assert rating._attr_name == plain._attr_name
    assert rating._residual_field_paths == plain._residual_field_paths


async def test_the_sensor_is_unknown_where_the_circuit_publishes_no_rating(
    hass: HomeAssistant,
) -> None:
    snap = _panel()
    manager = await _manager(hass, snap, rated=False)
    unrated = replace(
        snap, circuits={"circuit-2": replace(snap.circuits["circuit-2"], breaker_rating_a=None)}
    )
    coordinator = _coordinator(unrated, manager)

    sensor = SpanCircuitBreakerRatingSensor(
        coordinator, CIRCUIT_BREAKER_RATING_SENSOR, unrated, "circuit-2"
    )

    sensor._update_native_value()
    assert sensor.native_value is None


async def test_only_the_rating_sensor_is_the_subclass(hass: HomeAssistant) -> None:
    snap = _panel()
    coordinator = _coordinator(snap, await _manager(hass, snap, rated=True))
    coordinator.client.schema_major = "schema_1"
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})

    entities = create_circuit_sensors(coordinator, snap, entry)

    subclassed = {
        str(e.unique_id) for e in entities if isinstance(e, SpanCircuitBreakerRatingSensor)
    }
    assert len(subclassed) == 1
    assert all(unique_id.endswith("_breaker_rating") for unique_id in subclassed)


async def test_the_sensor_follows_the_services_without_a_new_snapshot(
    hass: HomeAssistant,
) -> None:
    snap = _panel()
    entry, manager = await _loaded_panel(hass, "sp3-readers-001", snap)
    coordinator = entry.runtime_data.coordinator
    sensor = SpanCircuitBreakerRatingSensor(
        _coordinator(snap, manager), CIRCUIT_BREAKER_RATING_SENSOR, snap, "circuit-2"
    )
    sensor._update_native_value()
    # The coordinator's listeners are the entities: updating them recomputes the sensor.
    coordinator.async_update_listeners.side_effect = sensor._update_native_value
    _async_register_monitoring_rating_services(hass)
    assert sensor.native_value == PANEL_A

    await hass.services.async_call(
        DOMAIN,
        "set_circuit_monitoring_rating",
        {"circuit_id": "circuit-2", "monitoring_rating_a": MONITORING_A},
        blocking=True,
    )
    assert sensor.native_value == float(MONITORING_A)

    await hass.services.async_call(
        DOMAIN, "clear_circuit_monitoring_rating", {"circuit_id": "circuit-2"}, blocking=True
    )
    assert sensor.native_value == PANEL_A


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


async def test_a_replay_with_one_stored_monitoring_rating_reports_it(
    hass: HomeAssistant,
    hass_storage: dict[str, object],
    hass_ws_client: WebSocketGenerator,
) -> None:
    candidates = [
        (captured, found)
        for captured in CAPTURES
        if (found := _capture_with_headroom(captured)) is not None
    ]
    assert candidates
    captured, (circuit_id, panel_a, monitoring_a) = candidates[0]
    entry_id = f"entry-{captured.name}"
    hass_storage[f"span_panel_monitoring_ratings.{entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"span_panel_monitoring_ratings.{entry_id}",
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
    tree = captured.tree()
    replayed = snapshot(tree)

    entry = await _install(hass, tree, captured.name)
    topology = await _topology(hass, hass_ws_client, entry)

    assert isinstance(topology, dict)
    rows = topology["circuits"]
    assert isinstance(rows, dict)
    corrected = rows[circuit_id]
    assert corrected["breaker_rating_a"] == float(monitoring_a)
    assert corrected["panel_breaker_rating_a"] == panel_a
    assert corrected["breaker_rating_user_set"] is True
    for other_id, row in rows.items():
        if other_id == circuit_id:
            continue
        assert row["breaker_rating_user_set"] is False, other_id
        assert row["breaker_rating_a"] == row["panel_breaker_rating_a"], other_id
        assert row["breaker_rating_a"] == replayed.circuits[other_id].breaker_rating_a, other_id
    assert json.dumps(topology)  # serialisable, as the websocket requires

    # The sensor's identity is the one every installation registers.
    unique_id = build_circuit_unique_id(str(entry.unique_id), circuit_id, "breaker_rating")
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id) is not None
