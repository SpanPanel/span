"""Test validation of all YAML fixture files against ha-synthetic-sensors schema."""

from pathlib import Path
from typing import Any

from ha_synthetic_sensors.schema_validator import SchemaValidator
import pytest
import yaml


def load_fixture_configs() -> list[tuple[str, dict[str, Any]]]:
    """Load all fixture YAML configurations."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    yaml_files = list(fixtures_dir.glob("*.yaml"))

    configs = []
    for yaml_file in sorted(yaml_files):
        try:
            with open(yaml_file) as f:
                config = yaml.safe_load(f)

            # Skip empty or None configs
            if not config:
                continue

            # Add version if missing (some fixtures might not have it)
            if "version" not in config:
                config["version"] = "1.0"

            configs.append((yaml_file.name, config))
        except Exception as e:
            # Include files that fail to load so we can see the error
            configs.append((yaml_file.name, {"_load_error": str(e)}))

    return configs


@pytest.mark.parametrize("filename,config", load_fixture_configs())
def test_fixture_validation(filename: str, config: dict[str, Any]) -> None:
    """Test that each fixture file passes ha-synthetic-sensors schema validation."""
    # Check for load errors first
    if "_load_error" in config:
        pytest.fail(f"{filename} failed to load: {config['_load_error']}")

    validator = SchemaValidator()
    result = validator.validate_config(config)

    assert result["valid"] is True, f"{filename} has validation errors: {result['errors']}"
    assert len(result["errors"]) == 0, f"{filename} has unexpected errors: {result['errors']}"


def test_fixture_directory_exists():
    """Ensure the fixtures directory exists and has YAML files."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    assert fixtures_dir.exists(), "Fixtures directory does not exist"

    yaml_files = list(fixtures_dir.glob("*.yaml"))
    assert len(yaml_files) > 0, "No YAML fixture files found"


def test_validate_known_good_format():
    """Test validation with a known good format to ensure our validator works."""
    good_config = {
        "version": "1.0",
        "global_settings": {"device_identifier": "test_device"},
        "sensors": {
            "test_sensor": {
                "name": "Test Sensor",
                "entity_id": "sensor.test_sensor",
                "formula": "value",
                "variables": {"value": "sensor.source_entity"},
                "metadata": {
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                },
            }
        },
    }

    validator = SchemaValidator()
    result = validator.validate_config(good_config)

    assert result["valid"] is True, f"Known good config failed validation: {result['errors']}"


def test_validate_known_bad_format():
    """Test validation with a known bad format to ensure our validator catches errors."""
    bad_config = {
        "version": "1.0",
        "sensors": {
            "test_sensor": {
                "name": "Test Sensor",
                # Missing required 'formula' field
                "variables": {},
                "unit_of_measurement": "W",
            }
        },
    }

    validator = SchemaValidator()
    result = validator.validate_config(bad_config)

    assert result["valid"] is False, "Known bad config should fail validation"
    assert len(result["errors"]) > 0, "Expected validation errors for bad config"
