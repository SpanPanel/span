"""Tests for helper functions in the Span Panel integration."""

from unittest.mock import MagicMock, patch

from homeassistant.util import slugify

from custom_components.span_panel.const import USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX
from custom_components.span_panel.helpers import (
    construct_entity_id,
    construct_synthetic_entity_id,
    get_user_friendly_suffix,
)


def construct_synthetic_friendly_name(
    circuit_numbers: list[int],
    suffix_description: str,
    user_friendly_name: str | None = None,
) -> str:
    """Construct friendly display name for synthetic sensors (test helper).

    Args:
        circuit_numbers: List of circuit numbers (e.g., [30, 32] for solar inverter)
        suffix_description: Human-readable suffix (e.g., "Instant Power", "Energy Produced")
        user_friendly_name: Optional user-provided name (e.g., "Solar Production")

    Returns:
        Friendly name for display in Home Assistant

    """
    if user_friendly_name:
        # User provided a custom name - use it with the suffix
        return f"{user_friendly_name} {suffix_description}"

    # Fallback to circuit-based name
    valid_circuits = [str(num) for num in circuit_numbers if num > 0]
    if len(valid_circuits) > 1:
        circuit_spec = "-".join(valid_circuits)
        return f"Circuit {circuit_spec} {suffix_description}"
    elif len(valid_circuits) == 1:
        return f"Circuit {valid_circuits[0]} {suffix_description}"
    else:
        return f"Unknown Circuit {suffix_description}"


class TestHelperFunctions:
    """Test the helper functions."""

    def test_slugify_name_for_entity_id(self):
        """Test name sanitization for entity IDs using HA's slugify."""
        assert slugify("Kitchen Outlets") == "kitchen_outlets"
        assert slugify("Main-Panel") == "main_panel"
        assert slugify("Test Name") == "test_name"
        assert slugify("UPPER CASE") == "upper_case"

    def test_get_user_friendly_suffix(self):
        """Test suffix mapping conversion."""
        assert get_user_friendly_suffix("instantPowerW") == "power"
        assert get_user_friendly_suffix("producedEnergyWh") == "energy_produced"
        assert get_user_friendly_suffix("circuit_priority") == "priority"
        assert get_user_friendly_suffix("unknown_field") == "unknown_field"

    def test_construct_entity_id_config_entry_none(self):
        """Test construct_entity_id works with valid coordinator (None config_entry should be caught at coordinator level)."""
        coordinator = MagicMock()
        coordinator.config_entry.options = {}
        span_panel = MagicMock()

        # This should work fine - the coordinator should validate config_entry at construction time
        result = construct_entity_id(coordinator, span_panel, "sensor", "Kitchen", 1, "power")
        # With empty options, should use legacy naming (no device prefix)
        assert result == "sensor.kitchen_power"

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_entity_id_empty_options_legacy(self, mock_device_info):
        """Test construct_entity_id with empty options (legacy installation)."""
        mock_device_info.return_value = {"name": "Span Panel"}

        coordinator = MagicMock()
        coordinator.config_entry.options = {}
        span_panel = MagicMock()

        result = construct_entity_id(
            coordinator, span_panel, "sensor", "Kitchen Outlets", 1, "power"
        )
        assert result == "sensor.kitchen_outlets_power"

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_entity_id_circuit_numbers_no_device_name(self, mock_device_info):
        """Test construct_entity_id with circuit numbers but no device name."""
        mock_device_info.return_value = {"name": None}

        coordinator = MagicMock()
        coordinator.config_entry.options = {USE_CIRCUIT_NUMBERS: True}
        span_panel = MagicMock()

        result = construct_entity_id(coordinator, span_panel, "sensor", "Kitchen", 1, "power")
        assert result is None

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    def test_construct_entity_id_device_prefix_no_device_name(self, mock_device_info):
        """Test construct_entity_id with device prefix but no device name."""
        mock_device_info.return_value = {"name": None}

        coordinator = MagicMock()
        coordinator.config_entry.options = {
            USE_CIRCUIT_NUMBERS: False,
            USE_DEVICE_PREFIX: True,
        }
        span_panel = MagicMock()

        result = construct_entity_id(coordinator, span_panel, "sensor", "Kitchen", 1, "power")
        assert result is None

    @patch("custom_components.span_panel.helpers.er.async_get")
    def test_construct_synthetic_entity_id_config_entry_none(self, mock_registry):
        """Test construct_synthetic_entity_id works with valid coordinator (None config_entry should be caught at coordinator level)."""
        mock_registry.return_value = None

        coordinator = MagicMock()
        coordinator.config_entry.options = {}
        span_panel = MagicMock()

        # This should work fine - the coordinator should validate config_entry at construction time
        result = construct_synthetic_entity_id(coordinator, span_panel, "sensor", "power")
        # With empty options, should use legacy naming (no device prefix)
        assert result == "sensor.synthetic_sensor_power"

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    @patch("custom_components.span_panel.helpers.er.async_get")
    def test_construct_synthetic_entity_id_empty_options(self, mock_registry, mock_device_info):
        """Test construct_synthetic_entity_id with stable naming (synthetic sensors are always stable)."""
        mock_registry.return_value = None
        mock_device_info.return_value = {"name": "Span Panel"}

        coordinator = MagicMock()
        coordinator.config_entry.options = {}
        span_panel = MagicMock()

        # Test with friendly name - legacy installation should not use device prefix
        result = construct_synthetic_entity_id(
            coordinator,
            span_panel,
            "sensor",
            "power",
            "Solar Production Power",
        )
        assert result == "sensor.solar_production_power"

        # Test without friendly name - legacy installation should not use device prefix
        result = construct_synthetic_entity_id(coordinator, span_panel, "sensor", "power")
        assert result == "sensor.synthetic_sensor_power"

    @patch("custom_components.span_panel.helpers.panel_to_device_info")
    @patch("custom_components.span_panel.helpers.er.async_get")
    def test_construct_synthetic_entity_id_no_device_name(self, mock_registry, mock_device_info):
        """Test construct_synthetic_entity_id with no device name - should return None."""
        mock_registry.return_value = None
        mock_device_info.return_value = {"name": None}

        coordinator = MagicMock()
        coordinator.config_entry.options = {USE_CIRCUIT_NUMBERS: True}
        span_panel = MagicMock()

        # Synthetic sensors should return None if no device name available
        result = construct_synthetic_entity_id(coordinator, span_panel, "sensor", "power")
        assert result is None

    def test_construct_synthetic_friendly_name_with_user_name(self):
        """Test construct_synthetic_friendly_name with user-provided name."""
        result = construct_synthetic_friendly_name([30, 32], "Instant Power", "Solar Production")
        assert result == "Solar Production Instant Power"

    def test_construct_synthetic_friendly_name_multiple_circuits(self):
        """Test construct_synthetic_friendly_name with multiple circuits."""
        result = construct_synthetic_friendly_name([30, 32], "Instant Power")
        assert result == "Circuit 30-32 Instant Power"

    def test_construct_synthetic_friendly_name_single_circuit(self):
        """Test construct_synthetic_friendly_name with single circuit."""
        result = construct_synthetic_friendly_name([30], "Instant Power")
        assert result == "Circuit 30 Instant Power"

    def test_construct_synthetic_friendly_name_no_valid_circuits(self):
        """Test construct_synthetic_friendly_name with no valid circuits."""
        result = construct_synthetic_friendly_name([0, -1], "Instant Power")
        assert result == "Unknown Circuit Instant Power"

    def test_construct_synthetic_friendly_name_empty_circuits(self):
        """Test construct_synthetic_friendly_name with empty circuit list."""
        result = construct_synthetic_friendly_name([], "Instant Power")
        assert result == "Unknown Circuit Instant Power"
