"""Test offline simulation functionality."""

from datetime import datetime, timedelta

from custom_components.span_panel.span_panel_api import SpanPanelApi


class TestOfflineSimulation:
    """Test offline simulation functionality."""

    def test_offline_simulation_disabled(self):
        """Test that offline simulation is disabled by default."""
        api = SpanPanelApi(
            host="test-host",
            simulation_mode=False,
            simulation_offline_minutes=0,
        )

        # Should not be offline when simulation mode is disabled
        assert not api._is_panel_offline()

    def test_offline_simulation_zero_minutes(self):
        """Test that offline simulation is disabled when minutes is 0."""
        api = SpanPanelApi(
            host="test-host",
            simulation_mode=True,
            simulation_offline_minutes=0,
            simulation_start_time=datetime.now(),
        )

        # Should not be offline when minutes is 0
        assert not api._is_panel_offline()

    def test_offline_simulation_no_start_time(self):
        """Test that offline simulation is disabled when no start time."""
        api = SpanPanelApi(
            host="test-host",
            simulation_mode=True,
            simulation_offline_minutes=5,
            simulation_start_time=None,
        )

        # Should not be offline when no start time
        assert not api._is_panel_offline()

    def test_offline_simulation_active(self):
        """Test that offline simulation is active within the time window."""
        start_time = datetime.now()
        api = SpanPanelApi(
            host="test-host",
            simulation_mode=True,
            simulation_offline_minutes=5,
            simulation_start_time=start_time,
        )

        # Should be offline immediately after start
        assert api._is_panel_offline()

    def test_offline_simulation_expired(self):
        """Test that offline simulation expires after the time window."""
        start_time = datetime.now() - timedelta(minutes=10)  # 10 minutes ago
        api = SpanPanelApi(
            host="test-host",
            simulation_mode=True,
            simulation_offline_minutes=5,
            simulation_start_time=start_time,
        )

        # Should not be offline after 10 minutes (window was 5 minutes)
        assert not api._is_panel_offline()

    def test_offline_simulation_boundary(self):
        """Test that offline simulation expires exactly at the boundary."""
        start_time = datetime.now() - timedelta(minutes=5)  # Exactly 5 minutes ago
        api = SpanPanelApi(
            host="test-host",
            simulation_mode=True,
            simulation_offline_minutes=5,
            simulation_start_time=start_time,
        )

        # Should not be offline at exactly the boundary (5 minutes)
        assert not api._is_panel_offline()
