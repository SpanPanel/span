"""The backup-planning forecast, surfaced as two sensors and three attributes.

Every assertion here runs against a real snapshot built by the real schema_1
adapter over the reference capture, and every expected value is read out of that
capture rather than written as a literal. That is deliberate and it is the whole
design of this module: a test that pins the same constant the code pins passes
whether or not the wire is ever read, so each reading is proved by republishing
it — a different value, or none — and asserting the entity moved.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    Producibility,
)
from custom_components.span_panel.helpers import detect_capabilities, has_shed_forecast
from custom_components.span_panel.sensor import create_shed_forecast_sensors
from custom_components.span_panel.sensor_definitions import SHED_FORECAST_SENSORS
from custom_components.span_panel.sensor_panel import SpanShedForecastSensor
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_HOST, UnitOfTime
from homeassistant.helpers.entity import EntityCategory

from .adapter_fixtures import SCHEMA_ONE_PANEL, schema_one_snapshot, schema_one_tree
from .factories import SpanPanelSnapshotFactory

from pytest_homeassistant_custom_component.common import MockConfigEntry

NODE = "shed-forecast"

TIME_TO_PRIORITY_SHED = "time-to-priority-shed"
TOTAL_TIME_REMAINING = "total-time-remaining"
FULL_CHARGE_TIME_TO_PRIORITY_SHED = "full-charge-time-to-priority-shed"
FULL_CHARGE_TOTAL_TIME_REMAINING = "full-charge-total-time-remaining"
CONFIDENCE = "confidence"

TIME_TO_PRIORITY_SHED_KEY = "time_to_priority_shed"
TOTAL_TIME_REMAINING_KEY = "shed_total_time_remaining"


@pytest.fixture(autouse=True)
def _mock_entity_registry() -> Any:
    """Patch entity registry lookups used during sensor construction."""
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    with patch(
        "custom_components.span_panel.sensor_base.er.async_get",
        return_value=registry,
    ):
        yield registry


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    """A coordinator-like mock carrying one snapshot."""
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = MagicMock()
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50"},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
    )
    return coordinator


def _published(property_id: str) -> str:
    """What the capture publishes for one forecast property."""
    return schema_one_tree()[SCHEMA_ONE_PANEL][f"{NODE}/{property_id}"]


def _republishing(**topics: str) -> SpanPanelSnapshot:
    """A snapshot from the capture with some forecast topics rewritten."""
    tree = schema_one_tree()
    for property_id, value in topics.items():
        tree[SCHEMA_ONE_PANEL][f"{NODE}/{property_id.replace('_', '-')}"] = value
    return schema_one_snapshot(tree)


def _without(*property_ids: str) -> SpanPanelSnapshot:
    """A snapshot from a capture that stopped publishing (and declaring) properties."""
    tree = schema_one_tree()
    description = json.loads(tree[SCHEMA_ONE_PANEL]["$description"])
    for property_id in property_ids:
        del tree[SCHEMA_ONE_PANEL][f"{NODE}/{property_id}"]
        del description["nodes"][NODE]["properties"][property_id]
    tree[SCHEMA_ONE_PANEL]["$description"] = json.dumps(description)
    return schema_one_snapshot(tree)


def _without_node() -> SpanPanelSnapshot:
    """A snapshot from a capture with no `shed-forecast` node at all."""
    tree = schema_one_tree()
    for topic in [t for t in tree[SCHEMA_ONE_PANEL] if t.startswith(f"{NODE}/")]:
        del tree[SCHEMA_ONE_PANEL][topic]
    description = json.loads(tree[SCHEMA_ONE_PANEL]["$description"])
    del description["nodes"][NODE]
    tree[SCHEMA_ONE_PANEL]["$description"] = json.dumps(description)
    return schema_one_snapshot(tree)


def _sensors(snapshot: SpanPanelSnapshot) -> dict[str, SpanShedForecastSensor]:
    """Whatever the platform creates for this snapshot, keyed by description key."""
    created = create_shed_forecast_sensors(_coordinator(snapshot), snapshot)
    return {sensor.entity_description.key: sensor for sensor in created}


def _state(snapshot: SpanPanelSnapshot, key: str) -> float | int | str | None:
    """The state one forecast sensor reports for a snapshot."""
    sensor = _sensors(snapshot)[key]
    sensor._update_native_value()
    return sensor.native_value


def _attributes(snapshot: SpanPanelSnapshot, key: str) -> dict[str, Any]:
    return _sensors(snapshot)[key].extra_state_attributes or {}


# ---------------------------------------------------------------------------
# The premise: the capture publishes the capability
# ---------------------------------------------------------------------------


def test_the_capture_publishes_the_whole_capability() -> None:
    """Guard the premise for every test below, all of which read the capture for
    their expected value: a capture that stopped publishing the node would make
    them vacuously true rather than failing."""
    panel = schema_one_tree()[SCHEMA_ONE_PANEL]

    for property_id in (
        TIME_TO_PRIORITY_SHED,
        TOTAL_TIME_REMAINING,
        FULL_CHARGE_TIME_TO_PRIORITY_SHED,
        FULL_CHARGE_TOTAL_TIME_REMAINING,
        CONFIDENCE,
    ):
        assert f"{NODE}/{property_id}" in panel


# ---------------------------------------------------------------------------
# States follow the wire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "property_id"),
    [
        (TIME_TO_PRIORITY_SHED_KEY, TIME_TO_PRIORITY_SHED),
        (TOTAL_TIME_REMAINING_KEY, TOTAL_TIME_REMAINING),
    ],
)
def test_each_sensor_reports_the_value_the_panel_published(key: str, property_id: str) -> None:
    assert _state(schema_one_snapshot(), key) == float(_published(property_id))


def test_the_two_sensors_do_not_report_the_same_reading() -> None:
    """The capture publishes different values for the two estimates, so a wiring
    that crossed them would fail here rather than looking plausible."""
    snapshot = schema_one_snapshot()

    assert _state(snapshot, TIME_TO_PRIORITY_SHED_KEY) != _state(
        snapshot, TOTAL_TIME_REMAINING_KEY
    )


@pytest.mark.parametrize(
    ("key", "property_id", "republished"),
    [
        (TIME_TO_PRIORITY_SHED_KEY, TIME_TO_PRIORITY_SHED, "17"),
        (TOTAL_TIME_REMAINING_KEY, TOTAL_TIME_REMAINING, "1440"),
    ],
)
def test_republishing_an_estimate_moves_its_sensor(
    key: str, property_id: str, republished: str
) -> None:
    """The mutation proof. Each republished value differs from every value the
    capture carries, so a sensor pinned to a constant — or wired to the wrong
    property — cannot report it."""
    snapshot = _republishing(**{property_id.replace("-", "_"): republished})

    assert _state(snapshot, key) == float(republished)
    assert _state(snapshot, key) != float(_published(property_id))


@pytest.mark.parametrize(
    ("key", "property_id"),
    [
        (TIME_TO_PRIORITY_SHED_KEY, TIME_TO_PRIORITY_SHED),
        (TOTAL_TIME_REMAINING_KEY, TOTAL_TIME_REMAINING),
    ],
)
def test_zero_minutes_is_a_state_and_not_an_absence(key: str, property_id: str) -> None:
    """Shedding starts now is a reading, and the most important one the capability
    reports. A gate that treated zero as absence would delete the entity exactly
    when a user needs it."""
    snapshot = _republishing(**{property_id.replace("-", "_"): "0"})

    assert key in _sensors(snapshot)
    assert _state(snapshot, key) == 0.0


# ---------------------------------------------------------------------------
# Attributes follow the wire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "attribute", "property_id"),
    [
        (
            TIME_TO_PRIORITY_SHED_KEY,
            "full_charge_time_to_priority_shed",
            FULL_CHARGE_TIME_TO_PRIORITY_SHED,
        ),
        (
            TOTAL_TIME_REMAINING_KEY,
            "full_charge_total_time_remaining",
            FULL_CHARGE_TOTAL_TIME_REMAINING,
        ),
    ],
)
def test_each_sensor_carries_its_own_full_charge_twin(
    key: str, attribute: str, property_id: str
) -> None:
    """The pairing, not just the presence: each estimate carries the
    full-charge figure that refines *it*."""
    attributes = _attributes(schema_one_snapshot(), key)

    assert attributes[attribute] == int(_published(property_id))


def test_no_sensor_carries_the_other_sensors_twin() -> None:
    """The capture publishes 3038 and 4320, and the second happens to equal the
    live total — so only the pairing check above plus this one can tell a correct
    wiring from a crossed one."""
    assert "full_charge_total_time_remaining" not in _attributes(
        schema_one_snapshot(), TIME_TO_PRIORITY_SHED_KEY
    )
    assert "full_charge_time_to_priority_shed" not in _attributes(
        schema_one_snapshot(), TOTAL_TIME_REMAINING_KEY
    )


@pytest.mark.parametrize(
    ("key", "attribute", "property_id"),
    [
        (
            TIME_TO_PRIORITY_SHED_KEY,
            "full_charge_time_to_priority_shed",
            FULL_CHARGE_TIME_TO_PRIORITY_SHED,
        ),
        (
            TOTAL_TIME_REMAINING_KEY,
            "full_charge_total_time_remaining",
            FULL_CHARGE_TOTAL_TIME_REMAINING,
        ),
    ],
)
def test_republishing_a_full_charge_figure_moves_its_attribute(
    key: str, attribute: str, property_id: str
) -> None:
    snapshot = _republishing(**{property_id.replace("-", "_"): "999"})

    assert _attributes(snapshot, key)[attribute] == 999


@pytest.mark.parametrize(
    ("key", "attribute", "property_id"),
    [
        (
            TIME_TO_PRIORITY_SHED_KEY,
            "full_charge_time_to_priority_shed",
            FULL_CHARGE_TIME_TO_PRIORITY_SHED,
        ),
        (
            TOTAL_TIME_REMAINING_KEY,
            "full_charge_total_time_remaining",
            FULL_CHARGE_TOTAL_TIME_REMAINING,
        ),
    ],
)
def test_a_full_charge_figure_the_panel_drops_takes_its_attribute_with_it(
    key: str, attribute: str, property_id: str
) -> None:
    """Absent, not `None`. An attribute that is present and empty reads as a
    reading the panel failed to produce; this is firmware that does not carry the
    property at all."""
    snapshot = _without(property_id)

    assert attribute not in _attributes(snapshot, key)
    # The sensor itself is unaffected: the estimate it reads is still published.
    assert _state(snapshot, key) == float(
        _published(TIME_TO_PRIORITY_SHED if key == TIME_TO_PRIORITY_SHED_KEY else TOTAL_TIME_REMAINING)
    )


@pytest.mark.parametrize("key", [TIME_TO_PRIORITY_SHED_KEY, TOTAL_TIME_REMAINING_KEY])
def test_confidence_rides_on_both_sensors(key: str) -> None:
    """It qualifies both estimates, so it appears on both."""
    assert _attributes(schema_one_snapshot(), key)["forecast_confidence"] == _published(
        CONFIDENCE
    )


@pytest.mark.parametrize("key", [TIME_TO_PRIORITY_SHED_KEY, TOTAL_TIME_REMAINING_KEY])
def test_republishing_confidence_moves_the_attribute(key: str) -> None:
    snapshot = _republishing(confidence="LOW")

    assert _attributes(snapshot, key)["forecast_confidence"] == "LOW"
    assert _attributes(snapshot, key)["forecast_confidence"] != _published(CONFIDENCE)


@pytest.mark.parametrize("key", [TIME_TO_PRIORITY_SHED_KEY, TOTAL_TIME_REMAINING_KEY])
def test_confidence_the_panel_drops_takes_its_attribute_with_it(key: str) -> None:
    assert "forecast_confidence" not in _attributes(_without(CONFIDENCE), key)


def test_a_sensor_with_neither_refinement_publishes_no_attributes_at_all() -> None:
    """`None` rather than an empty dict, which is what the entity contract asks
    for and what stops an empty attribute block rendering."""
    snapshot = _without(FULL_CHARGE_TIME_TO_PRIORITY_SHED, CONFIDENCE)

    assert _sensors(snapshot)[TIME_TO_PRIORITY_SHED_KEY].extra_state_attributes is None


# ---------------------------------------------------------------------------
# Creation is gated on what the panel publishes
# ---------------------------------------------------------------------------


def test_the_capture_creates_both_sensors() -> None:
    assert set(_sensors(schema_one_snapshot())) == {
        TIME_TO_PRIORITY_SHED_KEY,
        TOTAL_TIME_REMAINING_KEY,
    }


def test_a_panel_with_no_forecast_node_gets_no_sensors() -> None:
    """The absence test. A dead entity stuck at unknown is worse than no entity:
    it occupies the entity list, breaks a dashboard card, and cannot be told
    apart from a panel whose forecast has failed."""
    snapshot = _without_node()

    assert has_shed_forecast(snapshot) is False
    assert create_shed_forecast_sensors(_coordinator(snapshot), snapshot) == []


def test_a_flat_panel_gets_no_sensors() -> None:
    """The same absence by the other route: flat firmware publishes no such
    capability, so the factory's default snapshot carries none of the fields."""
    snapshot = SpanPanelSnapshotFactory.create()

    assert has_shed_forecast(snapshot) is False
    assert create_shed_forecast_sensors(_coordinator(snapshot), snapshot) == []


@pytest.mark.parametrize(
    ("dropped", "surviving"),
    [
        (TIME_TO_PRIORITY_SHED, TOTAL_TIME_REMAINING_KEY),
        (TOTAL_TIME_REMAINING, TIME_TO_PRIORITY_SHED_KEY),
    ],
)
def test_a_partial_node_creates_only_the_sensor_it_can_fill(
    dropped: str, surviving: str
) -> None:
    """The catalog marks all four times SHOULD, not MUST, so a partial node is
    legal firmware rather than a defect — and the half it omits must produce no
    entity rather than one permanently unknown."""
    snapshot = _without(dropped)

    assert has_shed_forecast(snapshot) is True
    assert set(_sensors(snapshot)) == {surviving}


def test_the_forecast_appearing_is_a_capability_change() -> None:
    """Which is how a panel that gains the node mid-life gets the sensors: the
    coordinator reloads on a new capability."""
    assert "shed_forecast" not in detect_capabilities(SpanPanelSnapshotFactory.create())
    assert "shed_forecast" in detect_capabilities(schema_one_snapshot())
    assert "shed_forecast" not in detect_capabilities(_without_node())


# ---------------------------------------------------------------------------
# Shape of the entities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("description", SHED_FORECAST_SENSORS, ids=lambda d: d.key)
def test_both_are_duration_sensors_in_the_unit_the_capability_declares(
    description: Any,
) -> None:
    assert description.device_class is SensorDeviceClass.DURATION
    assert description.state_class is SensorStateClass.MEASUREMENT
    assert description.native_unit_of_measurement == UnitOfTime.MINUTES


@pytest.mark.parametrize("description", SHED_FORECAST_SENSORS, ids=lambda d: d.key)
def test_both_are_enabled_by_default_and_not_filed_under_diagnostics(
    description: Any,
) -> None:
    """These are the numbers a user plans a backup around, which is the whole
    argument for surfacing them ahead of the rest of the unread v1.0 surface. A
    disabled or diagnostic sensor would surface them in name only."""
    assert description.entity_registry_enabled_default is True
    assert description.entity_category is not EntityCategory.DIAGNOSTIC


def test_the_declared_unit_matches_what_the_panel_declares() -> None:
    """HA's unit against the tree's, for the two paths schema_1 carries metadata
    for. A disagreement here is what the integration's unit-mismatch Repair
    reports at runtime; catching it in the suite is cheaper."""
    from .adapter_fixtures import schema_one_metadata

    metadata = schema_one_metadata()
    for description, field_path in (
        (SHED_FORECAST_SENSORS[0], "panel.shed_time_to_priority_shed_min"),
        (SHED_FORECAST_SENSORS[1], "panel.shed_total_time_remaining_min"),
    ):
        assert metadata[field_path].unit == description.native_unit_of_measurement


def test_the_two_sensors_get_distinct_unique_ids() -> None:
    """They live on the same device and differ only by description key."""
    sensors = _sensors(schema_one_snapshot())
    unique_ids = {sensor.unique_id for sensor in sensors.values()}

    assert len(unique_ids) == len(sensors)
    for unique_id in unique_ids:
        assert schema_one_snapshot().serial_number.lower() in unique_id


# ---------------------------------------------------------------------------
# Conformance annotations
# ---------------------------------------------------------------------------


def test_the_two_live_estimates_are_exempt_as_schema_1_only() -> None:
    """Pinned here as well as in the conformance suite, because the reason is
    specific to this capability: no flat panel publishes it, so the producible
    gate cannot be satisfied and the descriptions must stay derived."""
    assert (
        RESIDUAL_EXEMPT_PATHS["panel.shed_time_to_priority_shed_min"]
        is Producibility.SCHEMA_1_ONLY
    )
    assert (
        RESIDUAL_EXEMPT_PATHS["panel.shed_total_time_remaining_min"]
        is Producibility.SCHEMA_1_ONLY
    )


def test_the_three_refinements_are_exempt_as_neither() -> None:
    """No adapter maps them, by design — they qualify the two estimates rather
    than being readings of their own."""
    for path in (
        "panel.shed_full_charge_time_to_priority_shed_min",
        "panel.shed_full_charge_total_time_remaining_min",
        "panel.shed_forecast_confidence",
    ):
        assert RESIDUAL_EXEMPT_PATHS[path] is Producibility.NEITHER
