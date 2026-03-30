"""Tests for panel_entities section in topology response."""

import pytest
from unittest.mock import MagicMock

from custom_components.span_panel.helpers import build_panel_unique_id


class TestBuildPanelEntityMap:
    """Verify _build_panel_entity_map resolves unique_ids to entity_ids."""

    def test_resolves_known_sensors(self):
        """Panel entities section contains resolved entity IDs."""
        from custom_components.span_panel.websocket import _build_panel_entity_map

        serial = "test-serial-123"
        mock_registry = MagicMock()

        def mock_get_entity_id(domain, integration, unique_id):
            mapping = {
                build_panel_unique_id(serial, "instantGridPowerW"): "sensor.my_current_power",
                build_panel_unique_id(serial, "sitePowerW"): "sensor.custom_site",
                build_panel_unique_id(serial, "dsm_grid_state"): "sensor.grid_state",
            }
            return mapping.get(unique_id)

        mock_registry.async_get_entity_id = mock_get_entity_id

        result = _build_panel_entity_map(serial, mock_registry)

        assert result["current_power"] == "sensor.my_current_power"
        assert result["site_power"] == "sensor.custom_site"
        assert result["dsm_state"] == "sensor.grid_state"
        assert "pv_power" not in result  # Not in mock mapping

    def test_empty_when_no_entities_found(self):
        """Returns empty dict when no entities resolve."""
        from custom_components.span_panel.websocket import _build_panel_entity_map

        mock_registry = MagicMock()
        mock_registry.async_get_entity_id = MagicMock(return_value=None)

        result = _build_panel_entity_map("unknown-serial", mock_registry)
        assert result == {}

    def test_all_panel_sensor_keys_attempted(self):
        """All defined panel sensor keys are looked up."""
        from custom_components.span_panel.websocket import (
            _build_panel_entity_map,
            _PANEL_SENSOR_KEYS,
        )

        serial = "test-serial"
        mock_registry = MagicMock()
        mock_registry.async_get_entity_id = MagicMock(return_value=None)

        _build_panel_entity_map(serial, mock_registry)

        # Should have been called once per key
        assert mock_registry.async_get_entity_id.call_count == len(_PANEL_SENSOR_KEYS)
