"""Tests for synthetic_named_circuits module.

This module tests the named circuit synthetic sensor generation functionality.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er

from custom_components.span_panel.synthetic_named_circuits import (
    get_circuit_data_value,
    generate_named_circuit_sensors,
    NAMED_CIRCUIT_SENSOR_DEFINITIONS,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.span_panel import SpanPanel
from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit
from custom_components.span_panel.synthetic_utils import BackingEntity
from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory


class TestCircuitDataValue:
    """Test circuit data value extraction."""

    def test_get_circuit_data_value_valid_attribute(self):
        """Test getting valid circuit data value."""
        circuit_data = MagicMock()
        circuit_data.instant_power = 250.5

        result = get_circuit_data_value(circuit_data, "instant_power")

        assert result == 250.5

    def test_get_circuit_data_value_none_value(self):
        """Test getting circuit data when attribute is None."""
        circuit_data = MagicMock()
        circuit_data.instant_power = None

        result = get_circuit_data_value(circuit_data, "instant_power")

        assert result == 0.0

    def test_get_circuit_data_value_missing_attribute(self):
        """Test getting circuit data when attribute doesn't exist."""
        circuit_data = MagicMock()
        # Set the specific attribute to None to simulate missing attribute
        circuit_data.nonexistent_attr = None

        result = get_circuit_data_value(circuit_data, "nonexistent_attr")

        assert result == 0.0

    def test_get_circuit_data_value_string_number(self):
        """Test getting circuit data when value is a string number."""
        circuit_data = MagicMock()
        circuit_data.instant_power = "123.45"

        result = get_circuit_data_value(circuit_data, "instant_power")

        assert result == 123.45

    def test_get_circuit_data_value_invalid_type(self):
        """Test getting circuit data when value is not convertible to float."""
        circuit_data = MagicMock()
        circuit_data.instant_power = "invalid"

        result = get_circuit_data_value(circuit_data, "instant_power")

        assert result == 0.0

    def test_get_circuit_data_value_zero(self):
        """Test getting circuit data when value is zero."""
        circuit_data = MagicMock()
        circuit_data.instant_power = 0

        result = get_circuit_data_value(circuit_data, "instant_power")

        assert result == 0.0

    def test_get_circuit_data_value_negative(self):
        """Test getting circuit data when value is negative."""
        circuit_data = MagicMock()
        circuit_data.instant_power = -50.0

        result = get_circuit_data_value(circuit_data, "instant_power")

        assert result == -50.0


class TestNamedCircuitSensorDefinitions:
    """Test named circuit sensor definitions."""

    def test_sensor_definitions_structure(self):
        """Test that sensor definitions have correct structure."""
        assert len(NAMED_CIRCUIT_SENSOR_DEFINITIONS) == 3

        for definition in NAMED_CIRCUIT_SENSOR_DEFINITIONS:
            assert "key" in definition
            assert "name" in definition
            assert "template" in definition
            assert "data_path" in definition

    def test_sensor_definitions_keys(self):
        """Test that all expected sensor keys are defined."""
        keys = {defn["key"] for defn in NAMED_CIRCUIT_SENSOR_DEFINITIONS}
        expected_keys = {"instantPowerW", "producedEnergyWh", "consumedEnergyWh"}

        assert keys == expected_keys

    def test_sensor_definitions_data_paths(self):
        """Test that data paths match expected circuit attributes."""
        data_paths = {defn["data_path"] for defn in NAMED_CIRCUIT_SENSOR_DEFINITIONS}
        expected_paths = {"instant_power", "produced_energy", "consumed_energy"}

        assert data_paths == expected_paths


class TestGenerateNamedCircuitSensors:
    """Test named circuit sensor generation."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        return MagicMock(spec=HomeAssistant)

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.config_entry = MagicMock(spec=ConfigEntry)
        coordinator.config_entry.options = {
            "power_display_precision": 0,
            "energy_display_precision": 2,
            "energy_reporting_grace_period": 15,
        }
        coordinator.config_entry.data = {"device_name": "Test Panel"}
        coordinator.config_entry.title = "Test Panel"
        return coordinator

    @pytest.fixture
    def mock_span_panel(self):
        """Create a mock span panel with named circuits."""
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.status.serial_number = "SP3-001"

        # Create mock circuits
        circuits = {}

        # Named circuit (single tab)
        circuit1 = MagicMock(spec=SpanPanelCircuit)
        circuit1.circuit_id = "kitchen_lights"
        circuit1.name = "Kitchen Lights"
        circuit1.tabs = [3]
        circuit1.instant_power = 150.0
        circuit1.produced_energy = 0.0
        circuit1.consumed_energy = 1500.0
        circuits["kitchen_lights"] = circuit1

        # Named circuit (dual tab)
        circuit2 = MagicMock(spec=SpanPanelCircuit)
        circuit2.circuit_id = "electric_dryer"
        circuit2.name = "Electric Dryer"
        circuit2.tabs = [18, 20]
        circuit2.instant_power = 3000.0
        circuit2.produced_energy = 0.0
        circuit2.consumed_energy = 5000.0
        circuits["electric_dryer"] = circuit2

        # Unmapped circuit (should be filtered out)
        circuit3 = MagicMock(spec=SpanPanelCircuit)
        circuit3.circuit_id = "unmapped_tab_30"
        circuit3.name = "Unmapped Tab 30"
        circuit3.tabs = [30]
        circuits["unmapped_tab_30"] = circuit3

        span_panel.circuits = circuits
        return span_panel

    async def test_generate_named_circuit_sensors_success(self, mock_hass, mock_coordinator, mock_span_panel):
        """Test successful generation of named circuit sensors."""
        with patch('custom_components.span_panel.synthetic_named_circuits.get_circuit_number', side_effect=[3] * 10 + [18] * 10):
            with patch('custom_components.span_panel.synthetic_named_circuits.get_user_friendly_suffix', side_effect=['power', 'energy_produced', 'energy_consumed'] * 10):
                with patch('custom_components.span_panel.synthetic_named_circuits.construct_synthetic_unique_id', side_effect=[f"unique_{i}" for i in range(20)]):
                    with patch('custom_components.span_panel.synthetic_named_circuits.construct_120v_synthetic_entity_id', return_value="sensor.kitchen_lights_power"):
                        with patch('custom_components.span_panel.synthetic_named_circuits.construct_240v_synthetic_entity_id', return_value="sensor.electric_dryer_power"):
                            with patch('custom_components.span_panel.synthetic_named_circuits.construct_backing_entity_id_for_entry', side_effect=[f"backing_{i}" for i in range(20)]):
                                with patch('custom_components.span_panel.synthetic_named_circuits.combine_yaml_templates') as mock_combine:
                                    mock_combine.return_value = {
                                        "global_settings": {"device_identifier": "SP3-001"},
                                        "sensor_configs": {"test_sensor": {"entity_id": "sensor.test"}}
                                    }

                                    result = await generate_named_circuit_sensors(
                                        mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
                                    )

                                    sensor_configs, backing_entities, global_settings, mapping = result

                                    # Should generate 6 sensors (2 circuits * 3 sensor types each)
                                    assert len(backing_entities) == 6
                                    assert len(mapping) == 6
                                    assert global_settings["device_identifier"] == "SP3-001"

    async def test_generate_named_circuit_sensors_no_coordinator(self, mock_hass, mock_span_panel):
        """Test generation fails when coordinator is None."""
        with pytest.raises(ValueError, match="Coordinator is required"):
            await generate_named_circuit_sensors(
                mock_hass, None, mock_span_panel, "Test Panel"
            )

    async def test_generate_named_circuit_sensors_no_span_panel(self, mock_hass, mock_coordinator):
        """Test generation fails when span_panel is None."""
        with pytest.raises(ValueError, match="span_panel is None"):
            await generate_named_circuit_sensors(
                mock_hass, mock_coordinator, None, "Test Panel"
            )

    async def test_generate_named_circuit_sensors_no_named_circuits(self, mock_hass, mock_coordinator):
        """Test generation fails when no named circuits exist."""
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.status.serial_number = "SP3-001"
        span_panel.circuits = {}  # No circuits

        with pytest.raises(ValueError, match="No named circuits found"):
            await generate_named_circuit_sensors(
                mock_hass, mock_coordinator, span_panel, "Test Panel"
            )

    async def test_generate_named_circuit_sensors_only_unmapped_circuits(self, mock_hass, mock_coordinator):
        """Test generation fails when only unmapped circuits exist."""
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.status.serial_number = "SP3-001"

        # Only unmapped circuits
        circuits = {}
        circuit = MagicMock(spec=SpanPanelCircuit)
        circuit.circuit_id = "unmapped_tab_30"
        circuits["unmapped_tab_30"] = circuit

        span_panel.circuits = circuits

        with pytest.raises(ValueError, match="No named circuits found"):
            await generate_named_circuit_sensors(
                mock_hass, mock_coordinator, span_panel, "Test Panel"
            )

    async def test_generate_named_circuit_sensors_migration_mode(self, mock_hass, mock_coordinator, mock_span_panel):
        """Test generation in migration mode."""
        # Mock entity registry
        mock_registry = MagicMock()
        mock_registry.async_get_entity_id.return_value = "sensor.existing_entity"

        with patch('homeassistant.helpers.entity_registry.async_get', return_value=mock_registry):
            with patch('custom_components.span_panel.synthetic_named_circuits.get_circuit_number', side_effect=[3] * 10 + [18] * 10):
                with patch('custom_components.span_panel.synthetic_named_circuits.get_user_friendly_suffix', side_effect=['power', 'energy_produced', 'energy_consumed'] * 10):
                    with patch('custom_components.span_panel.synthetic_named_circuits.construct_synthetic_unique_id', side_effect=[f"unique_{i}" for i in range(20)]):
                        with patch('custom_components.span_panel.synthetic_named_circuits.construct_backing_entity_id_for_entry', side_effect=[f"backing_{i}" for i in range(20)]):
                            with patch('custom_components.span_panel.synthetic_named_circuits.combine_yaml_templates') as mock_combine:
                                mock_combine.return_value = {
                                    "global_settings": {"device_identifier": "SP3-001"},
                                    "sensor_configs": {"test_sensor": {"entity_id": "sensor.existing_entity"}}
                                }

                                result = await generate_named_circuit_sensors(
                                    mock_hass, mock_coordinator, mock_span_panel, "Test Panel", migration_mode=True
                                )

                                sensor_configs, backing_entities, global_settings, mapping = result

                                # Should still generate sensors using existing entity IDs
                                assert len(backing_entities) == 6
                                assert len(mapping) == 6

    async def test_generate_named_circuit_sensors_migration_mode_missing_entity(self, mock_hass, mock_coordinator, mock_span_panel):
        """Test migration mode fails when entity not found in registry."""
        # Mock entity registry returning None (entity not found)
        mock_registry = MagicMock()
        mock_registry.async_get_entity_id.return_value = None

        with patch('homeassistant.helpers.entity_registry.async_get', return_value=mock_registry):
            with patch('custom_components.span_panel.synthetic_named_circuits.get_circuit_number', return_value=3):
                with patch('custom_components.span_panel.synthetic_named_circuits.get_user_friendly_suffix', return_value='power'):
                    with patch('custom_components.span_panel.synthetic_named_circuits.construct_synthetic_unique_id', return_value="unique_id"):
                        with pytest.raises(ValueError, match="MIGRATION ERROR"):
                            await generate_named_circuit_sensors(
                                mock_hass, mock_coordinator, mock_span_panel, "Test Panel", migration_mode=True
                            )

    async def test_generate_named_circuit_sensors_invalid_tabs(self, mock_hass, mock_coordinator):
        """Test generation fails with invalid number of tabs."""
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.status.serial_number = "SP3-001"

        # Circuit with invalid number of tabs
        circuit = MagicMock(spec=SpanPanelCircuit)
        circuit.circuit_id = "invalid_circuit"
        circuit.name = "Invalid Circuit"
        circuit.tabs = [1, 2, 3]  # 3 tabs is invalid
        circuit.instant_power = 0.0

        span_panel.circuits = {"invalid_circuit": circuit}

        with patch('custom_components.span_panel.synthetic_named_circuits.get_circuit_number', return_value=1):
            with patch('custom_components.span_panel.synthetic_named_circuits.get_user_friendly_suffix', return_value='power'):
                with patch('custom_components.span_panel.synthetic_named_circuits.construct_synthetic_unique_id', return_value="unique_id"):
                    with pytest.raises(ValueError, match="Circuit invalid_circuit.*has 3 tabs"):
                        await generate_named_circuit_sensors(
                            mock_hass, mock_coordinator, span_panel, "Test Panel"
                        )

    async def test_generate_named_circuit_sensors_simulator_mode(self, mock_hass, mock_coordinator, mock_span_panel):
        """Test generation in simulator mode."""
        mock_coordinator.config_entry.data = {"simulation_mode": True}

        with patch('custom_components.span_panel.synthetic_named_circuits.get_circuit_number', side_effect=[3] * 10 + [18] * 10):
            with patch('custom_components.span_panel.synthetic_named_circuits.get_user_friendly_suffix', side_effect=['power', 'energy_produced', 'energy_consumed'] * 10):
                with patch('custom_components.span_panel.synthetic_named_circuits.construct_synthetic_unique_id', side_effect=[f"unique_{i}" for i in range(20)]):
                    with patch('custom_components.span_panel.synthetic_named_circuits.construct_120v_synthetic_entity_id', return_value="sensor.kitchen_lights_power"):
                        with patch('custom_components.span_panel.synthetic_named_circuits.construct_240v_synthetic_entity_id', return_value="sensor.electric_dryer_power"):
                            with patch('custom_components.span_panel.synthetic_named_circuits.construct_backing_entity_id_for_entry', side_effect=[f"backing_{i}" for i in range(20)]):
                                with patch('custom_components.span_panel.synthetic_named_circuits.combine_yaml_templates') as mock_combine:
                                    mock_combine.return_value = {
                                        "global_settings": {"device_identifier": "test-panel"},
                                        "sensor_configs": {"test_sensor": {"entity_id": "sensor.test"}}
                                    }

                                    result = await generate_named_circuit_sensors(
                                        mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
                                    )

                                    sensor_configs, backing_entities, global_settings, mapping = result

                                    # Should use slugified device name as identifier
                                    assert global_settings["device_identifier"] == "test-panel"

    async def test_generate_named_circuit_sensors_backing_entities(self, mock_hass, mock_coordinator, mock_span_panel):
        """Test that backing entities are created correctly."""
        with patch('custom_components.span_panel.synthetic_named_circuits.get_circuit_number', side_effect=[3] * 10 + [18] * 10):
            with patch('custom_components.span_panel.synthetic_named_circuits.get_user_friendly_suffix', side_effect=['power', 'energy_produced', 'energy_consumed'] * 10):
                with patch('custom_components.span_panel.synthetic_named_circuits.construct_synthetic_unique_id', side_effect=[f"unique_{i}" for i in range(20)]):
                    with patch('custom_components.span_panel.synthetic_named_circuits.construct_120v_synthetic_entity_id', return_value="sensor.kitchen_lights_power"):
                        with patch('custom_components.span_panel.synthetic_named_circuits.construct_240v_synthetic_entity_id', return_value="sensor.electric_dryer_power"):
                            with patch('custom_components.span_panel.synthetic_named_circuits.construct_backing_entity_id_for_entry', side_effect=[f"backing_{i}" for i in range(20)]):
                                with patch('custom_components.span_panel.synthetic_named_circuits.combine_yaml_templates') as mock_combine:
                                    mock_combine.return_value = {
                                        "global_settings": {"device_identifier": "SP3-001"},
                                        "sensor_configs": {"test_sensor": {"entity_id": "sensor.test"}}
                                    }

                                    result = await generate_named_circuit_sensors(
                                        mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
                                    )

                                    sensor_configs, backing_entities, global_settings, mapping = result

                                    # Check backing entities
                                    assert len(backing_entities) == 6

                                    # Check first backing entity (TypedDict, so check keys instead of isinstance)
                                    first_entity = backing_entities[0]
                                    assert "entity_id" in first_entity
                                    assert "value" in first_entity
                                    assert "data_path" in first_entity
                                    assert first_entity["entity_id"] == "backing_0"
                                    assert first_entity["value"] == 150.0  # Kitchen lights power
                                    assert "circuits.kitchen_lights.instant_power" in first_entity["data_path"]


class TestNamedCircuitSensorsIntegration:
    """Integration tests with realistic data."""

    @pytest.fixture
    async def realistic_panel_data(self):
        """Get realistic panel data from simulation factory."""
        panel_data = await SpanPanelSimulationFactory.get_realistic_panel_data()
        if not panel_data or "circuits" not in panel_data:
            pytest.skip("No realistic panel data available")
        return panel_data

    async def test_generate_with_realistic_data(self, realistic_panel_data):
        """Test sensor generation with realistic panel data."""
        mock_hass = MagicMock(spec=HomeAssistant)
        mock_coordinator = MagicMock(spec=SpanPanelCoordinator)
        mock_coordinator.config_entry = MagicMock(spec=ConfigEntry)
        mock_coordinator.config_entry.options = {
            "power_display_precision": 0,
            "energy_display_precision": 2,
            "energy_reporting_grace_period": 15,
        }
        mock_coordinator.config_entry.data = {}

        # Create span panel from realistic data
        span_panel = MagicMock(spec=SpanPanel)
        span_panel.status.serial_number = "SP3-REAL-001"

        circuits_data = realistic_panel_data["circuits"]
        circuits = {}

        # Extract circuits from API response structure
        if hasattr(circuits_data, 'circuits') and hasattr(circuits_data.circuits, 'additional_properties'):
            circuit_dict = circuits_data.circuits.additional_properties
        else:
            circuit_dict = circuits_data

        # Filter to only named circuits for testing
        named_circuit_count = 0
        for circuit_id, circuit_data in circuit_dict.items():
            if not circuit_id.startswith("unmapped_tab_") and named_circuit_count < 3:  # Limit for test performance
                circuit = MagicMock(spec=SpanPanelCircuit)
                circuit.circuit_id = circuit_id
                circuit.name = getattr(circuit_data, 'name', f"Circuit {circuit_id}")
                circuit.tabs = getattr(circuit_data, 'tabs', [1])
                circuit.instant_power = getattr(circuit_data, 'instant_power_w', 0.0)
                circuit.produced_energy = getattr(circuit_data, 'produced_energy_wh', 0.0)
                circuit.consumed_energy = getattr(circuit_data, 'consumed_energy_wh', 0.0)
                circuits[circuit_id] = circuit
                named_circuit_count += 1

        span_panel.circuits = circuits

        if len(circuits) == 0:
            pytest.skip("No named circuits found in realistic data")

        # Mock the helper functions and template processing
        with patch('custom_components.span_panel.synthetic_named_circuits.get_circuit_number', return_value=1):
            with patch('custom_components.span_panel.synthetic_named_circuits.get_user_friendly_suffix', side_effect=['power', 'energy_produced', 'energy_consumed'] * len(circuits) * 2):
                with patch('custom_components.span_panel.synthetic_named_circuits.construct_synthetic_unique_id', side_effect=[f"unique_{i}" for i in range(len(circuits) * 6)]):
                    with patch('custom_components.span_panel.synthetic_named_circuits.construct_120v_synthetic_entity_id', return_value="sensor.test_power"):
                        with patch('custom_components.span_panel.synthetic_named_circuits.construct_backing_entity_id_for_entry', side_effect=[f"backing_{i}" for i in range(len(circuits) * 6)]):
                            with patch('custom_components.span_panel.synthetic_named_circuits.combine_yaml_templates') as mock_combine:
                                mock_combine.return_value = {
                                    "global_settings": {"device_identifier": "SP3-REAL-001"},
                                    "sensor_configs": {"test_sensor": {"entity_id": "sensor.test"}}
                                }

                                result = await generate_named_circuit_sensors(
                                    mock_hass, mock_coordinator, span_panel, "Real Panel"
                                )

                                sensor_configs, backing_entities, global_settings, mapping = result

                                # Should generate 3 sensors per circuit
                                expected_count = len(circuits) * 3
                                assert len(backing_entities) == expected_count
                                assert len(mapping) == expected_count
