"""Test to verify sensor set creation when integration starts using synthetic sensors package."""

from typing import Any

import pytest

from tests.factories import SpanPanelApiResponseFactory
from tests.helpers import (
    patch_span_panel_dependencies,
    setup_span_panel_entry_with_cleanup,
)


@pytest.mark.asyncio
async def test_sensor_set_creation_through_synthetic_package(
    hass: Any,
    enable_custom_integrations: Any,
    mock_ha_storage,
    mock_synthetic_sensor_manager
):
    """Test that sensor set is created through ha-synthetic-sensors package when integration starts."""

    # Create mock responses
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry_with_cleanup(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options) as (mock_panel, mock_api):
        # Setup integration - this should create the sensor set through synthetic sensors package
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get the sensor manager from the integration
        from custom_components.span_panel.const import DOMAIN

        domain_data = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry.entry_id, {})
        sensor_manager = entry_data.get("sensor_manager")

        assert sensor_manager is not None, "Sensor manager should be created"

        # Test through the synthetic sensors package - check storage was used
        assert len(mock_ha_storage) > 0, "Storage should contain sensor set data"

        # Verify sensor set exists in synthetic sensors storage
        sensor_set = await mock_synthetic_sensor_manager.get_sensor_set()
        assert sensor_set is not None, "Sensor set should exist in synthetic sensors storage"

        # Verify sensors were created through the synthetic package
        sensor_configs = await sensor_set.async_get_all_sensor_configs()
        assert len(sensor_configs) > 0, "Sensor set should contain sensor configurations"

        # Verify the sensors use device prefix naming as configured
        for _sensor_id, config in sensor_configs.items():
            entity_id = config.get("entity_id", "")
            assert "span_panel" in entity_id, f"Entity ID should use device prefix: {entity_id}"

        print(f"✅ Sensor set created through synthetic sensors package with {len(sensor_configs)} sensors")


@pytest.mark.asyncio
async def test_yaml_content_structure(hass: Any, enable_custom_integrations: Any):
    """Test that the created sensor set has the expected structure."""

    # Create mock responses with known circuit data
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry_with_cleanup(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options) as (mock_panel, mock_api):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get the sensor manager from the integration
        from custom_components.span_panel.const import DOMAIN

        domain_data = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry.entry_id, {})
        sensor_manager = entry_data.get("sensor_manager")

        assert sensor_manager is not None, "Sensor manager should be created"

        # Get the config manager and read the configuration
        config_manager = await sensor_manager._get_config_manager()
        config_content = await config_manager.read_config()

        assert "sensors" in config_content, "Configuration should have sensors section"

        sensors = config_content["sensors"]
        assert len(sensors) > 0, "Should have at least some sensors defined"

        # Check for expected sensor types
        sensor_keys = list(sensors.keys())

        # Should have circuit sensors (look for friendly name patterns)
        circuit_sensors = [
            k
            for k in sensor_keys
            if any(name in k for name in ["kitchen_outlets", "living_room_lights", "solar_panels"])
        ]
        assert len(circuit_sensors) > 0, f"Should have circuit sensors, found keys: {sensor_keys}"

        # Should have panel sensors (look for current_power or feed_through_power patterns)
        panel_sensors = [
            k
            for k in sensor_keys
            if "current_power" in k or "feed_through_power" in k or "main_meter" in k
        ]
        assert len(panel_sensors) > 0, f"Should have panel sensors, found keys: {sensor_keys}"

        print(f"✅ Sensor set structure validated with {len(sensors)} sensors")
        print(f"Circuit sensors: {len(circuit_sensors)}")
        print(f"Panel sensors: {len(panel_sensors)}")


@pytest.mark.asyncio
async def test_yaml_export_content(hass: Any, enable_custom_integrations: Any):
    """Test to see what the actual YAML export looks like."""

    # Create mock responses with known circuit data
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry_with_cleanup(hass, mock_responses, options=options)

    with patch_span_panel_dependencies(mock_responses, options) as (mock_panel, mock_api):
        # Setup integration
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Get the sensor manager from the integration
        from custom_components.span_panel.const import DOMAIN

        domain_data = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry.entry_id, {})
        sensor_manager = entry_data.get("sensor_manager")

        assert sensor_manager is not None, "Sensor manager should be created"

        # Get the config manager and export YAML
        config_manager = sensor_manager._config_manager

        # Get the sensor set and export YAML directly
        await config_manager._ensure_initialized()
        sensor_set = config_manager._sensor_set

        if sensor_set:
            yaml_content = sensor_set.export_yaml()
            print("\n" + "=" * 50)
            print("ACTUAL YAML EXPORT CONTENT:")
            print("=" * 50)
            print(yaml_content)
            print("=" * 50)

            # Also print the dict format from read_config
            config_dict = await config_manager.read_config()
            print("\nCONFIG DICT FORMAT:")
            print("=" * 50)
            import yaml

            print(yaml.dump(config_dict, default_flow_style=False, sort_keys=False))
            print("=" * 50)

        # Just pass the test - we want to see the output
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
