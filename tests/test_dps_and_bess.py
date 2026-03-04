"""Tests for DPS select entity and BESS connected binary sensor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from span_panel_api import SpanBatterySnapshot
from span_panel_api.exceptions import SpanPanelServerError

from tests.factories import SpanPanelSnapshotFactory


# ---------------------------------------------------------------------------
# BESS Connected Binary Sensor
# ---------------------------------------------------------------------------


class TestBessConnectedBinarySensor:
    """Tests for the bess_connected binary sensor."""

    def test_bess_connected_true(self) -> None:
        """Connected=True → is_on=True."""
        from custom_components.span_panel.binary_sensor import BESS_CONNECTED_SENSOR

        snapshot = SpanPanelSnapshotFactory.create(
            battery=SpanBatterySnapshot(soe_percentage=85.0, connected=True),
        )
        assert BESS_CONNECTED_SENSOR.value_fn(snapshot) is True

    def test_bess_connected_false(self) -> None:
        """Connected=False → is_on=False."""
        from custom_components.span_panel.binary_sensor import BESS_CONNECTED_SENSOR

        snapshot = SpanPanelSnapshotFactory.create(
            battery=SpanBatterySnapshot(soe_percentage=85.0, connected=False),
        )
        assert BESS_CONNECTED_SENSOR.value_fn(snapshot) is False

    def test_bess_connected_none(self) -> None:
        """Connected=None → is_on=None."""
        from custom_components.span_panel.binary_sensor import BESS_CONNECTED_SENSOR

        snapshot = SpanPanelSnapshotFactory.create(
            battery=SpanBatterySnapshot(soe_percentage=85.0, connected=None),
        )
        assert BESS_CONNECTED_SENSOR.value_fn(snapshot) is None

    def test_bess_sensor_not_created_without_bess(self) -> None:
        """No BESS (soe_percentage=None) → has_bess returns False."""
        from custom_components.span_panel.sensors.factory import has_bess

        snapshot = SpanPanelSnapshotFactory.create(
            battery=SpanBatterySnapshot(),
        )
        assert not has_bess(snapshot)

    def test_bess_sensor_created_with_bess(self) -> None:
        """BESS present (soe_percentage set) → has_bess returns True."""
        from custom_components.span_panel.sensors.factory import has_bess

        snapshot = SpanPanelSnapshotFactory.create(
            battery=SpanBatterySnapshot(soe_percentage=85.0, connected=True),
        )
        assert has_bess(snapshot)


# ---------------------------------------------------------------------------
# DPS Select Entity
# ---------------------------------------------------------------------------


def _make_dps_coordinator(
    dominant_power_source: str | None = "GRID",
) -> MagicMock:
    """Build a mock coordinator for DPS select tests."""
    snapshot = SpanPanelSnapshotFactory.create(
        dominant_power_source=dominant_power_source,
    )

    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {}
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


class TestDPSSelect:
    """Tests for the dominant power source select entity."""

    def test_dps_options(self) -> None:
        """Select has the expected options."""
        from custom_components.span_panel.select import DPS_OPTIONS

        assert DPS_OPTIONS == ["GRID", "BATTERY", "GENERATOR", "PV"]

    def test_dps_select_init_current_option(self) -> None:
        """Select reads current DPS from snapshot."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator(dominant_power_source="BATTERY")
        select = SpanPanelDPSSelect(coordinator)
        assert select._attr_current_option == "BATTERY"

    def test_dps_select_init_defaults_to_grid(self) -> None:
        """Select defaults to GRID when snapshot has no DPS."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator(dominant_power_source=None)
        # dominant_power_source=None in factory passes through as None
        coordinator.data = SpanPanelSnapshotFactory.create(dominant_power_source=None)
        select = SpanPanelDPSSelect(coordinator)
        assert select._attr_current_option == "GRID"

    @pytest.mark.asyncio
    async def test_dps_select_set_option(self) -> None:
        """Setting an option calls set_dominant_power_source on the client."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator()
        select = SpanPanelDPSSelect(coordinator)
        select.hass = MagicMock()

        coordinator.client = AsyncMock()
        coordinator.client.set_dominant_power_source = AsyncMock()

        await select.async_select_option("BATTERY")

        coordinator.client.set_dominant_power_source.assert_called_once_with("BATTERY")
        coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_dps_select_simulation_mode(self) -> None:
        """Simulation mode (no set_dominant_power_source method) logs warning."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator()
        select = SpanPanelDPSSelect(coordinator)
        select.hass = MagicMock()

        # Simulation client without set_dominant_power_source
        coordinator.client = MagicMock(spec=[])

        await select.async_select_option("BATTERY")

        # Should not raise, just log
        coordinator.async_request_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_dps_select_server_error(self) -> None:
        """SpanPanelServerError triggers a notification."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator()

        with patch(
            "custom_components.span_panel.select.async_create_span_notification",
            new_callable=AsyncMock,
        ) as mock_notification:
            select = SpanPanelDPSSelect(coordinator)
            select.hass = MagicMock()

            coordinator.client = AsyncMock()
            coordinator.client.set_dominant_power_source = AsyncMock(
                side_effect=SpanPanelServerError("test error")
            )

            await select.async_select_option("BATTERY")

            mock_notification.assert_called_once()

    def test_dps_select_coordinator_update(self) -> None:
        """Coordinator update updates current_option from snapshot."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator(dominant_power_source="GRID")
        select = SpanPanelDPSSelect(coordinator)
        select.hass = MagicMock()
        assert select._attr_current_option == "GRID"

        # Simulate coordinator update with new DPS
        coordinator.data = SpanPanelSnapshotFactory.create(
            dominant_power_source="BATTERY",
        )
        with patch.object(select, "async_write_ha_state"):
            select._handle_coordinator_update()
        assert select._attr_current_option == "BATTERY"

    def test_dps_select_coordinator_update_ignores_unknown(self) -> None:
        """Coordinator update ignores DPS values not in options list."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator(dominant_power_source="GRID")
        select = SpanPanelDPSSelect(coordinator)
        select.hass = MagicMock()

        # Simulate coordinator update with unexpected DPS value
        coordinator.data = SpanPanelSnapshotFactory.create(
            dominant_power_source="UNKNOWN",
        )
        with patch.object(select, "async_write_ha_state"):
            select._handle_coordinator_update()
        # Should keep previous value
        assert select._attr_current_option == "GRID"

    def test_dps_select_unique_id(self) -> None:
        """Unique ID follows expected pattern."""
        from custom_components.span_panel.select import SpanPanelDPSSelect

        coordinator = _make_dps_coordinator()
        select = SpanPanelDPSSelect(coordinator)
        assert select._attr_unique_id == "span_sp3-242424-001_dominant_power_source"
