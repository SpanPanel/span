"""Panel and circuit readings this firmware can publish become sensors and attributes only where declared.

Busbar current, line frequency and the rating of the protection ahead of the
upstream lugs are panel sensors created only where the adapter's field
metadata holds a resolved row for them, which it does exactly where the panel
declares the property. The EVSE's advertised current follows the same rule,
and keeps today's behaviour where no metadata is known. A circuit's declared
nominal voltage is preferred over the pole-count inference, its breaker's
protection functions become an attribute, and the current monitor judges the
mains against the upstream protection where the panel has no main breaker.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import FieldMetadata, SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.current_monitor import panel_limit_a
from custom_components.span_panel.helpers import construct_voltage_attribute
from custom_components.span_panel.sensor import create_evse_sensors, create_panel_sensors
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS

from .factories import (
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
    pv_binding_for,
)
from .test_current_monitor import _make_hass, _make_monitor

READINGS: Final = {
    "panel.busbar_current_a": "busbar_current",
    "panel.frequency_hz": "frequency",
    "panel.upstream_protection_rating_a": "upstream_protection_rating",
}
ADVERTISED: Final = "evse.advertised_current_a"
POWER: Final = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")


def _coordinator(
    snapshot: SpanPanelSnapshot, metadata: dict[str, FieldMetadata] | None
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = MagicMock()
    coordinator.client.field_metadata = metadata
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN, data={}, title="SPAN Panel", options={}, unique_id=snapshot.serial_number
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    return coordinator


def _panel_keys(metadata: dict[str, FieldMetadata] | None) -> set[str]:
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator = _coordinator(snapshot, metadata)
    return {
        entity.entity_description.key
        for entity in create_panel_sensors(coordinator, snapshot, coordinator.config_entry)
    }


def test_each_reading_is_created_only_where_declared() -> None:
    declared = {path: FieldMetadata(unit="A", datatype="float") for path in READINGS}

    assert set(READINGS.values()) <= _panel_keys(declared)
    assert not set(READINGS.values()) & _panel_keys({})
    assert not set(READINGS.values()) & _panel_keys(None)


def test_a_declared_gap_creates_no_reading() -> None:
    """A row the adapter marks unresolved is a property the panel stopped declaring."""
    unresolved = {
        path: FieldMetadata(unit=None, datatype="unknown", resolved=False) for path in READINGS
    }

    assert not set(READINGS.values()) & _panel_keys(unresolved)


@pytest.mark.parametrize(
    ("metadata", "created"),
    [
        ({ADVERTISED: FieldMetadata(unit="A", datatype="float")}, True),
        ({}, False),
        ({ADVERTISED: FieldMetadata(unit=None, datatype="unknown", resolved=False)}, False),
        (None, True),
    ],
    ids=["declared", "undeclared", "unresolved", "metadata-unknown"],
)
def test_the_advertised_current_is_created_only_where_declared(
    metadata: dict[str, FieldMetadata] | None, created: bool
) -> None:
    snapshot = SpanPanelSnapshotFactory.create(evse={"evse-0": SpanEvseSnapshotFactory.create()})
    keys = {
        entity.entity_description.key
        for entity in create_evse_sensors(_coordinator(snapshot, metadata), snapshot)
    }

    assert ("evse_advertised_current" in keys) is created
    assert "evse_lock_state" in keys


@pytest.mark.parametrize(
    ("nominal", "tabs", "expected"),
    [
        (208.0, [1, 3], 208.0),
        (120.0, [], 120.0),
        (None, [1, 3], 240),
        (None, [1], 120),
        (None, [], None),
    ],
)
def test_the_declared_nominal_voltage_comes_first(
    nominal: float | None, tabs: list[int], expected: float | None
) -> None:
    circuit = replace(SpanCircuitSnapshotFactory.create(tabs=tabs), nominal_voltage_v=nominal)

    assert construct_voltage_attribute(circuit) == expected


def test_protection_functions_are_an_attribute_only_where_listed() -> None:
    listed = replace(
        SpanCircuitSnapshotFactory.create(circuit_id="1"),
        protection_functions=("OVERCURRENT", "ARC_FAULT"),
    )
    unlisted = SpanCircuitSnapshotFactory.create(circuit_id="2")
    snapshot = SpanPanelSnapshotFactory.create(circuits={"1": listed, "2": unlisted})
    coordinator = _coordinator(snapshot, None)

    with_list = SpanCircuitPowerSensor(coordinator, POWER, snapshot, "1").extra_state_attributes
    without = SpanCircuitPowerSensor(coordinator, POWER, snapshot, "2").extra_state_attributes

    assert with_list is not None and with_list["protection_functions"] == [
        "OVERCURRENT",
        "ARC_FAULT",
    ]
    assert without is not None and "protection_functions" not in without


@pytest.mark.parametrize(
    ("main", "upstream", "expected"),
    [(200, 80, 200.0), (None, 80, 80.0), (None, 200, 200.0), (None, None, None)],
)
def test_the_mains_limit_is_the_main_breaker_else_the_upstream_protection(
    main: int | None, upstream: int | None, expected: float | None
) -> None:
    snapshot = replace(
        SpanPanelSnapshotFactory.create(main_breaker_rating_a=main),
        upstream_protection_rating_a=upstream,
    )

    assert panel_limit_a(snapshot) == expected


def test_the_monitor_judges_the_mains_against_the_upstream_protection() -> None:
    hass = _make_hass()
    monitor = _make_monitor(hass)
    snapshot = replace(
        SpanPanelSnapshotFactory.create(main_breaker_rating_a=None, upstream_l1_current_a=40.0),
        upstream_protection_rating_a=80,
    )

    monitor.process_snapshot(snapshot)

    state = monitor.get_mains_state("upstream_l1")
    assert state is not None and state.last_current_a == 40.0
    (mains,) = monitor.get_monitoring_status()["mains"].values()
    assert mains["breaker_rating_a"] == 80.0
    assert mains["utilization_pct"] == 50.0
