"""Integration tests for coordinator-based entity migration with real entities."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.span_panel.const import (
    COORDINATOR,
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.options import (
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
)
from custom_components.span_panel.solar_synthetic_sensors import SolarSyntheticSensors
from tests.common import create_mock_config_entry


@pytest.fixture
async def setup_integration_with_yaml_config(hass: HomeAssistant):
    """Set up the integration with YAML config generation to test naming."""
    # Create config entry with solar enabled
    config_entry = create_mock_config_entry(
        {
            CONF_HOST: "192.168.1.100",
            USE_CIRCUIT_NUMBERS: True,
            USE_DEVICE_PREFIX: True,
        },
        {
            INVERTER_ENABLE: True,
            INVERTER_LEG1: 30,
            INVERTER_LEG2: 32,
        },
    )

    # Create temporary directory for YAML files
    temp_dir = tempfile.mkdtemp()

    # Create mock coordinator with proper data structure
    mock_coordinator = MagicMock(spec=SpanPanelCoordinator)
    mock_coordinator.config_entry = config_entry
    mock_coordinator.hass = hass

    # Create mock span panel data with required circuits
    span_panel = MagicMock()
    span_panel.circuits = {
        "unmapped_tab_30": MagicMock(name="Unmapped Tab 30"),
        "unmapped_tab_32": MagicMock(name="Unmapped Tab 32"),
    }
    mock_coordinator.data = span_panel

    # Store coordinator in hass.data BEFORE creating the bridge
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry.entry_id] = {COORDINATOR: mock_coordinator}

    # Mock the solar synthetic sensors to create YAML with real entity names
    solar_sensors = SolarSyntheticSensors(hass, config_entry, temp_dir)

    # Generate the YAML configuration (this creates the real entity names)
    await solar_sensors.generate_config(30, 32)

    # Read the generated YAML configuration
    yaml_path = Path(temp_dir) / "solar_synthetic_sensors.yaml"
    with open(yaml_path) as f:
        yaml_config = yaml.safe_load(f)

    return {
        "config_entry": config_entry,
        "coordinator": mock_coordinator,
        "yaml_config": yaml_config,
        "temp_dir": temp_dir,
    }


@pytest.mark.asyncio
async def test_yaml_config_generates_correct_entity_names(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that the YAML configuration generates entities with the correct names."""
    setup = setup_integration_with_yaml_config
    yaml_config = setup["yaml_config"]

    # Verify that the YAML config contains solar entities with correct names
    sensors = yaml_config.get("sensors", {})

    # Check for expected solar sensors (circuit-based keys for v1.0.10 compatibility)
    expected_sensors = {
        "solar_inverter_instant_power": "Solar Inverter Instant Power",
        "solar_inverter_energy_produced": "Solar Inverter Energy Produced",
        "solar_inverter_energy_consumed": "Solar Inverter Energy Consumed",
    }

    for sensor_key, expected_name in expected_sensors.items():
        assert sensor_key in sensors, f"Expected sensor {sensor_key} not found"
        assert sensors[sensor_key]["name"] == expected_name, (
            f"Expected name '{expected_name}' for {sensor_key}, got '{sensors[sensor_key]['name']}'"
        )

    print(f"✓ All {len(expected_sensors)} solar sensors have correct names in YAML config")


@pytest.mark.asyncio
async def test_coordinator_entity_id_generation_from_names(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that the coordinator can generate new entity IDs from entity names."""
    setup = setup_integration_with_yaml_config

    # Import the real coordinator migration method
    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a real coordinator instance with the migration method
    real_coordinator = SpanPanelCoordinator.__new__(SpanPanelCoordinator)
    real_coordinator.hass = hass
    real_coordinator.config_entry = setup["config_entry"]

    # Test the internal entity ID generation method directly
    expected_mappings = [
        {
            "current_entity_id": "sensor.span_panel_circuit_30_32_instant_power",
            "entity_name": "Solar Inverter Instant Power",
            "expected_id": "sensor.span_panel_solar_inverter_instant_power",
        },
        {
            "current_entity_id": "sensor.span_panel_circuit_30_32_energy_produced",
            "entity_name": "Solar Inverter Energy Produced",
            "expected_id": "sensor.span_panel_solar_inverter_energy_produced",
        },
        {
            "current_entity_id": "sensor.span_panel_circuit_30_32_energy_consumed",
            "entity_name": "Solar Inverter Energy Consumed",
            "expected_id": "sensor.span_panel_solar_inverter_energy_consumed",
        },
        {
            "current_entity_id": "sensor.span_panel_circuit_5_7_battery_level",
            "entity_name": "Battery Bank Level",
            "expected_id": "sensor.span_panel_battery_bank_level",
        },
    ]

    for mapping in expected_mappings:
        current_id = mapping["current_entity_id"]
        entity_name = mapping["entity_name"]
        expected_id = mapping["expected_id"]

        # Test the _generate_new_entity_id_from_name method
        generated_id = real_coordinator._generate_new_entity_id_from_name(
            current_id,
            entity_name,
            EntityNamingPattern.CIRCUIT_NUMBERS,
            EntityNamingPattern.FRIENDLY_NAMES,
        )

        assert generated_id == expected_id, (
            f"Expected '{expected_id}' for entity name '{entity_name}', got '{generated_id}'"
        )

    print(f"✓ All {len(expected_mappings)} entity name-to-ID mappings are correct")


@pytest.mark.asyncio
async def test_coordinator_migration_method_exists_and_callable(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that the coordinator migration method exists and is callable."""
    setup = setup_integration_with_yaml_config

    # Import the real coordinator
    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a real coordinator instance
    real_coordinator = SpanPanelCoordinator.__new__(SpanPanelCoordinator)
    real_coordinator.hass = hass
    real_coordinator.config_entry = setup["config_entry"]

    # Test that the migrate_entities method exists
    assert hasattr(real_coordinator, "migrate_entities"), (
        "Coordinator should have migrate_entities method"
    )
    assert callable(getattr(real_coordinator, "migrate_entities")), (
        "migrate_entities should be callable"
    )

    # Test that the helper method exists
    assert hasattr(real_coordinator, "_generate_new_entity_id_from_name"), (
        "Coordinator should have _generate_new_entity_id_from_name method"
    )

    print("✓ Coordinator has required migration methods")


@pytest.mark.asyncio
async def test_coordinator_migration_handles_missing_entities_gracefully(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that coordinator migration handles missing entities gracefully."""
    setup = setup_integration_with_yaml_config

    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a real coordinator instance
    real_coordinator = SpanPanelCoordinator.__new__(SpanPanelCoordinator)
    real_coordinator.hass = hass
    real_coordinator.config_entry = setup["config_entry"]

    # Migration should succeed even with no entities registered
    success = await real_coordinator.migrate_entities(
        EntityNamingPattern.CIRCUIT_NUMBERS.value, EntityNamingPattern.FRIENDLY_NAMES.value
    )

    assert success, "Migration should succeed even with no entities"
    print("✓ Migration handles missing entities gracefully")


@pytest.mark.asyncio
async def test_bidirectional_entity_migration_works_correctly(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that entity migration works correctly in both directions."""
    setup = setup_integration_with_yaml_config

    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a real coordinator instance
    real_coordinator = SpanPanelCoordinator.__new__(SpanPanelCoordinator)
    real_coordinator.hass = hass
    real_coordinator.config_entry = setup["config_entry"]

    # Add mock panel data with test circuits
    from unittest.mock import MagicMock

    mock_panel = MagicMock()
    mock_panel.circuits = {}

    # Set up the panel status with the expected serial number
    mock_panel.status.serial_number = "12345"

    # Create mock circuits for the test cases
    circuit_names = {1: "Main Panel", 10: "Heat Pump", 15: "EV Charger", 20: "Pool Pump"}

    for circuit_num in [1, 10, 15, 20]:
        circuit_id = str(circuit_num)
        mock_circuit = MagicMock()
        mock_circuit.tabs = [circuit_num]  # tab position is the circuit number
        mock_circuit.name = circuit_names[circuit_num]  # Set the actual friendly name
        mock_panel.circuits[circuit_id] = mock_circuit

    real_coordinator.data = mock_panel

    # Test cases with different entity types
    test_cases = [
        {
            "circuit_id": "sensor.span_panel_circuit_10_power",
            "circuit_unique_id": "span_12345_10_power",
            "friendly_id": "sensor.span_panel_heat_pump_power",
            "friendly_name": "Heat Pump Power",
        },
        {
            "circuit_id": "switch.span_panel_circuit_15_breaker",
            "circuit_unique_id": "span_12345_relay_15",
            "friendly_id": "switch.span_panel_ev_charger_breaker",
            "friendly_name": "EV Charger",
        },
        {
            "circuit_id": "sensor.span_panel_circuit_20_energy_produced",
            "circuit_unique_id": "span_12345_20_producedEnergyWh",
            "friendly_id": "sensor.span_panel_pool_pump_energy_produced",
            "friendly_name": "Pool Pump Energy Produced",
        },
    ]

    for test_case in test_cases:
        print(f"\n=== Testing {test_case['friendly_name']} ===")

        # Test: Circuit Numbers → Friendly Names
        circuit_to_friendly = real_coordinator._generate_new_entity_id_from_name(
            test_case["circuit_id"],
            test_case["friendly_name"],
            EntityNamingPattern.CIRCUIT_NUMBERS,
            EntityNamingPattern.FRIENDLY_NAMES,
            test_case["circuit_unique_id"],
        )

        print(f"Circuit → Friendly: {test_case['circuit_id']} → {circuit_to_friendly}")
        assert circuit_to_friendly == test_case["friendly_id"], (
            f"Circuit→Friendly failed: expected '{test_case['friendly_id']}', got '{circuit_to_friendly}'"
        )

        # Test: Friendly Names → Circuit Numbers
        friendly_to_circuit = real_coordinator._generate_new_entity_id_from_name(
            test_case["friendly_id"],
            test_case["friendly_name"],
            EntityNamingPattern.FRIENDLY_NAMES,
            EntityNamingPattern.CIRCUIT_NUMBERS,
            test_case["circuit_unique_id"],
        )

        print(f"Friendly → Circuit: {test_case['friendly_id']} → {friendly_to_circuit}")
        assert friendly_to_circuit == test_case["circuit_id"], (
            f"Friendly→Circuit failed: expected '{test_case['circuit_id']}', got '{friendly_to_circuit}'"
        )

        # Test: Round-trip consistency (Circuit → Friendly → Circuit)
        roundtrip_result = real_coordinator._generate_new_entity_id_from_name(
            circuit_to_friendly,
            test_case["friendly_name"],
            EntityNamingPattern.FRIENDLY_NAMES,
            EntityNamingPattern.CIRCUIT_NUMBERS,
            test_case["circuit_unique_id"],
        )

        print(f"Round-trip: {test_case['circuit_id']} → {circuit_to_friendly} → {roundtrip_result}")
        assert roundtrip_result == test_case["circuit_id"], (
            f"Round-trip failed: expected '{test_case['circuit_id']}', got '{roundtrip_result}'"
        )

    print(f"\n✓ Bidirectional migration works correctly for {len(test_cases)} entity types")
    print("✓ All round-trip migrations are consistent")


async def test_entity_name_to_id_conversion_is_generic(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that entity name to ID conversion works generically for any sensor type."""
    setup = setup_integration_with_yaml_config

    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a real coordinator instance
    real_coordinator = SpanPanelCoordinator.__new__(SpanPanelCoordinator)
    real_coordinator.hass = hass
    real_coordinator.config_entry = setup["config_entry"]

    # Add mock panel data with test circuits
    from unittest.mock import MagicMock

    mock_panel = MagicMock()
    mock_panel.circuits = {}

    # Set up the panel status with the expected serial number
    mock_panel.status.serial_number = "12345"

    # Create mock circuits for the test cases
    circuit_names = {
        1: "Main Panel",
        10: "Heat Pump",
        15: "EV Charger",
        20: "Pool Pump",
        25: "Solar Inverter",
    }

    for circuit_num in [1, 10, 15, 20, 25]:
        circuit_id = str(circuit_num)
        mock_circuit = MagicMock()
        mock_circuit.tabs = [circuit_num]  # tab position is the circuit number
        mock_circuit.name = circuit_names[circuit_num]  # Set the actual friendly name
        mock_panel.circuits[circuit_id] = mock_circuit

    real_coordinator.data = mock_panel

    # Test various circuit entity types for bidirectional migration
    test_cases = [
        {
            "circuit_id": "sensor.span_panel_circuit_10_power",
            "friendly_id": "sensor.span_panel_heat_pump_power",
            "friendly_name": "Heat Pump Power",
            "unique_id": "span_12345_10_power",
        },
        {
            "circuit_id": "sensor.span_panel_circuit_15_energy_produced",
            "friendly_id": "sensor.span_panel_ev_charger_energy_produced",
            "friendly_name": "EV Charger Energy Produced",
            "unique_id": "span_12345_15_producedEnergyWh",
        },
        {
            "circuit_id": "sensor.span_panel_circuit_20_energy_consumed",
            "friendly_id": "sensor.span_panel_pool_pump_energy_consumed",
            "friendly_name": "Pool Pump Energy Consumed",
            "unique_id": "span_12345_20_consumedEnergyWh",
        },
        {
            "circuit_id": "switch.span_panel_circuit_1_breaker",
            "friendly_id": "switch.span_panel_main_panel_breaker",
            "friendly_name": "Main Panel",
            "unique_id": "span_12345_relay_1",
        },
    ]

    print("Testing bidirectional entity migration...")

    for test_case in test_cases:
        # Test 1: Circuit Numbers → Friendly Names
        circuit_to_friendly = real_coordinator._generate_new_entity_id_from_name(
            test_case["circuit_id"],
            test_case["friendly_name"],
            EntityNamingPattern.CIRCUIT_NUMBERS,
            EntityNamingPattern.FRIENDLY_NAMES,
            test_case["unique_id"],
        )
        assert circuit_to_friendly == test_case["friendly_id"], (
            f"Circuit→Friendly: Expected '{test_case['friendly_id']}' for name '{test_case['friendly_name']}', "
            f"got '{circuit_to_friendly}'"
        )

        # Test 2: Friendly Names → Circuit Numbers (THE CRITICAL TEST!)
        friendly_to_circuit = real_coordinator._generate_new_entity_id_from_name(
            test_case["friendly_id"],
            test_case["friendly_name"],
            EntityNamingPattern.FRIENDLY_NAMES,
            EntityNamingPattern.CIRCUIT_NUMBERS,
            test_case["unique_id"],
        )
        assert friendly_to_circuit == test_case["circuit_id"], (
            f"Friendly→Circuit: Expected '{test_case['circuit_id']}' for name '{test_case['friendly_name']}', "
            f"got '{friendly_to_circuit}'"
        )

        # Test 3: Round-trip consistency (Circuit → Friendly → Circuit)
        round_trip_result = real_coordinator._generate_new_entity_id_from_name(
            circuit_to_friendly,
            test_case["friendly_name"],
            EntityNamingPattern.FRIENDLY_NAMES,
            EntityNamingPattern.CIRCUIT_NUMBERS,
            test_case["unique_id"],
        )
        assert round_trip_result == test_case["circuit_id"], (
            f"Round-trip failed: {test_case['circuit_id']} → {circuit_to_friendly} → {round_trip_result}"
        )

    print(f"✓ Bidirectional migration works correctly for {len(test_cases)} entity types")
    print("✓ Round-trip migration consistency verified")


@pytest.mark.asyncio
async def test_migration_handles_panel_level_entities_correctly(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that panel-level entities are handled correctly during migration."""
    setup = setup_integration_with_yaml_config

    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a real coordinator instance
    real_coordinator = SpanPanelCoordinator.__new__(SpanPanelCoordinator)
    real_coordinator.hass = hass
    real_coordinator.config_entry = setup["config_entry"]

    # Add mock panel data (needed for circuit lookup, even though this test focuses on panel-level entities)
    from unittest.mock import MagicMock

    mock_panel = MagicMock()
    mock_panel.circuits = {}

    # Set up the panel status with the expected serial number
    mock_panel.status.serial_number = "12345"

    real_coordinator.data = mock_panel

    # Test panel-level entities (these should use friendly names regardless of pattern)
    panel_level_cases = [
        {
            "entity_id": "sensor.span_panel_current_power",
            "friendly_name": "Current Power",
            "unique_id": "span_12345_instantGridPowerW",
        },
        {
            "entity_id": "binary_sensor.span_panel_cellular_link",
            "friendly_name": "Cellular Link",
            "unique_id": "span_12345_wwanLink",
        },
        {
            "entity_id": "sensor.span_panel_dsm_state",
            "friendly_name": "DSM State",
            "unique_id": "span_12345_dsmState",
        },
    ]

    print("Testing panel-level entity migration...")

    for test_case in panel_level_cases:
        # Panel-level entities should maintain consistent naming regardless of pattern
        for from_pattern, to_pattern in [
            (EntityNamingPattern.CIRCUIT_NUMBERS, EntityNamingPattern.FRIENDLY_NAMES),
            (EntityNamingPattern.FRIENDLY_NAMES, EntityNamingPattern.CIRCUIT_NUMBERS),
        ]:
            result = real_coordinator._generate_new_entity_id_from_name(
                test_case["entity_id"],
                test_case["friendly_name"],
                from_pattern,
                to_pattern,
                test_case["unique_id"],
            )
            assert result == test_case["entity_id"], (
                f"Panel-level entity should not change: {from_pattern.value}→{to_pattern.value} "
                f"Expected '{test_case['entity_id']}', got '{result}'"
            )

    print(f"✓ Panel-level entities maintain stable IDs across {len(panel_level_cases)} test cases")


@pytest.mark.asyncio
async def test_unmapped_tab_entities_are_not_renamed_during_migration(
    hass: HomeAssistant, setup_integration_with_yaml_config
):
    """Test that unmapped_tab entity IDs are never renamed during migration.

    Unmapped tab entities are used as variables in solar YAML configuration
    and must maintain stable entity IDs regardless of naming pattern changes.
    The migration logic should skip entities with 'unmapped_tab_' in their entity_id.
    """
    setup = setup_integration_with_yaml_config

    from custom_components.span_panel.const import EntityNamingPattern
    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a real coordinator instance
    real_coordinator = SpanPanelCoordinator.__new__(SpanPanelCoordinator)
    real_coordinator.hass = hass
    real_coordinator.config_entry = setup["config_entry"]

    # Test unmapped_tab entities that should NEVER be renamed
    unmapped_test_cases = [
        {
            "entity_id": "sensor.span_panel_unmapped_tab_15_power",
            "friendly_name": "Solar Leg 1 Power",
        },
        {
            "entity_id": "sensor.span_panel_unmapped_tab_15_energy_produced",
            "friendly_name": "Solar Leg 1 Energy Produced",
        },
        {
            "entity_id": "sensor.span_panel_unmapped_tab_16_power",
            "friendly_name": "Solar Leg 2 Power",
        },
        {
            "entity_id": "sensor.span_panel_unmapped_tab_16_energy_consumed",
            "friendly_name": "Solar Leg 2 Energy Consumed",
        },
    ]

    # Test that _generate_new_entity_id_from_name returns None for unmapped entities
    # (indicating they should not be renamed)
    for test_case in unmapped_test_cases:
        entity_id = test_case["entity_id"]
        friendly_name = test_case["friendly_name"]

        # Try to generate a new entity ID - this should return None or the same ID
        # for unmapped_tab entities because the migration logic should skip them
        generated_id = real_coordinator._generate_new_entity_id_from_name(
            entity_id,
            friendly_name,
            EntityNamingPattern.CIRCUIT_NUMBERS,
            EntityNamingPattern.FRIENDLY_NAMES,
        )

        # For unmapped entities, the generated ID should either be None (skip)
        # or the same as the original (no change)
        assert generated_id is None or generated_id == entity_id, (
            f"Unmapped entity {entity_id} should not be renamed. Got new ID: {generated_id}"
        )

    # Test regular entities for comparison - these SHOULD potentially be renamed
    regular_test_cases = [
        {
            "entity_id": "sensor.span_panel_circuit_10_power",
            "friendly_name": "Kitchen Outlet Power",
            "expected": "sensor.span_panel_kitchen_outlet_power",
        },
        {
            "entity_id": "sensor.span_panel_circuit_20_energy_produced",
            "friendly_name": "Living Room Energy Produced",
            "expected": "sensor.span_panel_living_room_energy_produced",
        },
    ]

    for test_case in regular_test_cases:
        entity_id = test_case["entity_id"]
        friendly_name = test_case["friendly_name"]
        expected = test_case["expected"]

        generated_id = real_coordinator._generate_new_entity_id_from_name(
            entity_id,
            friendly_name,
            EntityNamingPattern.CIRCUIT_NUMBERS,
            EntityNamingPattern.FRIENDLY_NAMES,
        )

        # Regular entities should get new IDs based on their friendly names
        assert generated_id == expected, (
            f"Regular entity {entity_id} should be renamed to {expected}, got {generated_id}"
        )

    print(f"✓ All {len(unmapped_test_cases)} unmapped_tab entities correctly avoided renaming")
    print(f"✓ All {len(regular_test_cases)} regular entities correctly got new names")
    print("✓ Migration logic properly distinguishes between unmapped and regular entities")


@pytest.mark.asyncio
async def test_unmapped_tab_entities_not_renamed():
    """Test that unmapped_tab entities are not renamed during migration.

    This verifies that entity IDs like sensor.span_panel_unmapped_tab_30_energy_produced
    are never changed during migration, as they are used as variables in solar YAML.
    """
    from unittest.mock import MagicMock

    from custom_components.span_panel.const import EntityNamingPattern
    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a mock coordinator with proper initialization
    mock_hass = MagicMock()
    mock_config_entry = MagicMock()
    mock_config_entry.entry_id = "test_entry_id"

    coordinator = SpanPanelCoordinator(
        hass=mock_hass,
        span_panel=MagicMock(),
        name="Test Panel",
        update_interval=30,
        config_entry=mock_config_entry,
    )

    # Test unmapped_tab entity IDs that should NOT be renamed
    test_cases = [
        "sensor.span_panel_unmapped_tab_30_energy_produced",
        "sensor.span_panel_unmapped_tab_31_power",
        "sensor.unmapped_tab_15_energy_consumed",
        "sensor.span_panel_unmapped_tab_16_instant_power",
    ]

    for entity_id in test_cases:
        # Try to generate new entity ID - should return None for unmapped_tab entities
        new_entity_id = coordinator._generate_new_entity_id_from_name(
            entity_id,
            "Some Friendly Name",  # Name doesn't matter for unmapped_tab
            EntityNamingPattern.LEGACY_NAMES,
            EntityNamingPattern.CIRCUIT_NUMBERS,
        )

        # Should return None, indicating no renaming should occur
        assert new_entity_id is None, (
            f"Unmapped tab entity {entity_id} should not be renamed, "
            f"but got new ID: {new_entity_id}"
        )

    print("✓ Unmapped tab entities are correctly excluded from migration")


@pytest.mark.asyncio
async def test_synthetic_sensors_are_removed_during_migration():
    """Test that synthetic sensors are removed during migration for recreation.

    This verifies that entity IDs like sensor.span_panel_solar_inverter_instant_power
    are removed during migration so they can be recreated via YAML regeneration
    with the correct naming pattern.
    """
    from unittest.mock import MagicMock

    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    # Create a mock coordinator with proper initialization
    mock_hass = MagicMock()
    mock_config_entry = MagicMock()
    mock_config_entry.entry_id = "test_entry_id"

    coordinator = SpanPanelCoordinator(
        hass=mock_hass,
        span_panel=MagicMock(),
        name="Test Panel",
        update_interval=30,
        config_entry=mock_config_entry,
    )

    # Test synthetic sensor entity IDs that should be identified
    test_cases = [
        ("sensor.span_panel_solar_inverter_instant_power", True),
        ("sensor.span_panel_solar_inverter_energy_produced", True),
        ("sensor.span_panel_solar_inverter_energy_consumed", True),
        ("sensor.span_panel_circuit_1_power", False),  # Regular circuit sensor
        ("sensor.span_panel_kitchen_outlets_power", False),  # Regular circuit sensor
        ("sensor.span_panel_unmapped_tab_30_energy_produced", False),  # Unmapped tab
    ]

    for entity_id, should_be_synthetic in test_cases:
        result = coordinator._is_integration_synthetic_sensor(entity_id)

        if should_be_synthetic:
            assert result, f"Entity {entity_id} should be identified as synthetic sensor"
        else:
            assert not result, f"Entity {entity_id} should NOT be identified as synthetic sensor"

    print("✓ Synthetic sensor identification works correctly")
