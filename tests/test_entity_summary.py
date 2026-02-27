"""Tests for entity_summary module.

This module tests the entity summary logging functionality.
"""

import logging
from unittest.mock import MagicMock

import pytest
from span_panel_api import SpanPanelSnapshot

from tests.factories import (
    SpanBatterySnapshotFactory,
    SpanCircuitSnapshotFactory,
    SpanPanelSnapshotFactory,
)


def _make_snapshot_with_circuits(
    count: int,
    controllable_count: int | None = None,
    battery_soe: float | None = None,
    power_flow_battery: float | None = None,
) -> SpanPanelSnapshot:
    """Create a snapshot with the given number of circuits.

    Args:
        count: Total number of circuits.
        controllable_count: How many are user-controllable (defaults to all).
        battery_soe: Battery state of energy percentage (None = no BESS).
        power_flow_battery: Battery power flow value (None = no BESS).

    """
    if controllable_count is None:
        controllable_count = count

    circuits = {}
    for i in range(1, count + 1):
        circuits[f"circuit_{i}"] = SpanCircuitSnapshotFactory.create(
            circuit_id=f"circuit_{i}",
            name=f"Circuit {i}",
            is_user_controllable=(i <= controllable_count),
        )

    battery = SpanBatterySnapshotFactory.create(soe_percentage=battery_soe)
    return SpanPanelSnapshotFactory.create(
        circuits=circuits,
        battery=battery,
        power_flow_battery=power_flow_battery,
    )


def _make_mock_coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    """Create a mock coordinator whose .data is the given snapshot."""
    coordinator = MagicMock()
    coordinator.data = snapshot
    return coordinator


class TestLogEntitySummary:
    """Test the log_entity_summary function."""

    @pytest.fixture
    def mock_coordinator(self) -> MagicMock:
        """Create a mock coordinator with span panel data."""
        snapshot = _make_snapshot_with_circuits(count=10, controllable_count=8)
        return _make_mock_coordinator(snapshot)

    def test_log_entity_summary_debug_level(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary logging at debug level."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator)

            assert "=== SPAN PANEL ENTITY SUMMARY ===" in caplog.text
            assert "Total circuits: 10 (8 controllable, 2 non-controllable)" in caplog.text
            assert "=== NATIVE SENSORS ===" in caplog.text
            assert "=== SYNTHETIC SENSORS (Template-based) ===" in caplog.text
            assert "=== END ENTITY SUMMARY ===" in caplog.text

    def test_log_entity_summary_info_level(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary logging at info level."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator)

            assert "=== SPAN PANEL ENTITY SUMMARY ===" in caplog.text
            assert "Total circuits: 10" in caplog.text

    def test_log_entity_summary_no_logging(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that nothing is logged when logging is disabled."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.WARNING, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator)

            assert "=== SPAN PANEL ENTITY SUMMARY ===" not in caplog.text

    def test_log_entity_summary_with_bess(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary when BESS is commissioned (both power and SoE)."""
        snapshot = _make_snapshot_with_circuits(
            count=10, controllable_count=8, battery_soe=85.0, power_flow_battery=500.0
        )
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator)

            assert "Battery sensors: 2 (BESS detected)" in caplog.text

    def test_log_entity_summary_with_battery_power_but_no_soe(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary when battery power is published but no SoE.

        Panels without a commissioned BESS still publish battery=0.0 in power-flows.
        Only soe_percentage is a reliable BESS signal.
        """
        snapshot = _make_snapshot_with_circuits(
            count=10, controllable_count=8, battery_soe=None, power_flow_battery=500.0
        )
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator)

            assert "Battery sensors: 0 (no BESS)" in caplog.text

    def test_log_entity_summary_no_bess(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary when no BESS is present."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator)

            assert "Battery sensors: 0 (no BESS)" in caplog.text

    def test_log_entity_summary_non_controllable_circuits(
        self, mock_coordinator: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that non-controllable circuits are properly identified and logged."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator)

            assert "Non-controllable circuits:" in caplog.text
            assert "Circuit 9 (ID: circuit_9)" in caplog.text
            assert "Circuit 10 (ID: circuit_10)" in caplog.text

    def test_log_entity_summary_all_controllable_circuits(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary when all circuits are controllable."""
        snapshot = _make_snapshot_with_circuits(count=5, controllable_count=5)
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(coordinator)

            assert "Non-controllable circuits: None" in caplog.text

    def test_log_entity_summary_no_circuits(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary with no circuits."""
        snapshot = _make_snapshot_with_circuits(count=0)
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator)

            assert "Total circuits: 0 (0 controllable, 0 non-controllable)" in caplog.text
            assert "Circuit synthetic sensors: 0" in caplog.text

    def test_log_entity_summary_sensor_counts(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that sensor counts are calculated correctly."""
        snapshot = _make_snapshot_with_circuits(
            count=10, controllable_count=8, battery_soe=85.0, power_flow_battery=500.0
        )
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator)

            # With 10 circuits, BESS present with SoE
            # Unmapped sensors: 10 circuits * 3 sensors per circuit = 30
            assert "Unmapped circuit sensors: 30" in caplog.text

            # Circuit synthetic sensors: 10 circuits * 3 sensors = 30
            assert "Circuit synthetic sensors: 30" in caplog.text

            # Panel synthetic sensors: 6 (fixed)
            assert "Panel synthetic sensors: 6" in caplog.text

            # Battery sensors: 2 (power + level when SoE present)
            assert "Battery sensors: 2" in caplog.text

            # Circuit switches: 8 (only controllable circuits)
            assert "Circuit switches: 8 (controllable circuits only)" in caplog.text

            # Circuit selects: 8 (only controllable circuits)
            assert "Circuit selects: 8 (controllable circuits only)" in caplog.text

    def test_log_entity_summary_total_entity_calculation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that total entity count is calculated correctly."""
        snapshot = _make_snapshot_with_circuits(
            count=10, controllable_count=8, battery_soe=85.0, power_flow_battery=500.0
        )
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator)

            assert "Total entities:" in caplog.text
            assert "sensors +" in caplog.text
            assert "switches +" in caplog.text
            assert "selects =" in caplog.text


class TestEntitySummaryEdgeCases:
    """Test edge cases for entity summary."""

    def test_log_entity_summary_no_bess_no_circuits(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test entity summary with no BESS and no circuits."""
        snapshot = _make_snapshot_with_circuits(count=0)
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator)

            assert "Battery sensors: 0 (no BESS)" in caplog.text
