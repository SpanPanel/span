import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator


@pytest.mark.asyncio
async def test_coordinator_init():
    hass = MagicMock()
    span_panel = MagicMock()
    config_entry = MagicMock()
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
    config_entry = MagicMock()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    result = await coordinator._async_update_data()
    assert result is span_panel
    span_panel.update.assert_called_once()


@pytest.mark.asyncio
async def test_coordinator_update_failure():
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=Exception("API Error"))
    config_entry = MagicMock()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    with pytest.raises(Exception):
        await coordinator._async_update_data()


def test_request_reload():
    """Test request_reload sets the reload flag."""
    hass = MagicMock()
    span_panel = MagicMock()
    config_entry = MagicMock()
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
    config_entry = MagicMock()
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


@pytest.mark.asyncio
async def test_async_update_data_reload_no_config_entry():
    """Test reload logic when config_entry is None."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()

    # Create a list to capture tasks and mock async_create_task to return a mock task
    created_tasks = []

    def mock_create_task(coro):
        task = AsyncMock()
        created_tasks.append(coro)
        return task

    hass.async_create_task = mock_create_task

    span_panel = MagicMock()

    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, None)
    coordinator._needs_reload = True

    result = await coordinator._async_update_data()

    # Should still return span_panel and reset reload flag
    assert result is span_panel
    assert coordinator._needs_reload is False
    # Should still schedule a task (error handling is inside the task)
    assert len(created_tasks) == 1

    # Execute the scheduled task to avoid warnings
    await created_tasks[0]


@pytest.mark.asyncio
async def test_async_update_data_auth_error():
    """Test handling of SpanPanelAuthError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    config_entry = MagicMock()
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
    config_entry = MagicMock()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed, match="Error communicating with API"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_timeout_error():
    """Test handling of SpanPanelTimeoutError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelTimeoutError("Timeout"))
    config_entry = MagicMock()
    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, config_entry)

    # Ensure _needs_reload is False to avoid reload path
    coordinator._needs_reload = False

    with pytest.raises(UpdateFailed, match="Error communicating with API"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_retriable_error():
    """Test handling of SpanPanelRetriableError."""
    hass = MagicMock()
    span_panel = MagicMock()
    span_panel.update = AsyncMock(side_effect=SpanPanelRetriableError("Retry me"))
    config_entry = MagicMock()
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
    config_entry = MagicMock()
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
    config_entry = MagicMock()
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
    config_entry = MagicMock()
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
    hass.config_entries.async_reload = AsyncMock(
        side_effect=ConfigEntryNotReady("Not ready")
    )

    span_panel = MagicMock()
    config_entry = MagicMock()
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
    config_entry = MagicMock()
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
    config_entry = MagicMock()
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
    config_entry = MagicMock()
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


@pytest.mark.asyncio
async def test_reload_task_none_config_entry():
    """Test the reload task when config_entry is None."""
    hass = MagicMock()
    hass.async_block_till_done = AsyncMock()

    span_panel = MagicMock()

    coordinator = SpanPanelCoordinator(hass, span_panel, "test", 30, None)
    coordinator._needs_reload = True

    # Capture the task that gets created
    created_task = None

    def capture_task(task):
        nonlocal created_task
        created_task = task
        return MagicMock()

    hass.async_create_task.side_effect = capture_task

    await coordinator._async_update_data()

    # Execute the captured task - should handle None config_entry gracefully
    assert created_task is not None
    await created_task
