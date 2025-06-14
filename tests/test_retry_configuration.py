"""Test retry configuration in config flow and options."""

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry

from custom_components.span_panel.config_flow import create_config_client
from custom_components.span_panel.options import Options
from custom_components.span_panel.const import (
    CONFIG_TIMEOUT,
    CONFIG_API_RETRIES,
    CONFIG_API_RETRY_TIMEOUT,
    CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_API_RETRIES,
    DEFAULT_API_RETRY_TIMEOUT,
    DEFAULT_API_RETRY_BACKOFF_MULTIPLIER,
    CONF_API_RETRIES,
    CONF_API_RETRY_TIMEOUT,
    CONF_API_RETRY_BACKOFF_MULTIPLIER,
)


class TestRetryConfiguration:
    """Test retry configuration functionality."""

    def test_create_config_client_uses_config_settings(self):
        """Test that create_config_client uses the correct config settings."""
        with patch("custom_components.span_panel.config_flow.SpanPanelClient") as mock_client:
            create_config_client("192.168.1.100", use_ssl=False)

            # Verify the client was created with config settings (no retries, short timeout)
            mock_client.assert_called_once_with(
                host="192.168.1.100",
                timeout=CONFIG_TIMEOUT,
                use_ssl=False,
                retries=CONFIG_API_RETRIES,
                retry_timeout=CONFIG_API_RETRY_TIMEOUT,
                retry_backoff_multiplier=CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
            )

    def test_options_with_default_retry_settings(self):
        """Test that Options class uses default retry settings when not configured."""
        # Create a mock config entry with no retry options
        mock_entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="span_panel",
            title="Test Panel",
            data={},
            options={},  # No retry options
            source="test",
            entry_id="test_entry",
            discovery_keys={},
            subentries_data={},
            unique_id="test_unique_id",
        )

        options = Options(mock_entry)

        # Verify default values are used
        assert options.api_retries == DEFAULT_API_RETRIES
        assert options.api_retry_timeout == DEFAULT_API_RETRY_TIMEOUT
        assert options.api_retry_backoff_multiplier == DEFAULT_API_RETRY_BACKOFF_MULTIPLIER

    def test_options_with_custom_retry_settings(self):
        """Test that Options class respects custom retry settings."""
        # Create a mock config entry with custom retry options
        custom_options = {
            CONF_API_RETRIES: 5,
            CONF_API_RETRY_TIMEOUT: 1.0,
            CONF_API_RETRY_BACKOFF_MULTIPLIER: 3.0,
        }

        mock_entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="span_panel",
            title="Test Panel",
            data={},
            options=custom_options,
            source="test",
            entry_id="test_entry",
            discovery_keys={},
            subentries_data={},
            unique_id="test_unique_id",
        )

        options = Options(mock_entry)

        # Verify custom values are used
        assert options.api_retries == 5
        assert options.api_retry_timeout == 1.0
        assert options.api_retry_backoff_multiplier == 3.0

    def test_options_get_options_includes_retry_settings(self):
        """Test that get_options() includes retry configuration."""
        custom_options = {
            CONF_API_RETRIES: 2,
            CONF_API_RETRY_TIMEOUT: 0.8,
            CONF_API_RETRY_BACKOFF_MULTIPLIER: 1.5,
        }

        mock_entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="span_panel",
            title="Test Panel",
            data={},
            options=custom_options,
            source="test",
            entry_id="test_entry",
            discovery_keys={},
            subentries_data={},
            unique_id="test_unique_id",
        )

        options = Options(mock_entry)
        result = options.get_options()

        # Verify retry settings are included in the returned options
        assert result[CONF_API_RETRIES] == 2
        assert result[CONF_API_RETRY_TIMEOUT] == 0.8
        assert result[CONF_API_RETRY_BACKOFF_MULTIPLIER] == 1.5

    def test_config_vs_normal_operation_settings(self):
        """Test that config and normal operation settings are different."""
        # Config settings should have no retries and shorter timeout
        assert CONFIG_API_RETRIES == 0
        assert CONFIG_TIMEOUT == 15

        # Normal operation defaults should have retries and longer timeout
        assert DEFAULT_API_RETRIES == 3
        assert DEFAULT_API_RETRY_TIMEOUT == 0.5
        assert DEFAULT_API_RETRY_BACKOFF_MULTIPLIER == 2.0
