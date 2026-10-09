"""Circuits that share a meter or a relay name each other, are monitored once, and are never summed.

The library reports each circuit's `meter_shared_with` and `relay_shared_with`
peers, `None` where a circuit declares none and `()` where it names no circuit
the panel has. Every member keeps its own entities, as the panel publishes
them. Here each member's power sensor and switch name the peers, the current
monitor judges a shared meter once against its members' summed ratings, and
the topology and diagnostics carry the groups. No reading is ever added up:
every member already reports the one meter's values.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.current_monitor import monitored_circuits
from custom_components.span_panel.diagnostics import async_get_config_entry_diagnostics
from custom_components.span_panel.helpers import (
    construct_shared_with_attribute,
    construct_shared_with_attributes,
)
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS
from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .test_current_monitor import _make_hass, _make_monitor, _make_options
from .test_websocket import (
    _handle_panel_topology_inner,
    _make_coordinator,
    _make_mock_connection,
    _register_panel_device,
)

SERIAL: Final = "sp3-shared-001"
POWER: Final = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")


def _member(
    circuit_id: str,
    name: str,
    tab: int,
    rating: float,
    peers: tuple[str, ...] | None,
    *,
    current_a: float = 12.0,
) -> SpanCircuitSnapshot:
    """One circuit on a shared meter and relay, at the same breaker space as its peers."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id,
        name=name,
        tabs=[tab],
        breaker_rating_a=rating,
        current_a=current_a,
    )
    return replace(circuit, meter_shared_with=peers, relay_shared_with=peers)


def _panel(*circuits: SpanCircuitSnapshot) -> SpanPanelSnapshot:
    return SpanPanelSnapshotFactory.create(
        serial_number=SERIAL, circuits={c.circuit_id: c for c in circuits}
    )


# Two groups as a panel publishes them: 15 + 15 A at space 44, 15 + 10 A at space
# 48, each member naming the other; and a circuit that shares nothing.
PAIR_A: Final = (
    _member("circuit-52", "Bath Fan", 44, 15.0, ("circuit-53",)),
    _member("circuit-53", "", 44, 15.0, ("circuit-52",)),
)
PAIR_B: Final = (
    _member("circuit-56", "Porch", 48, 15.0, ("circuit-57",)),
    _member("circuit-57", "Shed", 48, 10.0, ("circuit-56",)),
)
ALONE: Final = SpanCircuitSnapshotFactory.create(
    circuit_id="circuit-1", name="Kitchen", tabs=[1], breaker_rating_a=20.0, current_a=3.0
)
SNAPSHOT: Final = _panel(*PAIR_A, *PAIR_B, ALONE)


def test_a_nameless_peer_is_named_by_its_position() -> None:
    assert construct_shared_with_attribute(SNAPSHOT, ("circuit-53",)) == ["Circuit 44"]
    assert construct_shared_with_attribute(SNAPSHOT, ("circuit-52",)) == ["Bath Fan"]


def test_attributes_name_the_peers_only_where_the_circuit_says_whom_it_shares_with() -> None:
    assert construct_shared_with_attributes(SNAPSHOT, SNAPSHOT.circuits["circuit-56"]) == {
        "meter_shared_with": ["Shed"],
        "relay_shared_with": ["Shed"],
    }
    assert construct_shared_with_attributes(SNAPSHOT, ALONE) == {}

    declared_alone = replace(ALONE, meter_shared_with=(), relay_shared_with=None)
    assert construct_shared_with_attributes(SNAPSHOT, declared_alone) == {"meter_shared_with": []}


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry.options = {}
    coordinator.config_entry.data = {}
    coordinator.config_entry.title = "SPAN Panel"
    return coordinator


def test_each_members_power_sensor_and_switch_carry_its_peers() -> None:
    coordinator = _coordinator(SNAPSHOT)
    sensor = SpanCircuitPowerSensor(coordinator, POWER, SNAPSHOT, "circuit-57")
    switch = SpanPanelCircuitsSwitch(coordinator, "circuit-57", "SPAN Panel")

    for attributes in (sensor.extra_state_attributes, switch.extra_state_attributes):
        assert attributes is not None
        assert attributes["meter_shared_with"] == ["Porch"]
        assert attributes["relay_shared_with"] == ["Porch"]

    lone = SpanCircuitPowerSensor(coordinator, POWER, SNAPSHOT, "circuit-1").extra_state_attributes
    assert lone is not None
    assert "meter_shared_with" not in lone
    assert "relay_shared_with" not in lone


def test_a_shared_meter_is_one_monitored_point_rated_by_its_members_sum() -> None:
    points = monitored_circuits(SNAPSHOT)

    assert points["circuit-52"] is points["circuit-53"]
    assert (points["circuit-52"].point_id, points["circuit-52"].rating_a) == ("circuit-52", 30.0)
    assert (points["circuit-57"].point_id, points["circuit-57"].rating_a) == ("circuit-56", 25.0)
    assert points["circuit-56"].basis == "group"
    assert (points["circuit-1"].basis, points["circuit-1"].rating_a) == ("circuit", 20.0)


def test_a_group_with_an_unrated_member_has_no_rating() -> None:
    """A partial sum would understate the group, so it is not judged at all."""
    unrated = _panel(PAIR_A[0], replace(PAIR_A[1], breaker_rating_a=None))

    assert monitored_circuits(unrated)["circuit-52"].rating_a is None


def test_the_monitor_alerts_on_the_group_under_its_first_member() -> None:
    """28 A on one shared meter: 93 % of the pair's 30 A, not 187 % of each member's 15 A."""
    hass = _make_hass()
    monitor = _make_monitor(hass, _make_options(spike_threshold_pct=100))
    snapshot = _panel(
        replace(PAIR_A[0], current_a=28.0),
        replace(PAIR_A[1], current_a=28.0),
    )

    monitor.process_snapshot(snapshot)

    assert monitor.get_circuit_state("circuit-53") is None
    state = monitor.get_circuit_state("circuit-52")
    assert state is not None and state.last_current_a == 28.0
    assert state.last_spike_alert is None

    with patch.object(monitor, "_resolve_circuit_entity_id", side_effect=lambda cid: cid):
        status = monitor.get_monitoring_status()["circuits"]
    rows = [status["circuit-52"], status["circuit-53"]]
    assert all(row["basis"] == "group" for row in rows)
    assert all(row["breaker_rating_a"] == 30.0 for row in rows)
    assert all(row["utilization_pct"] == pytest.approx(93.3) for row in rows)


@pytest.mark.asyncio
async def test_topology_carries_both_group_keys(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="span_entry", unique_id=SERIAL)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=_make_coordinator(SNAPSHOT),
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SNAPSHOT),
        setup_snapshot=SNAPSHOT,
    )
    device = _register_panel_device(hass, "span_entry", serial=SERIAL)
    connection = _make_mock_connection()

    await _handle_panel_topology_inner(
        hass, connection, {"id": 1, "type": "span_panel/panel_topology", "device_id": device.id}
    )
    rows = connection.send_result.call_args[0][1]["circuits"]

    for circuit_id, key in (
        ("circuit-52", "circuit-52"),
        ("circuit-53", "circuit-52"),
        ("circuit-56", "circuit-56"),
        ("circuit-57", "circuit-56"),
        ("circuit-1", None),
    ):
        assert rows[circuit_id]["shared_meter_group"] == key, circuit_id
        assert rows[circuit_id]["shared_relay_group"] == key, circuit_id


@pytest.mark.asyncio
async def test_diagnostics_list_one_row_per_group(hass: HomeAssistant) -> None:
    coordinator = MagicMock()
    coordinator.data = SNAPSHOT
    coordinator.panel_offline = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    entry = MockConfigEntry(domain=DOMAIN, title="SPAN Panel", unique_id=SERIAL)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SNAPSHOT),
        setup_snapshot=SNAPSHOT,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    groups = {
        "circuit-52": ("circuit-52", "circuit-53"),
        "circuit-56": ("circuit-56", "circuit-57"),
    }
    assert result["shared_meter_groups"] == groups
    assert result["shared_relay_groups"] == groups
