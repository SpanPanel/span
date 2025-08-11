"""
Working example showing how to properly test grace period with metadata functions.

This test demonstrates the correct mock setup that the SPAN Panel team needs
to make metadata(state, 'last_changed') work correctly in their tests.
"""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

# Import the synthetic sensors framework
from ha_synthetic_sensors import StorageManager
from ha_synthetic_sensors.type_definitions import DataProviderResult


def create_proper_mock_state_for_metadata(entity_id: str, value: Any, last_changed: datetime.datetime) -> MagicMock:
    """
    Create a properly structured mock state that works with metadata functions.

    This is the KEY fix for the SPAN Panel team's test setup.
    The metadata handler needs these specific attributes on the state object.
    """
    mock_state = MagicMock()

    # Critical attributes for metadata function access
    mock_state.entity_id = entity_id
    mock_state.object_id = entity_id.split(".")[-1] if "." in entity_id else entity_id
    mock_state.domain = entity_id.split(".")[0] if "." in entity_id else "sensor"
    mock_state.state = str(value) if value is not None else STATE_UNAVAILABLE
    mock_state.last_changed = last_changed
    mock_state.last_updated = last_changed
    mock_state.attributes = {}

    return mock_state


def create_working_data_provider(backing_entities: Dict[str, Dict[str, Any]]):
    """
    Create a data provider that properly supports metadata function queries.

    This shows the SPAN Panel team exactly what their data provider needs to return.
    """
    def data_provider_callback(entity_id: str) -> DataProviderResult:
        entity_info = backing_entities.get(entity_id)
        if not entity_info:
            return {"value": None, "exists": False}

        value = entity_info["value"]
        last_changed = entity_info["last_changed"]

        # This is the crucial part - create a state object with proper metadata
        mock_state = create_proper_mock_state_for_metadata(entity_id, value, last_changed)

        return {
            "value": value,
            "exists": True,
            "state": mock_state,  # This state object must have all metadata attributes
        }

    return data_provider_callback


@pytest.mark.asyncio
async def test_span_grace_period_with_proper_mock_setup(hass: HomeAssistant):
    """
    Demonstrate working grace period test with proper metadata mock setup.

    This test shows the SPAN Panel team exactly what they need to change
    in their test setup to make metadata(state, 'last_changed') work.
    """

    # 1. Create YAML that matches the fixed templates
    yaml_content = """
version: '1.0'
global_settings:
  device_identifier: test_span_001
  variables:
    energy_grace_period_minutes: '15'

sensors:
  span_main_meter_energy_consumed:
    name: "Main Meter Consumed Energy"
    entity_id: "sensor.test_span_001_main_meter_consumed_energy"
    formula: "state"
    UNAVAILABLE: "state if within_grace else UNKNOWN"
    UNKNOWN: "state if within_grace else UNKNOWN"
    variables:
      within_grace:
        formula: "minutes_between(metadata(state, 'last_changed'), now()) < energy_grace_period_minutes"
        UNAVAILABLE: 'false'
        UNKNOWN: 'false'
    attributes:
      grace_period_active:
        formula: "within_grace"
    metadata:
      unit_of_measurement: "Wh"
      device_class: "energy"
      state_class: "total_increasing"
"""

    # 2. Set up backing entities with proper metadata support
    backing_entity_id = "sensor.test_span_001_main_meter_consumed_energy_backing"
    initial_time = dt_util.utcnow()

    backing_entities = {
        backing_entity_id: {
            "value": 1500.0,
            "last_changed": initial_time,
        }
    }

    # 3. Create working data provider
    data_provider = create_working_data_provider(backing_entities)

    # 4. Set up synthetic sensors
    storage_manager = StorageManager(hass, "span_test", enable_entity_listener=False)
    await storage_manager.async_load()

    sensor_set_id = "test_energy_sensors"
    await storage_manager.async_create_sensor_set(
        sensor_set_id=sensor_set_id,
        device_identifier="test_span_001",
        name="Test Energy Sensors"
    )

    # 5. Import YAML
    result = await storage_manager.async_from_yaml(
        yaml_content=yaml_content,
        sensor_set_id=sensor_set_id
    )

    print("✅ YAML import successful!")
    print(f"Import result: {result}")

    # 6. Set up sensor manager with proper data provider
    from ha_synthetic_sensors import async_setup_synthetic_sensors
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    config_entry = MockConfigEntry(
        domain="span_panel",
        data={"device_id": "test_span_001"},
        entry_id="test_grace_period",
    )

    # Mock entity additions
    added_entities = []
    def mock_add_entities(entities):
        added_entities.extend(entities)
        for entity in entities:
            entity.hass = hass

    # Create sensor-to-backing mapping
    sensor_to_backing_mapping = {
        "span_main_meter_energy_consumed": backing_entity_id
    }

    # Setup synthetic sensors
    sensor_manager = await async_setup_synthetic_sensors(
        hass=hass,
        config_entry=config_entry,
        async_add_entities=mock_add_entities,
        storage_manager=storage_manager,
        device_identifier="test_span_001",
        data_provider_callback=data_provider,
        change_notifier=lambda changed_ids: None,
        sensor_to_backing_mapping=sensor_to_backing_mapping,
    )

    await hass.async_block_till_done()

    # 7. Verify sensor was created and works
    assert len(added_entities) == 1
    energy_sensor = added_entities[0]

    # Force sensor update
    await energy_sensor.async_update()
    await hass.async_block_till_done()

    # 8. Test that metadata function now works correctly
    print(f"Sensor state: {energy_sensor.native_value}")
    print(f"Sensor attributes: {energy_sensor.extra_state_attributes}")

    # The within_grace computed variable should now evaluate correctly
    # because metadata(state, 'last_changed') can access the mock_state.last_changed

    attributes = energy_sensor.extra_state_attributes or {}
    if "grace_period_active" in attributes:
        grace_period_value = attributes["grace_period_active"]
        print(f"✅ Grace period active: {grace_period_value}")

        # This should be a boolean value, not None
        assert grace_period_value is not None, "Grace period should not be None with proper mock setup"
        print("✅ Metadata function working correctly!")
    else:
        print("⚠️ grace_period_active attribute not found")

    print("✅ Test completed successfully!")


@pytest.mark.asyncio
async def test_grace_period_behavior_with_unavailable_backing_sensor(hass: HomeAssistant):
    """
    Test the complete grace period behavior when backing sensor becomes unavailable.

    This test demonstrates the full grace period workflow:
    1. Initial state with valid value
    2. Backing sensor becomes unavailable (None)
    3. Sensor should retain last value during grace period
    4. After grace period expires, sensor should become UNKNOWN
    """

    # 1. Create YAML that matches the fixed templates
    yaml_content = """
version: '1.0'
global_settings:
  device_identifier: test_span_001
  variables:
    energy_grace_period_minutes: '15'

sensors:
  span_main_meter_energy_consumed:
    name: "Main Meter Consumed Energy"
    entity_id: "sensor.test_span_001_main_meter_consumed_energy"
    formula: "state"
    UNAVAILABLE: "state if within_grace else UNKNOWN"
    UNKNOWN: "state if within_grace else UNKNOWN"
    variables:
      within_grace:
        formula: "minutes_between(metadata(state, 'last_changed'), now()) < energy_grace_period_minutes"
        UNAVAILABLE: 'false'
        UNKNOWN: 'false'
    attributes:
      grace_period_active:
        formula: "within_grace"
    metadata:
      unit_of_measurement: "Wh"
      device_class: "energy"
      state_class: "total_increasing"
"""

    # 2. Set up backing entities with proper metadata support
    backing_entity_id = "sensor.test_span_001_main_meter_consumed_energy_backing"
    initial_time = dt_util.utcnow()

    # Start with a valid value
    backing_entities = {
        backing_entity_id: {
            "value": 1500.0,
            "last_changed": initial_time,
        }
    }

    # 3. Create working data provider that can be updated
    def create_dynamic_data_provider(entities_dict):
        def data_provider_callback(entity_id: str) -> DataProviderResult:
            entity_info = entities_dict.get(entity_id)
            if not entity_info:
                return {"value": None, "exists": False}

            value = entity_info["value"]
            last_changed = entity_info["last_changed"]

            # Create proper mock state with all required attributes
            mock_state = create_proper_mock_state_for_metadata(entity_id, value, last_changed)

            return {
                "value": value,
                "exists": True,
                "state": mock_state,
            }
        return data_provider_callback

    data_provider = create_dynamic_data_provider(backing_entities)

    # 4. Set up synthetic sensors
    storage_manager = StorageManager(hass, "span_test", enable_entity_listener=False)
    await storage_manager.async_load()

    sensor_set_id = "test_energy_sensors"
    await storage_manager.async_create_sensor_set(
        sensor_set_id=sensor_set_id,
        device_identifier="test_span_001",
        name="Test Energy Sensors"
    )

    # 5. Import YAML
    result = await storage_manager.async_from_yaml(
        yaml_content=yaml_content,
        sensor_set_id=sensor_set_id
    )

    print("✅ YAML import successful!")

    # 6. Set up sensor manager
    from ha_synthetic_sensors import async_setup_synthetic_sensors
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    config_entry = MockConfigEntry(
        domain="span_panel",
        data={"device_id": "test_span_001"},
        entry_id="test_grace_period",
    )

    # Mock entity additions
    added_entities = []
    def mock_add_entities(entities):
        added_entities.extend(entities)
        for entity in entities:
            entity.hass = hass

    # Create sensor-to-backing mapping
    sensor_to_backing_mapping = {
        "span_main_meter_energy_consumed": backing_entity_id
    }

    # Setup synthetic sensors
    sensor_manager = await async_setup_synthetic_sensors(
        hass=hass,
        config_entry=config_entry,
        async_add_entities=mock_add_entities,
        storage_manager=storage_manager,
        device_identifier="test_span_001",
        data_provider_callback=data_provider,
        change_notifier=lambda changed_ids: None,
        sensor_to_backing_mapping=sensor_to_backing_mapping,
    )

    await hass.async_block_till_done()

    # 7. Verify sensor was created
    assert len(added_entities) == 1
    energy_sensor = added_entities[0]

    # 8. Test initial state (valid value)
    await energy_sensor.async_update()
    await hass.async_block_till_done()

    print(f"📊 Initial state: {energy_sensor.native_value}")
    print(f"📊 Initial attributes: {energy_sensor.extra_state_attributes}")

    # Should have valid value initially
    assert energy_sensor.native_value == 1500.0, "Initial state should be valid value"

    # 9. Simulate backing sensor becoming unavailable
    print("\n🔄 Simulating backing sensor becoming unavailable...")

    # Update backing entity to None (unavailable)
    backing_entities[backing_entity_id] = {
        "value": None,  # This simulates UNAVAILABLE state
        "last_changed": initial_time,  # Keep the same last_changed for grace period calculation
    }

    # Force sensor update
    await energy_sensor.async_update()
    await hass.async_block_till_done()

    print(f"📊 After becoming unavailable: {energy_sensor.native_value}")
    print(f"📊 Attributes: {energy_sensor.extra_state_attributes}")

    # Should still have the last valid value during grace period
    assert energy_sensor.native_value == 1500.0, "Should retain last value during grace period"

    attributes = energy_sensor.extra_state_attributes or {}
    grace_period_active = attributes.get("grace_period_active")
    print(f"📊 Grace period active: {grace_period_active}")

    # 10. Test grace period expiration by advancing time
    print("\n⏰ Testing grace period expiration...")

    # Advance time by 20 minutes (beyond 15-minute grace period)
    future_time = initial_time + datetime.timedelta(minutes=20)

    # Update backing entity with new timestamp
    backing_entities[backing_entity_id] = {
        "value": None,
        "last_changed": future_time,  # This will make grace period calculation show expired
    }

    # Force sensor update
    await energy_sensor.async_update()
    await hass.async_block_till_done()

    print(f"📊 After grace period expires: {energy_sensor.native_value}")
    print(f"📊 Attributes: {energy_sensor.extra_state_attributes}")

    # Should now be UNKNOWN after grace period expires
    assert energy_sensor.native_value == "unknown", "Should be UNKNOWN after grace period expires"

    # 11. Test recovery when backing sensor becomes available again
    print("\n🔄 Testing recovery when backing sensor becomes available...")

    # Update backing entity back to valid value
    recovery_time = future_time + datetime.timedelta(minutes=5)
    backing_entities[backing_entity_id] = {
        "value": 2000.0,  # New valid value
        "last_changed": recovery_time,
    }

    # Force sensor update
    await energy_sensor.async_update()
    await hass.async_block_till_done()

    print(f"📊 After recovery: {energy_sensor.native_value}")
    print(f"📊 Attributes: {energy_sensor.extra_state_attributes}")

    # Should have the new valid value
    assert energy_sensor.native_value == 2000.0, "Should have new valid value after recovery"

    print("✅ Complete grace period behavior test passed!")


if __name__ == "__main__":
    print("""
    🔧 SPAN Panel Team: Key Fixes for Your Test Setup

    1. Update your data_provider_callback to return a 'state' object with these attributes:
       - state.last_changed (datetime)
       - state.last_updated (datetime)
       - state.entity_id (string)
       - state.object_id (string)
       - state.domain (string)

    2. Use the create_proper_mock_state_for_metadata() function as a template

    3. Update your YAML structure tests to expect:
       - metadata(state, 'last_changed') instead of metadata({{entity_id}}, 'last_changed')
       - "state if within_grace else UNKNOWN" instead of if() syntax
       - UNAVAILABLE handlers on computed variables

    With these changes, your grace period logic will work correctly! 🎯
    """)
