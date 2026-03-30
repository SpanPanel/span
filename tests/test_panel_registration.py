"""Tests for integration panel registration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()

        mock_register = AsyncMock()

        with patch(
            "custom_components.span_panel.async_register_panel",
            mock_register,
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

    @pytest.mark.asyncio
    async def test_static_path_serves_frontend_dir(self):
        """Static path points to the frontend dist directory."""
        from custom_components.span_panel import async_setup, PANEL_URL

        hass = MagicMock()
        hass.data = {}
        hass.http = MagicMock()
        hass.http.async_register_static_paths = AsyncMock()
        hass.services = MagicMock()
        hass.services.async_register = MagicMock()

        with patch(
            "custom_components.span_panel.async_register_panel",
            AsyncMock(),
        ):
            await async_setup(hass, {})

        static_call = hass.http.async_register_static_paths.call_args
        configs = static_call[0][0]
        assert len(configs) == 1
        config = configs[0]
        assert config.url_path == PANEL_URL
        assert "frontend" in config.path
        assert "dist" in config.path
