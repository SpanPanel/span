"""Tests for the SpanPanel class."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from span_panel_api import PanelGeneration, SpanCircuitSnapshot, SpanPanelSnapshot

from custom_components.span_panel.exceptions import SpanPanelReturnedEmptyData
from custom_components.span_panel.options import Options
from custom_components.span_panel.span_panel import SpanPanel
from custom_components.span_panel.span_panel_api import SpanPanelApi
from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit
from custom_components.span_panel.span_panel_data import SpanPanelData
from custom_components.span_panel.span_panel_hardware_status import (
    SpanPanelHardwareStatus,
)
from custom_components.span_panel.span_panel_storage_battery import (
    SpanPanelStorageBattery,
)


@pytest.fixture
def mock_options():
    """Create mock options."""
    options = MagicMock(spec=Options)
    options.enable_battery_percentage = True
    options.enable_solar_sensors = False
    options.inverter_leg1 = 0
    options.inverter_leg2 = 0
    options.api_retries = 3
    options.api_retry_timeout = 5.0
    options.api_retry_backoff_multiplier = 2.0
    return options


@pytest.fixture
def mock_status():
    """Create mock hardware status."""
    return MagicMock(spec=SpanPanelHardwareStatus)


@pytest.fixture
def mock_panel_data():
    """Create mock panel data."""
    return MagicMock(spec=SpanPanelData)


@pytest.fixture
def mock_circuits():
    """Create mock circuits."""
    return {"circuit_1": MagicMock(spec=SpanPanelCircuit)}


@pytest.fixture
def mock_battery():
    """Create mock storage battery."""
    return MagicMock(spec=SpanPanelStorageBattery)


@pytest.fixture
def minimal_snapshot():
    """Create a minimal SpanPanelSnapshot for update() tests."""
    return SpanPanelSnapshot(
        panel_generation=PanelGeneration.GEN2,
        serial_number="SPAN123",
        firmware_version="1.2.3",
        main_power_w=500.0,
        main_relay_state="CLOSED",
        grid_power_w=500.0,
        dsm_state="DSM_GRID_OK",
        dsm_grid_state="DSM_ON_GRID",
        current_run_config="PANEL_ON_GRID",
        battery_soe=75.0,
        feedthrough_power_w=0.0,
        hardware_door_state="CLOSED",
        hardware_uptime=12345,
        hardware_is_ethernet_connected=True,
        hardware_is_wifi_connected=False,
        hardware_is_cellular_connected=False,
        hardware_update_status="UP_TO_DATE",
        hardware_env="prod",
        hardware_manufacturer="Span",
        hardware_model="MAIN40",
        hardware_proximity_proven=True,
        circuits={
            "circuit_1": SpanCircuitSnapshot(
                circuit_id="circuit_1",
                name="Washer",
                power_w=200.0,
                voltage_v=120.0,
                current_a=1.67,
                is_on=True,
                relay_state="CLOSED",
                priority="MUST_HAVE",
                tabs=[1],
                energy_produced_wh=0.0,
                energy_consumed_wh=5000.0,
            )
        },
    )


class TestSpanPanelInit:
    """Test SpanPanel initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        panel = SpanPanel("192.168.1.100")

        assert panel.host == "192.168.1.100"
        assert panel.options is None
        assert isinstance(panel.api, SpanPanelApi)
        assert panel._status is None
        assert panel._panel is None
        assert panel._circuits == {}
        assert panel._storage_battery is None

    def test_init_with_access_token(self):
        """Test initialization with access token."""
        panel = SpanPanel("192.168.1.100", access_token="test_token")

        assert panel.host == "192.168.1.100"
        assert panel.api.access_token == "test_token"

    def test_init_with_options(self, mock_options):
        """Test initialization with options."""
        panel = SpanPanel("192.168.1.100", options=mock_options)

        assert panel.host == "192.168.1.100"
        assert panel.options is mock_options
        assert panel._options is mock_options

    def test_init_with_ssl(self):
        """Test initialization with SSL enabled."""
        panel = SpanPanel("192.168.1.100", use_ssl=True)

        assert panel.host == "192.168.1.100"
        # SSL parameter is passed to the API


class TestSpanPanelProperties:
    """Test SpanPanel properties."""

    def test_host_property(self):
        """Test host property returns API host."""
        panel = SpanPanel("192.168.1.100")
        assert panel.host == "192.168.1.100"

    def test_options_property(self, mock_options):
        """Test options property returns options atomically."""
        panel = SpanPanel("192.168.1.100", options=mock_options)
        assert panel.options is mock_options

    def test_status_property_with_data(self, mock_status):
        """Test status property when data is available."""
        panel = SpanPanel("192.168.1.100")
        panel._status = mock_status

        assert panel.status is mock_status

    def test_status_property_without_data(self):
        """Test status property when data is not available."""
        panel = SpanPanel("192.168.1.100")

        with pytest.raises(RuntimeError, match="Hardware status not available"):
            _ = panel.status

    def test_panel_property_with_data(self, mock_panel_data):
        """Test panel property when data is available."""
        panel = SpanPanel("192.168.1.100")
        panel._panel = mock_panel_data

        assert panel.panel is mock_panel_data

    def test_panel_property_without_data(self):
        """Test panel property when data is not available."""
        panel = SpanPanel("192.168.1.100")

        with pytest.raises(RuntimeError, match="Panel data not available"):
            _ = panel.panel

    def test_circuits_property(self, mock_circuits):
        """Test circuits property returns circuits atomically."""
        panel = SpanPanel("192.168.1.100")
        panel._circuits = mock_circuits

        assert panel.circuits is mock_circuits

    def test_storage_battery_property_with_data(self, mock_battery):
        """Test storage_battery property when data is available."""
        panel = SpanPanel("192.168.1.100")
        panel._storage_battery = mock_battery

        assert panel.storage_battery is mock_battery

    def test_storage_battery_property_without_data(self):
        """Test storage_battery property when data is not available."""
        panel = SpanPanel("192.168.1.100")

        with pytest.raises(RuntimeError, match="Storage battery not available"):
            _ = panel.storage_battery


class TestSpanPanelUpdate:
    """Test SpanPanel update functionality."""

    @pytest.mark.asyncio
    async def test_update_success_without_battery(self, minimal_snapshot):
        """Test successful update without battery data (battery option disabled)."""
        panel = SpanPanel("192.168.1.100")  # no options → battery disabled

        panel.api.get_snapshot = AsyncMock(return_value=minimal_snapshot)

        await panel.update()

        panel.api.get_snapshot.assert_called_once()

        # Domain objects are derived from the snapshot
        assert panel._status is not None
        assert panel._status.serial_number == "SPAN123"
        assert panel._panel is not None
        assert panel._panel.instant_grid_power == 500.0
        assert "circuit_1" in panel._circuits
        assert panel._circuits["circuit_1"].instant_power == 200.0
        assert panel._storage_battery is None  # battery option not enabled

    @pytest.mark.asyncio
    async def test_update_success_with_battery(self, minimal_snapshot, mock_options):
        """Test successful update with battery data when option is enabled."""
        panel = SpanPanel("192.168.1.100", options=mock_options)  # battery enabled

        panel.api.get_snapshot = AsyncMock(return_value=minimal_snapshot)

        await panel.update()

        panel.api.get_snapshot.assert_called_once()

        assert panel._storage_battery is not None
        assert panel._storage_battery.storage_battery_percentage == 75  # from battery_soe=75.0

    @pytest.mark.asyncio
    async def test_update_with_battery_disabled(self, minimal_snapshot):
        """Test update when battery is disabled in options."""
        options = MagicMock(spec=Options)
        options.enable_battery_percentage = False
        options.api_retries = 3
        options.api_retry_timeout = 5.0
        options.api_retry_backoff_multiplier = 2.0
        panel = SpanPanel("192.168.1.100", options=options)

        panel.api.get_snapshot = AsyncMock(return_value=minimal_snapshot)

        await panel.update()

        panel.api.get_snapshot.assert_called_once()
        assert panel._storage_battery is None

    @pytest.mark.asyncio
    async def test_update_handles_empty_data_exception(self):
        """Test update handles SpanPanelReturnedEmptyData exception."""
        panel = SpanPanel("192.168.1.100")

        panel.api.get_snapshot = AsyncMock(side_effect=SpanPanelReturnedEmptyData("Empty data"))

        # Should not raise exception, just log warning
        await panel.update()

        # Data should not be updated due to exception
        assert panel._status is None
        assert panel._panel is None
        assert panel._circuits == {}

    @pytest.mark.asyncio
    async def test_update_propagates_other_exceptions(self):
        """Test update propagates non-empty-data exceptions."""
        panel = SpanPanel("192.168.1.100")

        panel.api.get_snapshot = AsyncMock(side_effect=Exception("API error"))

        with pytest.raises(Exception, match="API error"):
            await panel.update()


class TestSpanPanelAtomicUpdates:
    """Test SpanPanel atomic update methods."""

    def test_update_status(self, mock_status):
        """Test atomic status update."""
        panel = SpanPanel("192.168.1.100")

        panel._update_status(mock_status)

        assert panel._status is mock_status

    def test_update_panel(self, mock_panel_data):
        """Test atomic panel update."""
        panel = SpanPanel("192.168.1.100")

        panel._update_panel(mock_panel_data)

        assert panel._panel is mock_panel_data

    def test_update_circuits(self, mock_circuits):
        """Test atomic circuits update."""
        panel = SpanPanel("192.168.1.100")

        panel._update_circuits(mock_circuits)

        assert panel._circuits is mock_circuits

    def test_update_storage_battery(self, mock_battery):
        """Test atomic storage battery update."""
        panel = SpanPanel("192.168.1.100")

        panel._update_storage_battery(mock_battery)

        assert panel._storage_battery is mock_battery


class TestSpanPanelClose:
    """Test SpanPanel close functionality."""

    @pytest.mark.asyncio
    async def test_close(self):
        """Test close method calls API close."""
        panel = SpanPanel("192.168.1.100")
        panel.api.close = AsyncMock()

        await panel.close()

        panel.api.close.assert_called_once()
