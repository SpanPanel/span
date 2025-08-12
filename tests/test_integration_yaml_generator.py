"""Test the integration-driven YAML generator.

This test validates that the YAML generator correctly creates fixtures
using the integration's actual sensor creation code with simulation data.
"""

import pytest

from homeassistant.core import HomeAssistant

from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from tests.utils.integration_yaml_generator import IntegrationYAMLGenerator


@pytest.mark.asyncio
async def test_yaml_generator_basic_functionality(hass: HomeAssistant) -> None:
    """Test that YAML generator produces valid YAML using integration code paths."""
    # Set up the YAML generator
    yaml_generator = IntegrationYAMLGenerator()

    # Test basic naming pattern: legacy no prefix
    naming_flags = {
        USE_DEVICE_PREFIX: False,
        USE_CIRCUIT_NUMBERS: False,
    }

    # Generate YAML using integration's actual code
    yaml_content = await yaml_generator.generate_yaml_for_naming_pattern(
        hass=hass,
        naming_flags=naming_flags
    )

    # Validate we got YAML content
    assert yaml_content is not None
    assert len(yaml_content) > 0

    # Parse YAML to ensure it's valid
    import yaml
    yaml_data = yaml.safe_load(yaml_content)
    assert isinstance(yaml_data, dict)

    # Should contain synthetic sensors structure
    assert "synthetic_sensors" in yaml_data
    sensors = yaml_data["synthetic_sensors"]
    assert isinstance(sensors, dict)

    # Should have some sensors (simulation data should provide circuits)
    assert len(sensors) > 0

    print(f"Generated {len(sensors)} synthetic sensors")
    print("Sample YAML content:")
    print(yaml_content[:500] + "..." if len(yaml_content) > 500 else yaml_content)


@pytest.mark.asyncio
async def test_yaml_generator_all_naming_patterns(hass: HomeAssistant) -> None:
    """Test YAML generation for all entity naming patterns."""
    yaml_generator = IntegrationYAMLGenerator()

    # Generate YAML for all naming patterns
    yaml_fixtures = await yaml_generator.generate_yaml_for_all_patterns(hass)

    # Should have all expected patterns
    expected_patterns = {
        "legacy_no_prefix",
        "device_prefix_friendly",
        "device_prefix_circuit_numbers",
        "circuit_numbers_only"
    }

    assert set(yaml_fixtures.keys()) == expected_patterns

    # Each pattern should produce valid YAML
    import yaml
    for pattern_name, yaml_content in yaml_fixtures.items():
        assert yaml_content is not None
        assert len(yaml_content) > 0

        yaml_data = yaml.safe_load(yaml_content)
        assert isinstance(yaml_data, dict)
        assert "synthetic_sensors" in yaml_data

        print(f"Pattern '{pattern_name}' generated {len(yaml_data['synthetic_sensors'])} sensors")


@pytest.mark.asyncio
async def test_yaml_generator_with_solar_enabled(hass: HomeAssistant) -> None:
    """Test YAML generation with solar configuration enabled."""
    yaml_generator = IntegrationYAMLGenerator()

    naming_flags = {
        USE_DEVICE_PREFIX: True,
        USE_CIRCUIT_NUMBERS: False,
    }

    # Generate YAML with solar enabled
    yaml_content = await yaml_generator.generate_yaml_with_solar_enabled(
        hass=hass,
        naming_flags=naming_flags,
        leg1_circuit=30,
        leg2_circuit=32
    )

    # Parse and validate
    import yaml
    yaml_data = yaml.safe_load(yaml_content)
    sensors = yaml_data["synthetic_sensors"]

    # Should include solar sensors
    solar_sensor_names = [name for name in sensors if "solar" in name.lower()]
    assert len(solar_sensor_names) > 0, "Should include solar sensors when enabled"

    print(f"Generated {len(solar_sensor_names)} solar sensors: {solar_sensor_names}")


@pytest.mark.asyncio
async def test_yaml_generator_naming_pattern_differences(hass: HomeAssistant) -> None:
    """Test that different naming patterns produce different entity IDs."""
    yaml_generator = IntegrationYAMLGenerator()

    # Generate YAML for two different naming patterns
    legacy_yaml = await yaml_generator.generate_yaml_for_naming_pattern(
        hass=hass,
        naming_flags={USE_DEVICE_PREFIX: False, USE_CIRCUIT_NUMBERS: False}
    )

    prefix_yaml = await yaml_generator.generate_yaml_for_naming_pattern(
        hass=hass,
        naming_flags={USE_DEVICE_PREFIX: True, USE_CIRCUIT_NUMBERS: False}
    )

    # Parse both YAML outputs
    import yaml
    legacy_data = yaml.safe_load(legacy_yaml)
    prefix_data = yaml.safe_load(prefix_yaml)

    legacy_sensors = legacy_data["synthetic_sensors"]
    prefix_sensors = prefix_data["synthetic_sensors"]

    # Should have different entity IDs due to different naming patterns
    legacy_entity_ids = set(legacy_sensors.keys())
    prefix_entity_ids = set(prefix_sensors.keys())

    # Entity IDs should be different (though there might be some overlap)
    assert legacy_entity_ids != prefix_entity_ids, "Different naming patterns should produce different entity IDs"

    print(f"Legacy pattern entity IDs: {sorted(legacy_entity_ids)[:5]}...")
    print(f"Prefix pattern entity IDs: {sorted(prefix_entity_ids)[:5]}...")
