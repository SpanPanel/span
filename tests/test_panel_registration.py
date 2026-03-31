"""Tests for integration panel registration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


async def _fake_executor_job(func, *args):
    """Run a function synchronously, mimicking async_add_executor_job."""
    return func(*args)


class TestPanelRegistration:
    """Tests for sidebar panel registration."""

    @pytest.mark.asyncio
    async def test_panel_registered_on_setup(self):
        """Panel is registered when async_setup runs."""
        from custom_components.span_panel import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.http = MagicMock()
        hass.http.async_register_static_paths = AsyncMock()
        hass.async_add_executor_job = AsyncMock(side_effect=_fake_executor_job)
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()

        mock_register = AsyncMock()

        with (
            patch(
                "custom_components.span_panel.async_register_panel",
                mock_register,
            ),
            patch(
                "custom_components.span_panel.async_load_panel_settings",
                return_value={},
            ),
            patch(
                "custom_components.span_panel.async_remove_panel",
            ),
        ):
            result = await async_setup(hass, {})

        assert result is True
        hass.http.async_register_static_paths.assert_called_once()
        mock_register.assert_called_once()

        call_kwargs = mock_register.call_args
        assert call_kwargs[1]["sidebar_title"] == "Span Panel"
        assert call_kwargs[1]["sidebar_icon"] == "mdi:lightning-bolt"
        assert call_kwargs[1]["frontend_url_path"] == "span-panel"
        assert "span-panel.js" in call_kwargs[1]["module_url"]
        assert call_kwargs[1]["require_admin"] is False

    @pytest.mark.asyncio
    async def test_panel_hidden_when_show_panel_false(self):
        """Panel is not registered when show_panel is False."""
        from custom_components.span_panel import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.http = MagicMock()
        hass.http.async_register_static_paths = AsyncMock()
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()

        mock_register = AsyncMock()
        mock_remove = MagicMock()

        with (
            patch(
                "custom_components.span_panel.async_register_panel",
                mock_register,
            ),
            patch(
                "custom_components.span_panel.async_load_panel_settings",
                return_value={"show_panel": False},
            ),
            patch(
                "custom_components.span_panel.async_remove_panel",
                mock_remove,
            ),
        ):
            result = await async_setup(hass, {})

        assert result is True
        mock_register.assert_not_called()
        mock_remove.assert_called_once_with(hass, "span-panel", warn_if_unknown=False)

    @pytest.mark.asyncio
    async def test_panel_admin_only_when_configured(self):
        """Panel require_admin matches panel_admin_only setting."""
        from custom_components.span_panel import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.http = MagicMock()
        hass.http.async_register_static_paths = AsyncMock()
        hass.async_add_executor_job = AsyncMock(side_effect=_fake_executor_job)
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()

        mock_register = AsyncMock()

        with (
            patch(
                "custom_components.span_panel.async_register_panel",
                mock_register,
            ),
            patch(
                "custom_components.span_panel.async_load_panel_settings",
                return_value={"show_panel": True, "panel_admin_only": True},
            ),
            patch(
                "custom_components.span_panel.async_remove_panel",
            ),
        ):
            result = await async_setup(hass, {})

        assert result is True
        mock_register.assert_called_once()
        assert mock_register.call_args[1]["require_admin"] is True

    @pytest.mark.asyncio
    async def test_static_path_serves_frontend_dir(self):
        """Static path points to the frontend dist directory."""
        from custom_components.span_panel import async_setup, PANEL_URL

        hass = MagicMock()
        hass.data = {}
        hass.http = MagicMock()
        hass.http.async_register_static_paths = AsyncMock()
        hass.async_add_executor_job = AsyncMock(side_effect=_fake_executor_job)
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()

        with (
            patch(
                "custom_components.span_panel.async_register_panel",
                AsyncMock(),
            ),
            patch(
                "custom_components.span_panel.async_load_panel_settings",
                return_value={},
            ),
            patch(
                "custom_components.span_panel.async_remove_panel",
            ),
        ):
            await async_setup(hass, {})

        static_call = hass.http.async_register_static_paths.call_args
        configs = static_call[0][0]
        assert len(configs) == 1
        config = configs[0]
        assert config.url_path == PANEL_URL
        assert "frontend" in config.path
        assert "dist" in config.path
