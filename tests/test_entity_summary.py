"""Tests for entity_summary module.

This module tests the entity summary logging functionality.
"""

import logging
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntry
import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.options import BATTERY_ENABLE
from tests.factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory


def _make_snapshot_with_circuits(
    count: int,
    controllable_count: int | None = None,
) -> SpanPanelSnapshot:
    """Create a snapshot with the given number of circuits.

    Args:
        count: Total number of circuits.
        controllable_count: How many are user-controllable (defaults to all).

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
    return SpanPanelSnapshotFactory.create(circuits=circuits)


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

    @pytest.fixture
    def mock_config_entry(self) -> MagicMock:
        """Create a mock config entry."""
        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {}
        return config_entry

    @pytest.fixture
    def mock_config_entry_with_battery(self) -> MagicMock:
        """Create a mock config entry with battery enabled."""
        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {BATTERY_ENABLE: True}
        return config_entry

    def test_log_entity_summary_debug_level(
        self, mock_coordinator: MagicMock, mock_config_entry: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary logging at debug level."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            assert "=== SPAN PANEL ENTITY SUMMARY ===" in caplog.text
            assert "Total circuits: 10 (8 controllable, 2 non-controllable)" in caplog.text
            assert "=== NATIVE SENSORS ===" in caplog.text
            assert "=== SYNTHETIC SENSORS (Template-based) ===" in caplog.text
            assert "=== END ENTITY SUMMARY ===" in caplog.text

    def test_log_entity_summary_info_level(
        self, mock_coordinator: MagicMock, mock_config_entry: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary logging at info level."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            assert "=== SPAN PANEL ENTITY SUMMARY ===" in caplog.text
            assert "Total circuits: 10" in caplog.text

    def test_log_entity_summary_no_logging(
        self, mock_coordinator: MagicMock, mock_config_entry: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that nothing is logged when logging is disabled."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.WARNING, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            assert "=== SPAN PANEL ENTITY SUMMARY ===" not in caplog.text

    def test_log_entity_summary_with_battery_enabled(
        self, mock_coordinator: MagicMock, mock_config_entry_with_battery: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary with battery enabled."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry_with_battery)

            assert "Battery sensors: 1 (native sensor)" in caplog.text

    def test_log_entity_summary_with_battery_disabled(
        self, mock_coordinator: MagicMock, mock_config_entry: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary with battery disabled."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            assert "Battery sensors: 0 (battery disabled)" in caplog.text

    def test_log_entity_summary_non_controllable_circuits(
        self, mock_coordinator: MagicMock, mock_config_entry: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that non-controllable circuits are properly identified and logged."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry)

            assert "Non-controllable circuits:" in caplog.text
            assert "Circuit 9 (ID: circuit_9)" in caplog.text
            assert "Circuit 10 (ID: circuit_10)" in caplog.text

    def test_log_entity_summary_all_controllable_circuits(
        self, mock_config_entry: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary when all circuits are controllable."""
        snapshot = _make_snapshot_with_circuits(count=5, controllable_count=5)
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.INFO, logger="custom_components.span_panel"):
            log_entity_summary(coordinator, mock_config_entry)

            assert "Non-controllable circuits: None" in caplog.text

    def test_log_entity_summary_no_circuits(
        self, mock_config_entry: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test entity summary with no circuits."""
        snapshot = _make_snapshot_with_circuits(count=0)
        coordinator = _make_mock_coordinator(snapshot)

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator, mock_config_entry)

            assert "Total circuits: 0 (0 controllable, 0 non-controllable)" in caplog.text
            assert "Circuit synthetic sensors: 0" in caplog.text

    def test_log_entity_summary_sensor_counts(
        self, mock_coordinator: MagicMock, mock_config_entry_with_battery: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that sensor counts are calculated correctly."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry_with_battery)

            # With 10 circuits, battery enabled
            # Unmapped sensors: 10 circuits * 3 sensors per circuit = 30
            assert "Unmapped circuit sensors: 30" in caplog.text

            # Circuit synthetic sensors: 10 circuits * 3 sensors = 30
            assert "Circuit synthetic sensors: 30" in caplog.text

            # Panel synthetic sensors: 6 (fixed)
            assert "Panel synthetic sensors: 6" in caplog.text

            # Battery sensors: 1 (when enabled)
            assert "Battery sensors: 1" in caplog.text

            # Circuit switches: 8 (only controllable circuits)
            assert "Circuit switches: 8 (controllable circuits only)" in caplog.text

            # Circuit selects: 8 (only controllable circuits)
            assert "Circuit selects: 8 (controllable circuits only)" in caplog.text

    def test_log_entity_summary_total_entity_calculation(
        self, mock_coordinator: MagicMock, mock_config_entry_with_battery: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that total entity count is calculated correctly."""
        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(mock_coordinator, mock_config_entry_with_battery)

            assert "Total entities:" in caplog.text
            assert "sensors +" in caplog.text
            assert "switches +" in caplog.text
            assert "selects =" in caplog.text


class TestEntitySummaryEdgeCases:
    """Test edge cases for entity summary."""

    def test_log_entity_summary_with_empty_options(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test entity summary when config entry options is empty."""
        snapshot = _make_snapshot_with_circuits(count=0)
        coordinator = _make_mock_coordinator(snapshot)

        config_entry = MagicMock(spec=ConfigEntry)
        config_entry.options = {}

        from custom_components.span_panel.entity_summary import log_entity_summary

        with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel"):
            log_entity_summary(coordinator, config_entry)

            assert "Battery sensors: 0 (battery disabled)" in caplog.text
