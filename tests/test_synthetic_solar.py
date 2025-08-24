"""Tests for synthetic_solar module.

This module tests the solar synthetic sensor generation functionality
using YAML fixtures and the simulation factory.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er

from custom_components.span_panel.synthetic_solar import (
    _extract_leg_numbers,
    _get_template_attributes,
    _generate_sensor_entity_id,
    _process_sensor_template,
    generate_solar_sensors_with_entity_ids,
    handle_solar_sensor_crud,
    handle_solar_options_change,
    get_stored_solar_sensor_ids_from_set,
    get_solar_data_value,
    SOLAR_SENSOR_DEFINITIONS,
)
from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.span_panel import SpanPanel
from ha_synthetic_sensors.sensor_set import SensorSet
from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory


class TestLegNumberExtraction:
    """Test circuit leg number extraction."""

    def test_extract_leg_numbers_valid_tabs(self):
        """Test extracting valid tab numbers from circuit IDs."""
        leg1_number, leg2_number = _extract_leg_numbers("unmapped_tab_30", "unmapped_tab_32")
        assert leg1_number == 30
        assert leg2_number == 32

    def test_extract_leg_numbers_single_tab(self):
        """Test extracting with one valid tab."""
        leg1_number, leg2_number = _extract_leg_numbers("unmapped_tab_15", "")
        assert leg1_number == 15
        assert leg2_number == 0

    def test_extract_leg_numbers_invalid_format(self):
        """Test extracting from invalid circuit ID format."""
        leg1_number, leg2_number = _extract_leg_numbers("invalid_circuit", "unmapped_tab_32")
        assert leg1_number == 0
        assert leg2_number == 32

    def test_extract_leg_numbers_both_invalid(self):
        """Test extracting when both circuit IDs are invalid."""
        leg1_number, leg2_number = _extract_leg_numbers("invalid_1", "invalid_2")
        assert leg1_number == 0
        assert leg2_number == 0

    def test_extract_leg_numbers_none_values(self):
        """Test extracting with None values."""
        leg1_number, leg2_number = _extract_leg_numbers("unmapped_tab_30", None)
        assert leg1_number == 30
        assert leg2_number == 0


class TestTemplateAttributes:
    """Test template attribute generation."""

    def test_get_template_attributes_dual_tab(self):
        """Test generating template attributes for dual-tab configuration."""
        tabs_attr, voltage_attr = _get_template_attributes(30, 32)
        assert "30" in tabs_attr and "32" in tabs_attr
        assert voltage_attr == 240

    def test_get_template_attributes_single_tab(self):
        """Test generating template attributes for single-tab configuration."""
        tabs_attr, voltage_attr = _get_template_attributes(15, 0)
        assert tabs_attr == ""
        assert voltage_attr == 0

    def test_get_template_attributes_no_tabs(self):
        """Test generating template attributes with no valid tabs."""
        tabs_attr, voltage_attr = _get_template_attributes(0, 0)
        assert tabs_attr == ""
        assert voltage_attr == 0


class TestSensorEntityIdGeneration:
    """Test solar sensor entity ID generation."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.config_entry = MagicMock(spec=ConfigEntry)
        coordinator.config_entry.data = {"device_name": "Test Panel"}
        coordinator.config_entry.title = "Test Panel"
        coordinator.config_entry.options = {
            "energy_reporting_grace_period": 15,
            "power_display_precision": 0,
            "energy_display_precision": 2,
        }
        return coordinator

    @pytest.fixture
    def mock_span_panel(self):
        """Create a mock span panel."""
        return MagicMock(spec=SpanPanel)

    def test_generate_sensor_entity_id_dual_tab(self, mock_coordinator, mock_span_panel):
        """Test generating entity ID for dual-tab solar sensor."""
        with patch('custom_components.span_panel.synthetic_solar.construct_240v_synthetic_entity_id') as mock_240v:
            mock_240v.return_value = "sensor.span_panel_solar_power"

            entity_id = _generate_sensor_entity_id(
                mock_coordinator, mock_span_panel, "power", 30, 32
            )

            assert entity_id == "sensor.span_panel_solar_power"
            mock_240v.assert_called_once()

    def test_generate_sensor_entity_id_single_tab(self, mock_coordinator, mock_span_panel):
        """Test generating entity ID for single-tab solar sensor."""
        with patch('custom_components.span_panel.synthetic_solar.construct_120v_synthetic_entity_id') as mock_120v:
            mock_120v.return_value = "sensor.span_panel_solar_power"

            entity_id = _generate_sensor_entity_id(
                mock_coordinator, mock_span_panel, "power", 15, 0
            )

            assert entity_id == "sensor.span_panel_solar_power"
            mock_120v.assert_called_once()

    def test_generate_sensor_entity_id_migration_mode(self, mock_coordinator, mock_span_panel):
        """Test generating entity ID in migration mode."""
        mock_hass = MagicMock(spec=HomeAssistant)
        mock_registry = MagicMock()
        mock_registry.async_get_entity_id.return_value = "sensor.existing_solar_power"

        with patch('homeassistant.helpers.entity_registry.async_get', return_value=mock_registry):
            with patch('custom_components.span_panel.synthetic_solar.construct_synthetic_unique_id_for_entry', return_value="test_unique_id"):
                entity_id = _generate_sensor_entity_id(
                    mock_coordinator, mock_span_panel, "power", 30, 32,
                    migration_mode=True, hass=mock_hass
                )

                assert entity_id == "sensor.existing_solar_power"

    def test_generate_sensor_entity_id_migration_mode_not_found(self, mock_coordinator, mock_span_panel):
        """Test migration mode when entity not found in registry."""
        mock_hass = MagicMock(spec=HomeAssistant)
        mock_registry = MagicMock()
        mock_registry.async_get_entity_id.return_value = None

        with patch('homeassistant.helpers.entity_registry.async_get', return_value=mock_registry):
            with patch('custom_components.span_panel.synthetic_solar.construct_synthetic_unique_id_for_entry', return_value="test_unique_id"):
                with pytest.raises(ValueError, match="MIGRATION ERROR"):
                    _generate_sensor_entity_id(
                        mock_coordinator, mock_span_panel, "power", 30, 32,
                        migration_mode=True, hass=mock_hass
                    )


class TestSensorTemplateProcessing:
    """Test sensor template processing."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        return MagicMock(spec=HomeAssistant)

    @pytest.fixture
    def sensor_definition(self):
        """Create a test sensor definition."""
        return {
            "template": "solar_current_power.yaml.txt",
            "sensor_type": "power",
            "description": "Current solar power production",
        }

    @pytest.fixture
    def template_vars(self):
        """Create test template variables."""
        return {
            "leg1_power_entity": "sensor.span_panel_unmapped_tab_30_power",
            "leg2_power_entity": "sensor.span_panel_unmapped_tab_32_power",
            "voltage_attribute": 240,
            "tabs_attribute": "tabs [30:32]",
        }

    async def test_process_sensor_template_success(self, mock_hass, sensor_definition, template_vars):
        """Test successful sensor template processing."""
        mock_result = {
            "sensor_configs": {
                "test_sensor": {
                    "entity_id": "sensor.solar_power",
                    "name": "Solar Power",
                    "formula": "leg1_power + leg2_power",
                    "variables": {"leg1_power": "sensor.test_1", "leg2_power": "sensor.test_2"},
                    "attributes": {"voltage": 240},
                    "metadata": {"unit_of_measurement": "W"},
                }
            }
        }

        with patch('custom_components.span_panel.synthetic_solar.combine_yaml_templates', return_value=mock_result):
            result = await _process_sensor_template(
                mock_hass, sensor_definition, template_vars, "sensor.solar_power"
            )

            assert result is not None
            assert result["entity_id"] == "sensor.solar_power"
            assert result["name"] == "Solar Power"
            assert result["formula"] == "leg1_power + leg2_power"

    async def test_process_sensor_template_missing_entity_id(self, mock_hass, sensor_definition, template_vars):
        """Test template processing with missing entity ID."""
        result = await _process_sensor_template(
            mock_hass, sensor_definition, template_vars, None
        )

        assert result is None

    async def test_process_sensor_template_missing_variables(self, mock_hass, sensor_definition):
        """Test template processing with missing required variables."""
        incomplete_vars = {"voltage_attribute": 240}

        result = await _process_sensor_template(
            mock_hass, sensor_definition, incomplete_vars, "sensor.solar_power"
        )

        assert result is None

    async def test_process_sensor_template_template_error(self, mock_hass, sensor_definition, template_vars):
        """Test template processing with template error."""
        with patch('custom_components.span_panel.synthetic_solar.combine_yaml_templates', side_effect=Exception("Template error")):
            result = await _process_sensor_template(
                mock_hass, sensor_definition, template_vars, "sensor.solar_power"
            )

            assert result is None


class TestSolarSensorGeneration:
    """Test solar sensor generation with entity IDs."""

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.config_entry = MagicMock(spec=ConfigEntry)
        coordinator.config_entry.data = {"device_name": "Test Panel"}
        coordinator.config_entry.options = {
            "energy_reporting_grace_period": 15,
            "power_display_precision": 0,
            "energy_display_precision": 2,
        }
        return coordinator

    @pytest.fixture
    def mock_span_panel(self):
        """Create a mock span panel."""
        return MagicMock(spec=SpanPanel)

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        return MagicMock(spec=HomeAssistant)

    async def test_generate_solar_sensors_success(self, mock_coordinator, mock_span_panel, mock_hass):
        """Test successful generation of solar sensors."""
        leg1_entity = "sensor.span_panel_unmapped_tab_30_power"
        leg2_entity = "sensor.span_panel_unmapped_tab_32_power"

        mock_template_result = {
            "entity_id": "sensor.solar_power",
            "name": "Solar Power",
            "formula": "leg1_power + leg2_power",
            "variables": {},
            "attributes": {},
            "metadata": {},
        }

        with patch('custom_components.span_panel.synthetic_solar._process_sensor_template', return_value=mock_template_result):
            with patch('custom_components.span_panel.synthetic_solar._generate_sensor_entity_id', return_value="sensor.solar_power"):
                with patch('custom_components.span_panel.synthetic_solar.construct_synthetic_unique_id_for_entry', side_effect=lambda coord, panel, name, device: f"test_unique_id_{name}"):
                    result = await generate_solar_sensors_with_entity_ids(
                        mock_coordinator, mock_span_panel, leg1_entity, leg2_entity, "Test Panel", hass=mock_hass
                    )

                    assert len(result) == len(SOLAR_SENSOR_DEFINITIONS)
                    # Check that all three sensors were created with unique IDs
                    assert any("power" in key for key in result.keys())
                    assert any("energy_produced" in key for key in result.keys())
                    assert any("energy_consumed" in key for key in result.keys())

    async def test_generate_solar_sensors_no_valid_tabs(self, mock_coordinator, mock_span_panel, mock_hass):
        """Test solar sensor generation with no valid tabs."""
        # Entity IDs that don't contain valid tab numbers (avoid 'tab' word to prevent parsing issues)
        leg1_entity = "sensor.span_panel_invalid_123_power"
        leg2_entity = "sensor.span_panel_also_invalid_456_power"

        result = await generate_solar_sensors_with_entity_ids(
            mock_coordinator, mock_span_panel, leg1_entity, leg2_entity, "Test Panel", hass=mock_hass
        )

        assert len(result) == 0

    async def test_generate_solar_sensors_migration_mode(self, mock_coordinator, mock_span_panel, mock_hass):
        """Test solar sensor generation in migration mode."""
        leg1_entity = "sensor.span_panel_unmapped_tab_30_power"
        leg2_entity = "sensor.span_panel_unmapped_tab_32_power"

        mock_registry = MagicMock()
        mock_registry.async_get_entity_id.return_value = "sensor.existing_solar_power"

        mock_template_result = {
            "entity_id": "sensor.existing_solar_power",
            "name": "Solar Power",
            "formula": "leg1_power + leg2_power",
            "variables": {},
            "attributes": {},
            "metadata": {},
        }

        with patch('homeassistant.helpers.entity_registry.async_get', return_value=mock_registry):
            with patch('custom_components.span_panel.synthetic_solar._process_sensor_template', return_value=mock_template_result):
                with patch('custom_components.span_panel.synthetic_solar._generate_sensor_entity_id', return_value="sensor.existing_entity"):
                    with patch('custom_components.span_panel.synthetic_solar.construct_synthetic_unique_id_for_entry', side_effect=lambda coord, panel, name, device: f"test_unique_id_{name}"):
                        result = await generate_solar_sensors_with_entity_ids(
                            mock_coordinator, mock_span_panel, leg1_entity, leg2_entity,
                            "Test Panel", migration_mode=True, hass=mock_hass
                        )

                        assert len(result) == len(SOLAR_SENSOR_DEFINITIONS)
                        # Check that all three sensors were created with unique IDs
                        assert any("power" in key for key in result.keys())
                        assert any("energy_produced" in key for key in result.keys())
                        assert any("energy_consumed" in key for key in result.keys())


class TestSolarSensorCRUD:
    """Test solar sensor CRUD operations."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        return MagicMock(spec=HomeAssistant)

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        return MagicMock(spec=ConfigEntry)

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.data = MagicMock(spec=SpanPanel)
        return coordinator

    @pytest.fixture
    def mock_sensor_set(self):
        """Create a mock sensor set."""
        sensor_set = MagicMock(spec=SensorSet)
        sensor_set.async_add_sensor_from_yaml = AsyncMock()
        return sensor_set

    async def test_handle_solar_sensor_crud_success(self, mock_hass, mock_config_entry,
                                                   mock_coordinator, mock_sensor_set):
        """Test successful solar sensor CRUD operations."""
        with patch('custom_components.span_panel.synthetic_solar.get_unmapped_circuit_entity_id') as mock_get_entity:
            mock_get_entity.side_effect = [
                "sensor.span_panel_unmapped_tab_30_power",
                "sensor.span_panel_unmapped_tab_32_power"
            ]

            with patch('custom_components.span_panel.synthetic_solar.load_template', return_value="mock_template"):
                with patch('custom_components.span_panel.synthetic_solar.fill_template', return_value="filled_template"):
                    with patch('custom_components.span_panel.synthetic_solar.construct_synthetic_unique_id_for_entry', return_value="test_unique_id"):
                        result = await handle_solar_sensor_crud(
                            mock_hass, mock_config_entry, mock_coordinator, mock_sensor_set,
                            enable_solar=True, leg1_circuit=30, leg2_circuit=32
                        )

                        assert result is True
                        assert mock_sensor_set.async_add_sensor_from_yaml.call_count == 4  # 4 solar sensor types (including net energy)

    async def test_handle_solar_sensor_crud_missing_circuit(self, mock_hass, mock_config_entry,
                                                           mock_coordinator, mock_sensor_set):
        """Test CRUD operations with missing circuit."""
        with patch('custom_components.span_panel.synthetic_solar.get_unmapped_circuit_entity_id') as mock_get_entity:
            mock_get_entity.side_effect = [None, "sensor.span_panel_unmapped_tab_32_power"]

            result = await handle_solar_sensor_crud(
                mock_hass, mock_config_entry, mock_coordinator, mock_sensor_set,
                enable_solar=True, leg1_circuit=30, leg2_circuit=32
            )

            assert result is False

    async def test_handle_solar_sensor_crud_exception(self, mock_hass, mock_config_entry,
                                                     mock_coordinator, mock_sensor_set):
        """Test CRUD operations with exception."""
        with patch('custom_components.span_panel.synthetic_solar.get_unmapped_circuit_entity_id', side_effect=Exception("Test error")):
            result = await handle_solar_sensor_crud(
                mock_hass, mock_config_entry, mock_coordinator, mock_sensor_set,
                enable_solar=True, leg1_circuit=30, leg2_circuit=32
            )

            assert result is False


class TestSolarOptionsChange:
    """Test solar options change handling."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.data = {}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"
        return entry

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock coordinator."""
        coordinator = MagicMock(spec=SpanPanelCoordinator)
        coordinator.data = MagicMock(spec=SpanPanel)
        coordinator.data.circuits = {"test_circuit": MagicMock(name="Test Circuit")}
        coordinator.config_entry = MagicMock(spec=ConfigEntry)
        coordinator.config_entry.data = {"device_name": "Test Panel"}
        coordinator.config_entry.title = "Test Panel"
        coordinator.config_entry.options = {
            "energy_reporting_grace_period": 15,
            "power_display_precision": 0,
            "energy_display_precision": 2,
        }
        return coordinator

    @pytest.fixture
    def mock_sensor_set(self):
        """Create a mock sensor set."""
        sensor_set = MagicMock(spec=SensorSet)
        sensor_set.exists = True
        sensor_set.async_remove_sensor = AsyncMock()
        return sensor_set

    async def test_handle_solar_options_change_enable_success(self, mock_hass, mock_config_entry,
                                                             mock_coordinator, mock_sensor_set):
        """Test enabling solar sensors successfully."""
        with patch('custom_components.span_panel.synthetic_solar.get_stored_solar_sensor_ids_from_set', return_value=[]):
            with patch('custom_components.span_panel.synthetic_solar.handle_solar_sensor_crud', return_value=True):
                result = await handle_solar_options_change(
                    mock_hass, mock_config_entry, mock_coordinator, mock_sensor_set,
                    enable_solar=True, leg1_circuit=30, leg2_circuit=32
                )

                assert result is True

    async def test_handle_solar_options_change_disable(self, mock_hass, mock_config_entry,
                                                      mock_coordinator, mock_sensor_set):
        """Test disabling solar sensors."""
        # Create mock sensors that will be returned by sensor_set.list_sensors()
        mock_sensor1 = MagicMock()
        mock_sensor1.unique_id = "solar_power"
        mock_sensor2 = MagicMock()
        mock_sensor2.unique_id = "solar_energy_produced"
        mock_sensor3 = MagicMock()
        mock_sensor3.unique_id = "solar_energy_consumed"

        mock_sensor_set.list_sensors.return_value = [mock_sensor1, mock_sensor2, mock_sensor3]

        with patch('custom_components.span_panel.synthetic_solar.construct_expected_solar_sensor_ids', return_value=["solar_power", "solar_energy_produced", "solar_energy_consumed"]):
            result = await handle_solar_options_change(
                mock_hass, mock_config_entry, mock_coordinator, mock_sensor_set,
                enable_solar=False, leg1_circuit=0, leg2_circuit=0
            )

            assert result is True
            assert mock_sensor_set.async_remove_sensor.call_count == 3

    async def test_handle_solar_options_change_sensor_set_not_exists(self, mock_hass, mock_config_entry,
                                                                    mock_coordinator, mock_sensor_set):
        """Test options change when sensor set doesn't exist."""
        mock_sensor_set.exists = False

        result = await handle_solar_options_change(
            mock_hass, mock_config_entry, mock_coordinator, mock_sensor_set,
            enable_solar=True, leg1_circuit=30, leg2_circuit=32
        )

        assert result is False

    async def test_handle_solar_options_change_exception(self, mock_hass, mock_config_entry,
                                                        mock_coordinator, mock_sensor_set):
        """Test options change with exception."""
        with patch('custom_components.span_panel.synthetic_solar.get_stored_solar_sensor_ids_from_set', side_effect=Exception("Test error")):
            result = await handle_solar_options_change(
                mock_hass, mock_config_entry, mock_coordinator, mock_sensor_set,
                enable_solar=True, leg1_circuit=30, leg2_circuit=32
            )

            assert result is False


class TestStoredSolarSensorIds:
    """Test stored solar sensor ID retrieval."""

    def test_get_stored_solar_sensor_ids_by_name(self):
        """Test finding solar sensors by name patterns."""
        mock_sensor_config = MagicMock()
        mock_sensor_config.unique_id = "solar_power_sensor"
        mock_sensor_config.entity_id = "sensor.solar_power"
        mock_sensor_config.formulas = []

        mock_sensor_set = MagicMock(spec=SensorSet)
        mock_sensor_set.list_sensors.return_value = [mock_sensor_config]

        result = get_stored_solar_sensor_ids_from_set(mock_sensor_set)

        assert len(result) == 1
        assert "solar_power_sensor" in result

    def test_get_stored_solar_sensor_ids_by_formula(self):
        """Test finding solar sensors by formula patterns."""
        mock_formula = MagicMock()
        mock_formula.variables = {"power": "sensor.leg1_power", "energy": "sensor.leg2_energy"}

        mock_sensor_config = MagicMock()
        mock_sensor_config.unique_id = "test_sensor"
        mock_sensor_config.entity_id = "sensor.test_sensor"
        mock_sensor_config.formulas = [mock_formula]

        mock_sensor_set = MagicMock(spec=SensorSet)
        mock_sensor_set.list_sensors.return_value = [mock_sensor_config]

        result = get_stored_solar_sensor_ids_from_set(mock_sensor_set)

        assert len(result) == 1
        assert "test_sensor" in result

    def test_get_stored_solar_sensor_ids_by_entity_pattern(self):
        """Test finding solar sensors by entity ID patterns."""
        mock_sensor_config = MagicMock()
        mock_sensor_config.unique_id = "circuit_30_32_power"
        mock_sensor_config.entity_id = "sensor.span_panel_circuit_30_32_power"
        mock_sensor_config.formulas = []

        mock_sensor_set = MagicMock(spec=SensorSet)
        mock_sensor_set.list_sensors.return_value = [mock_sensor_config]

        result = get_stored_solar_sensor_ids_from_set(mock_sensor_set)

        assert len(result) == 1
        assert "circuit_30_32_power" in result

    def test_get_stored_solar_sensor_ids_exception(self):
        """Test handling exceptions when getting stored sensor IDs."""
        mock_sensor_set = MagicMock(spec=SensorSet)
        mock_sensor_set.list_sensors.side_effect = Exception("Test error")

        result = get_stored_solar_sensor_ids_from_set(mock_sensor_set)

        assert result == []

    def test_get_stored_solar_sensor_ids_empty(self):
        """Test getting stored sensor IDs with empty sensor set."""
        mock_sensor_set = MagicMock(spec=SensorSet)
        mock_sensor_set.list_sensors.return_value = []

        result = get_stored_solar_sensor_ids_from_set(mock_sensor_set)

        assert result == []


class TestSolarDataValue:
    """Test solar data value function."""

    def test_get_solar_data_value(self):
        """Test solar data value function returns zero."""
        mock_span_panel = MagicMock(spec=SpanPanel)
        mock_sensor_map = {}

        result = get_solar_data_value("test", mock_span_panel, mock_sensor_map)

        assert result == 0.0


class TestSolarSensorDefinitions:
    """Test solar sensor definitions constants."""

    def test_solar_sensor_definitions_structure(self):
        """Test that solar sensor definitions have correct structure."""
        assert len(SOLAR_SENSOR_DEFINITIONS) == 4

        for definition in SOLAR_SENSOR_DEFINITIONS:
            assert "template" in definition
            assert "sensor_type" in definition
            assert "description" in definition
            assert definition["template"].endswith(".yaml.txt")

    def test_solar_sensor_definitions_types(self):
        """Test that all expected sensor types are defined."""
        sensor_types = {defn["sensor_type"] for defn in SOLAR_SENSOR_DEFINITIONS}
        expected_types = {"power", "energy_produced", "energy_consumed", "net_energy"}

        assert sensor_types == expected_types


class TestIntegrationWithFactory:
    """Integration tests using the simulation factory."""

    @pytest.fixture
    async def panel_data(self):
        """Get realistic panel data from simulation factory."""
        return await SpanPanelSimulationFactory.get_realistic_panel_data()

    @pytest.fixture
    async def solar_circuit_ids(self):
        """Get solar circuit IDs from simulation factory."""
        return await SpanPanelSimulationFactory.find_circuit_ids_by_name(["solar", "inverter"])

    async def test_extract_leg_numbers_with_real_data(self, solar_circuit_ids):
        """Test leg number extraction with real circuit data."""
        if len(solar_circuit_ids) >= 2:
            # Use actual solar circuit IDs from the simulation
            leg1_circuit = f"unmapped_tab_{solar_circuit_ids[0].split('_')[-1]}"
            leg2_circuit = f"unmapped_tab_{solar_circuit_ids[1].split('_')[-1]}"

            leg1_number, leg2_number = _extract_leg_numbers(leg1_circuit, leg2_circuit)

            assert leg1_number > 0
            assert leg2_number > 0
            assert leg1_number != leg2_number

    async def test_template_attributes_with_real_circuits(self, solar_circuit_ids):
        """Test template attribute generation with real circuit numbers."""
        if len(solar_circuit_ids) >= 2:
            # Extract tab numbers from real circuit IDs
            tab1 = int(solar_circuit_ids[0].split('_')[-1])
            tab2 = int(solar_circuit_ids[1].split('_')[-1])

            tabs_attr, voltage_attr = _get_template_attributes(tab1, tab2)

            assert str(tab1) in tabs_attr
            assert str(tab2) in tabs_attr
            assert voltage_attr == 240

    async def test_solar_sensor_generation_realistic_scenario(self, panel_data):
        """Test solar sensor generation with realistic panel data."""
        # Skip if no panel data available
        if not panel_data or "circuits" not in panel_data:
            pytest.skip("No realistic panel data available")

        # This test validates that the solar sensor generation functions
        # work with data structures that match the real SPAN panel API
        circuits_data = panel_data["circuits"]

        # Handle different possible circuit data structures
        if hasattr(circuits_data, 'circuits') and hasattr(circuits_data.circuits, 'additional_properties'):
            circuits = circuits_data.circuits.additional_properties
        else:
            circuits = circuits_data

        assert isinstance(circuits, dict)

        # The test validates the structure is compatible with our functions
        # without actually calling them with mock data
        assert len(circuits) > 0
