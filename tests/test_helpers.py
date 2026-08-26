"""Tests for helper functions in the Span Panel integration."""

# pylint: disable=reimported

from unittest.mock import MagicMock, patch

from homeassistant.util import slugify
import pytest

from custom_components.span_panel.helpers import (
    async_create_span_notification,
    construct_circuit_identifier_from_tabs,
    detect_capabilities,
    get_suffix_from_sensor_key,
    get_user_friendly_suffix,
    is_panel_level_sensor_key,
)

from .factories import (
    SpanBatterySnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
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
    if len(valid_circuits) == 1:
        return f"Circuit {valid_circuits[0]} {suffix_description}"
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

    def test_get_suffix_from_sensor_key(self):
        """Test suffix extraction from panel and synthetic sensor keys."""
        assert get_suffix_from_sensor_key("span_abc123_solar_inverter_power") == "power"
        assert (
            get_suffix_from_sensor_key("span_abc123_house_total_energy_produced")
            == "energy_produced"
        )
        assert get_suffix_from_sensor_key("plain_sensor_name") == "name"

    def test_is_panel_level_sensor_key(self):
        """Test classification of panel-level and circuit-level sensor keys."""
        assert is_panel_level_sensor_key("span_span12345678_current_power") is True
        assert (
            is_panel_level_sensor_key(
                "span_span12345678_12ce227695cd44338864b0ef2ec4168b_instantPowerW"
            )
            is False
        )
        assert is_panel_level_sensor_key("invalid_format") is False

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

    def test_construct_circuit_identifier_from_tabs(self):
        """Test fallback circuit naming from tabs."""
        assert construct_circuit_identifier_from_tabs([5, 6], "c1") == "Circuit 5 6"
        assert construct_circuit_identifier_from_tabs([7], "c1") == "Circuit 7"
        assert construct_circuit_identifier_from_tabs([], "fallback") == "Circuit fallback"

    @patch("custom_components.span_panel.helpers.async_create")
    @pytest.mark.asyncio
    async def test_async_create_span_notification_logs_and_forwards(self, mock_create):
        """Test notification helper forwarding."""
        hass = MagicMock()

        await async_create_span_notification(
            hass,
            "Panel connection lost",
            "SPAN Alert",
            "notif-1",
            level="error",
        )

        mock_create.assert_called_once_with(
            hass,
            message="Panel connection lost",
            title="SPAN Alert",
            notification_id="notif-1",
        )

    def test_detect_capabilities_helper(self):
        """Test capability detection from a populated snapshot."""
        snapshot = SpanPanelSnapshotFactory.create(
            battery=SpanBatterySnapshotFactory.create(soe_percentage=88.0),
            power_flow_pv=1200.0,
            power_flow_site=3000.0,
            evse={"evse-0": SpanEvseSnapshotFactory.create()},
        )

        assert detect_capabilities(snapshot) == frozenset({"bess", "evse", "power_flows", "pv"})
