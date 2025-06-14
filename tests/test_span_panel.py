"""Tests for the SpanPanel class."""

import pytest
from unittest.mock import AsyncMock, MagicMock

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
    async def test_update_success_without_battery(
        self, mock_status, mock_panel_data, mock_circuits
    ):
        """Test successful update without battery data."""
        panel = SpanPanel("192.168.1.100")

        # Mock API methods
        panel.api.get_status_data = AsyncMock(return_value=mock_status)
        panel.api.get_panel_data = AsyncMock(return_value=mock_panel_data)
        panel.api.get_circuits_data = AsyncMock(return_value=mock_circuits)

        await panel.update()

        # Verify API calls
        panel.api.get_status_data.assert_called_once()
        panel.api.get_panel_data.assert_called_once()
        panel.api.get_circuits_data.assert_called_once()

        # Verify data is updated
        assert panel._status is mock_status
        assert panel._panel is mock_panel_data
        assert panel._circuits is mock_circuits
        assert panel._storage_battery is None

    @pytest.mark.asyncio
    async def test_update_success_with_battery(
        self, mock_status, mock_panel_data, mock_circuits, mock_battery, mock_options
    ):
        """Test successful update with battery data."""
        panel = SpanPanel("192.168.1.100", options=mock_options)

        # Mock API methods
        panel.api.get_status_data = AsyncMock(return_value=mock_status)
        panel.api.get_panel_data = AsyncMock(return_value=mock_panel_data)
        panel.api.get_circuits_data = AsyncMock(return_value=mock_circuits)
        panel.api.get_storage_battery_data = AsyncMock(return_value=mock_battery)

        await panel.update()

        # Verify API calls
        panel.api.get_status_data.assert_called_once()
        panel.api.get_panel_data.assert_called_once()
        panel.api.get_circuits_data.assert_called_once()
        panel.api.get_storage_battery_data.assert_called_once()

        # Verify data is updated
        assert panel._status is mock_status
        assert panel._panel is mock_panel_data
        assert panel._circuits is mock_circuits
        assert panel._storage_battery is mock_battery

    @pytest.mark.asyncio
    async def test_update_with_battery_disabled(self, mock_status, mock_panel_data, mock_circuits):
        """Test update when battery is disabled in options."""
        options = MagicMock(spec=Options)
        options.enable_battery_percentage = False
        options.enable_solar_sensors = False
        options.inverter_leg1 = 0
        options.inverter_leg2 = 0
        options.api_retries = 3
        options.api_retry_timeout = 5.0
        options.api_retry_backoff_multiplier = 2.0
        panel = SpanPanel("192.168.1.100", options=options)

        # Mock API methods
        panel.api.get_status_data = AsyncMock(return_value=mock_status)
        panel.api.get_panel_data = AsyncMock(return_value=mock_panel_data)
        panel.api.get_circuits_data = AsyncMock(return_value=mock_circuits)
        panel.api.get_storage_battery_data = AsyncMock()

        await panel.update()

        # Verify battery API is not called
        panel.api.get_storage_battery_data.assert_not_called()
        assert panel._storage_battery is None

    @pytest.mark.asyncio
    async def test_update_handles_empty_data_exception(
        self, mock_status, mock_panel_data, mock_circuits
    ):
        """Test update handles SpanPanelReturnedEmptyData exception."""
        panel = SpanPanel("192.168.1.100")

        # Mock API methods - one raises empty data exception
        panel.api.get_status_data = AsyncMock(side_effect=SpanPanelReturnedEmptyData("Empty data"))
        panel.api.get_panel_data = AsyncMock(return_value=mock_panel_data)
        panel.api.get_circuits_data = AsyncMock(return_value=mock_circuits)

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

        # Mock API method to raise generic exception
        panel.api.get_status_data = AsyncMock(side_effect=Exception("API error"))

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
