"""Test to verify YAML creation when integration starts."""

from pathlib import Path
from typing import Any

import pytest

from tests.factories import SpanPanelApiResponseFactory
from tests.helpers import (
    patch_span_panel_dependencies,
    setup_span_panel_entry_with_cleanup,
)


@pytest.mark.asyncio
async def test_yaml_creation_on_integration_start(hass: Any, enable_custom_integrations: Any):
    """Test that YAML files are created when the integration starts if they don't exist."""

    # Create mock responses
    mock_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    # Configure entry to use device prefix naming (post-1.0.4 style)
    options = {
        "use_device_prefix": True,
        "use_circuit_numbers": False,
    }
    entry, _ = setup_span_panel_entry_with_cleanup(hass, mock_responses, options=options)

    # Ensure YAML files don't exist initially
    config_dir = Path(hass.config.config_dir) / "custom_components" / "span_panel"
    span_sensors_yaml = config_dir / "span_sensors.yaml"

    # Remove YAML files if they exist
    if span_sensors_yaml.exists():
        span_sensors_yaml.unlink()

    assert not span_sensors_yaml.exists(), "YAML file should not exist initially"

    with patch_span_panel_dependencies(mock_responses, options) as (mock_panel, mock_api):
        # Setup integration - this should create the YAML files
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Check that YAML file was created
        assert span_sensors_yaml.exists(), (
            f"YAML file should have been created at {span_sensors_yaml}"
        )

        # Check that the file has content
        with open(span_sensors_yaml) as f:
            content = f.read()
            assert len(content) > 0, "YAML file should not be empty"
            assert "sensors:" in content, "YAML file should contain sensors section"

        print(f"✅ YAML file created successfully at {span_sensors_yaml}")
        print(f"File size: {span_sensors_yaml.stat().st_size} bytes")


@pytest.mark.asyncio
async def test_yaml_content_structure(hass: Any, enable_custom_integrations: Any):
    """Test that the created YAML has the expected structure."""

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

        # Get the YAML file path
        config_dir = Path(hass.config.config_dir) / "custom_components" / "span_panel"
        span_sensors_yaml = config_dir / "span_sensors.yaml"

        assert span_sensors_yaml.exists(), "YAML file should exist"

        # Parse YAML content
        import yaml

        with open(span_sensors_yaml) as f:
            yaml_content = yaml.safe_load(f)

        assert "sensors" in yaml_content, "YAML should have sensors section"

        sensors = yaml_content["sensors"]
        assert len(sensors) > 0, "Should have at least some sensors defined"

        # Check for expected sensor types
        sensor_keys = list(sensors.keys())

        # Should have circuit sensors (look for circuit number and power pattern)
        circuit_power_sensors = [
            k for k in sensor_keys if "_power" in k and any(f"_{i}_" in k for i in range(1, 33))
        ]
        assert len(circuit_power_sensors) > 0, (
            f"Should have circuit power sensors, found keys: {sensor_keys}"
        )

        # Should have panel sensors (look for current_power or feed_through_power patterns)
        panel_sensors = [
            k for k in sensor_keys if "current_power" in k or "feed_through_power" in k
        ]
        assert len(panel_sensors) > 0, f"Should have panel sensors, found keys: {sensor_keys}"

        print(f"✅ YAML structure validated with {len(sensors)} sensors")
        print(f"Circuit sensors: {len(circuit_power_sensors)}")
        print(f"Panel sensors: {len(panel_sensors)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
