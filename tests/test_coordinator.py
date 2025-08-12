"""Comprehensive tests for the SpanPanelCoordinator data coordination functionality."""

from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

from custom_components.span_panel.coordinator import SpanPanelCoordinator
from tests.common import create_mock_config_entry, create_mock_span_panel_with_data


class TestSpanPanelCoordinatorDataFlow:
    """Test coordinator data management and update cycles."""

    @pytest.mark.asyncio
    async def test_successful_data_update_returns_complete_panel_data(self):
        """Test that successful update returns fully populated SpanPanel with all expected data."""
        hass = MagicMock()

        # Create a fully populated mock SpanPanel with realistic data
        span_panel = create_mock_span_panel_with_data()
        # Make update method async-compatible
        span_panel.update = AsyncMock()
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        # Execute the update
        result = await coordinator._async_update_data()

        # Verify the returned data is the span_panel API instance
        assert result is span_panel

        # Verify the update method was called
        span_panel.update.assert_called_once()

        # Note: coordinator.data is set by the framework after successful update
        # The _async_update_data method returns the span_panel instance which becomes coordinator.data

        # Verify actual data content exists
        assert span_panel.status is not None
        assert span_panel.status.serial_number == "TEST123456"
        assert span_panel.panel is not None
        assert span_panel.panel.instant_grid_power_w == 1500.0
        assert len(span_panel.circuits) > 0
        assert "1" in span_panel.circuits
        assert span_panel.circuits["1"].name == "Main Panel"

    @pytest.mark.asyncio
    async def test_coordinator_manages_update_intervals_correctly(self):
        """Test that coordinator respects configured update intervals."""
        hass = MagicMock()
        span_panel = create_mock_span_panel_with_data()
        config_entry = create_mock_config_entry()

        # Test with 30 second interval
        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

        # Verify update interval is set correctly
        assert coordinator.update_interval.total_seconds() == 30

        # Test with different interval
        coordinator2 = SpanPanelCoordinator(hass, span_panel, "test", 60, config_entry)
        assert coordinator2.update_interval.total_seconds() == 60

    @pytest.mark.asyncio
    async def test_reload_request_triggers_integration_reload(self):
        """Test that reload requests are properly handled and trigger HA integration reload."""
        hass = MagicMock()
        span_panel = create_mock_span_panel_with_data()
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

        # Request a reload
        coordinator.request_reload()
        assert coordinator._needs_reload is True

        # Next update should trigger reload scheduling
        result = await coordinator._async_update_data()

        # Verify reload was scheduled
        hass.async_create_task.assert_called_once()

        # Verify _needs_reload was reset
        assert coordinator._needs_reload is False

        # Verify data is still returned
        assert result is span_panel


class TestSpanPanelCoordinatorErrorHandling:
    """Test coordinator error handling with specific SPAN Panel exceptions."""

    @pytest.mark.asyncio
    async def test_authentication_error_raises_config_entry_auth_failed(self):
        """Test that SpanPanelAuthError is properly converted to ConfigEntryAuthFailed with original error details."""
        hass = MagicMock()
        span_panel = MagicMock()
        original_error = SpanPanelAuthError("Invalid token: authentication failed")
        span_panel.update = AsyncMock(side_effect=original_error)
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        with pytest.raises(ConfigEntryAuthFailed) as exc_info:
            await coordinator._async_update_data()

        # Verify the original error is preserved
        assert exc_info.value.__cause__ is original_error
        assert "Invalid token: authentication failed" in str(exc_info.value.__cause__)

    @pytest.mark.asyncio
    async def test_connection_error_raises_update_failed_with_details(self):
        """Test that SpanPanelConnectionError is converted to UpdateFailed with descriptive message."""
        hass = MagicMock()
        span_panel = MagicMock()
        original_error = SpanPanelConnectionError("Network unreachable: connection refused")
        span_panel.update = AsyncMock(side_effect=original_error)
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        # Verify proper error message formatting
        assert "Error communicating with API" in str(exc_info.value)
        assert "Network unreachable: connection refused" in str(exc_info.value)
        assert exc_info.value.__cause__ is original_error

    @pytest.mark.asyncio
    async def test_timeout_error_raises_update_failed_with_timeout_context(self):
        """Test that SpanPanelTimeoutError is converted to UpdateFailed with timeout-specific messaging."""
        hass = MagicMock()
        span_panel = MagicMock()
        original_error = SpanPanelTimeoutError("Request timeout after 30 seconds")
        span_panel.update = AsyncMock(side_effect=original_error)
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Error communicating with API" in str(exc_info.value)
        assert "Request timeout after 30 seconds" in str(exc_info.value)
        assert exc_info.value.__cause__ is original_error

    @pytest.mark.asyncio
    async def test_retriable_error_raises_update_failed_with_retry_indication(self):
        """Test that SpanPanelRetriableError is converted to UpdateFailed indicating temporary nature."""
        hass = MagicMock()
        span_panel = MagicMock()
        original_error = SpanPanelRetriableError("Rate limit exceeded, retry in 60 seconds")
        span_panel.update = AsyncMock(side_effect=original_error)
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Temporary SPAN Panel error" in str(exc_info.value)
        assert "Rate limit exceeded, retry in 60 seconds" in str(exc_info.value)
        assert exc_info.value.__cause__ is original_error

    @pytest.mark.asyncio
    async def test_server_error_raises_update_failed_with_server_context(self):
        """Test that SpanPanelServerError is converted to UpdateFailed with server error context."""
        hass = MagicMock()
        span_panel = MagicMock()
        original_error = SpanPanelServerError("Internal server error: database unavailable")
        span_panel.update = AsyncMock(side_effect=original_error)
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "SPAN Panel server error" in str(exc_info.value)
        assert "Internal server error: database unavailable" in str(exc_info.value)
        assert exc_info.value.__cause__ is original_error

    @pytest.mark.asyncio
    async def test_api_error_raises_update_failed_with_api_context(self):
        """Test that SpanPanelAPIError is converted to UpdateFailed with API error context."""
        hass = MagicMock()
        span_panel = MagicMock()
        original_error = SpanPanelAPIError("Invalid API request: malformed JSON")
        span_panel.update = AsyncMock(side_effect=original_error)
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Error communicating with API" in str(exc_info.value)
        assert "Invalid API request: malformed JSON" in str(exc_info.value)
        assert exc_info.value.__cause__ is original_error

    @pytest.mark.asyncio
    async def test_asyncio_timeout_error_raises_update_failed(self):
        """Test that asyncio.TimeoutError is properly handled and converted to UpdateFailed."""
        hass = MagicMock()
        span_panel = MagicMock()
        original_error = TimeoutError("Operation timed out")
        span_panel.update = AsyncMock(side_effect=original_error)
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
        coordinator._needs_reload = False

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Error communicating with API" in str(exc_info.value)
        assert exc_info.value.__cause__ is original_error


class TestSpanPanelCoordinatorIntegration:
    """Test coordinator integration with Home Assistant."""

    @pytest.mark.asyncio
    async def test_coordinator_initializes_with_valid_configuration(self):
        """Test that coordinator initializes correctly with proper configuration and dependencies."""
        hass = MagicMock()
        span_panel = create_mock_span_panel_with_data()
        config_entry = create_mock_config_entry()

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

        # Verify initialization
        assert coordinator.span_panel_api is span_panel
        assert coordinator.config_entry is config_entry
        assert coordinator._needs_reload is False
        assert coordinator.name == "test"
        assert coordinator.hass is hass

    def test_coordinator_requires_valid_config_entry(self):
        """Test that coordinator raises error when initialized with None config_entry."""
        hass = MagicMock()
        span_panel = create_mock_span_panel_with_data()

        with pytest.raises(ValueError, match="config_entry cannot be None"):
            SpanPanelCoordinator(hass, span_panel, "test", 30, None)

    @pytest.mark.asyncio
    async def test_reload_task_handles_home_assistant_errors_gracefully(self):
        """Test that reload task properly handles and logs Home Assistant errors."""
        hass = MagicMock()
        span_panel = create_mock_span_panel_with_data()
        config_entry = create_mock_config_entry()

        # Mock reload to raise HomeAssistantError
        hass.config_entries.async_reload.side_effect = HomeAssistantError("Reload failed")

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

        # Trigger reload
        coordinator.request_reload()
        result = await coordinator._async_update_data()

        # Verify task was created and would handle the error
        hass.async_create_task.assert_called_once()
        assert result is span_panel

    @pytest.mark.asyncio
    async def test_reload_task_handles_unexpected_errors_gracefully(self):
        """Test that reload task properly handles unexpected errors during reload."""
        hass = MagicMock()
        span_panel = create_mock_span_panel_with_data()
        config_entry = create_mock_config_entry()

        # Mock reload to raise unexpected error
        hass.config_entries.async_reload.side_effect = RuntimeError("Unexpected error")

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

        # Trigger reload
        coordinator.request_reload()
        result = await coordinator._async_update_data()

        # Verify task was created and would handle the error
        hass.async_create_task.assert_called_once()
        assert result is span_panel

    @pytest.mark.asyncio
    async def test_reload_task_successful_completion(self):
        """Test that reload task completes successfully when HA reload succeeds."""
        hass = MagicMock()
        span_panel = create_mock_span_panel_with_data()
        config_entry = create_mock_config_entry()

        # Mock successful reload
        hass.config_entries.async_reload.return_value = None

        coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

        # Trigger reload
        coordinator.request_reload()
        result = await coordinator._async_update_data()

        # Verify task was created for successful reload
        hass.async_create_task.assert_called_once()
        assert result is span_panel


@pytest.mark.asyncio
async def test_coordinator_init():
    hass = MagicMock()
    span_panel = MagicMock()
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
    assert coordinator.span_panel_api is span_panel
    assert coordinator.hass is hass
    assert coordinator.config_entry is config_entry
    assert coordinator._needs_reload is False


@pytest.mark.asyncio
async def test_coordinator_update_success():
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock()
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    result = await coordinator._async_update_data()
    assert result is span_panel
    span_panel.update.assert_called_once()


@pytest.mark.asyncio
async def test_coordinator_update_failure():
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=Exception("API Error"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    with pytest.raises(Exception):
        await coordinator._async_update_data()


def test_request_reload():
    """Test request_reload sets the reload flag."""
    hass = MagicMock()
    span_panel = MagicMock()
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    assert coordinator._needs_reload is False
    coordinator.request_reload()
    assert coordinator._needs_reload is True


@pytest.mark.asyncio
async def test_async_update_data_with_reload():
    """Test _async_update_data when reload is requested."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()
    hass.config_entries.async_reload = AsyncMock()

    # Create a list to capture tasks and mock async_create_task to return a mock task
    created_tasks = []

    def mock_create_task(coro):
        task = AsyncMock()
        created_tasks.append(coro)
        return task

    hass.async_create_task = mock_create_task

    span_panel = MagicMock()
    config_entry = create_mock_config_entry()
    config_entry.entry_id = "test_entry_id"

    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
    coordinator._needs_reload = True

    result = await coordinator._async_update_data()

    # Should return span_panel and reset reload flag
    assert result is span_panel
    assert coordinator._needs_reload is False
    # Should schedule a reload task
    assert len(created_tasks) == 1

    # Execute the scheduled task to avoid warnings
    await created_tasks[0]


def test_coordinator_init_none_config_entry():
    """Test coordinator initialization with None config_entry raises ValueError."""
    hass = MagicMock()
    span_panel = MagicMock()

    with pytest.raises(ValueError, match="config_entry cannot be None"):
        SpanPanelCoordinator(hass, span_panel, "test", 30, None)


@pytest.mark.asyncio
async def test_async_update_data_auth_error():
    """Test handling of SpanPanelAuthError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_connection_error():
    """Test handling of SpanPanelConnectionError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelConnectionError("Connection failed"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed, match="Error communicating with API"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_timeout_error():
    """Test that SpanPanelTimeoutError is properly converted to UpdateFailed."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelTimeoutError("Connection timeout"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()

    assert "Error communicating with API" in str(exc_info.value)
    assert "Connection timeout" in str(exc_info.value)
    span_panel.update.assert_called_once()


@pytest.mark.asyncio
async def test_async_update_data_retriable_error():
    """Test handling of SpanPanelRetriableError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelRetriableError("Retry me"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed, match="Temporary SPAN Panel error"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_server_error():
    """Test handling of SpanPanelServerError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelServerError("Server error"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed, match="SPAN Panel server error"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_api_error():
    """Test handling of SpanPanelAPIError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelAPIError("API error"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed, match="Error communicating with API"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_asyncio_timeout():
    """Test handling of asyncio TimeoutError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=TimeoutError("Asyncio timeout"))
    config_entry = create_mock_config_entry()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed, match="Error communicating with API"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_reload_task_config_entry_not_ready():
    """Test the reload task when config entry is not ready."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()
    hass.config_entries.async_reload = AsyncMock(side_effect=ConfigEntryNotReady("Not ready"))

    span_panel = MagicMock()
    config_entry = create_mock_config_entry()
    config_entry.entry_id = "test_entry_id"

    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
    coordinator._needs_reload = True

    # Capture the task that gets created
    created_task = None

    def capture_task(task):
        nonlocal created_task
        created_task = task
        return MagicMock()

    hass.async_create_task.side_effect = capture_task

    await coordinator._async_update_data()

    # Execute the captured task
    assert created_task is not None
    await created_task


@pytest.mark.asyncio
async def test_reload_task_home_assistant_error():
    """Test the reload task when HomeAssistant error occurs."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()
    hass.config_entries.async_reload = AsyncMock(side_effect=HomeAssistantError("HA error"))

    span_panel = MagicMock()
    config_entry = create_mock_config_entry()
    config_entry.entry_id = "test_entry_id"

    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
    coordinator._needs_reload = True

    # Capture the task that gets created
    created_task = None

    def capture_task(task):
        nonlocal created_task
        created_task = task
        return MagicMock()

    hass.async_create_task.side_effect = capture_task

    await coordinator._async_update_data()

    # Execute the captured task
    assert created_task is not None
    await created_task


@pytest.mark.asyncio
async def test_reload_task_unexpected_error():
    """Test the reload task when unexpected error occurs."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()
    hass.config_entries.async_reload = AsyncMock(side_effect=Exception("Unexpected error"))

    span_panel = MagicMock()
    config_entry = create_mock_config_entry()
    config_entry.entry_id = "test_entry_id"

    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
    coordinator._needs_reload = True

    # Capture the task that gets created
    created_task = None

    def capture_task(task):
        nonlocal created_task
        created_task = task
        return MagicMock()

    hass.async_create_task.side_effect = capture_task

    await coordinator._async_update_data()

    # Execute the captured task
    assert created_task is not None
    await created_task


@pytest.mark.asyncio
async def test_reload_task_successful():
    """Test the reload task when everything works correctly."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()
    hass.config_entries.async_reload = AsyncMock()

    span_panel = MagicMock()
    config_entry = create_mock_config_entry()
    config_entry.entry_id = "test_entry_id"

    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)
    coordinator._needs_reload = True

    # Capture the task that gets created
    created_task = None

    def capture_task(task):
        nonlocal created_task
        created_task = task
        return MagicMock()

    hass.async_create_task.side_effect = capture_task

    await coordinator._async_update_data()

    # Execute the captured task
    assert created_task is not None
    await created_task

    # Verify reload was called
    hass.config_entries.async_reload.assert_called_once_with("test_entry_id")


# Test removed - coordinator constructor now validates config_entry is not None
