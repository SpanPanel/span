"""Test YAML regeneration when entity naming patterns change."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant

from custom_components.span_panel.const import (
    DOMAIN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
)
from custom_components.span_panel.synthetic_bridge import SyntheticSensorsBridge
from tests.common import create_mock_config_entry


class TestYamlRegeneration:
    """Test YAML regeneration when entity naming patterns change."""

    @pytest.fixture
    def hass_with_solar_data(self, hass):
        """Create hass instance with solar coordinator data for both patterns."""
        # Create coordinators for both patterns
        coordinators = {}

        for pattern_name, config_flags in [
            ("friendly", {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}),
            ("circuit", {USE_CIRCUIT_NUMBERS: True, USE_DEVICE_PREFIX: True}),
        ]:
            # Create config entry
            config_entry = create_mock_config_entry({"host": "192.168.1.100"}, config_flags)
            config_entry.entry_id = f"test_entry_id_{pattern_name}"

            # Create coordinator with mock data
            coordinator = MagicMock()
            coordinator.data = MagicMock()

            # Create proper mock circuits that SyntheticSensorsBridge expects
            # The bridge looks for circuits with IDs like "unmapped_tab_15", "unmapped_tab_30", etc.
            mock_circuit_30 = MagicMock()
            mock_circuit_30.name = "Unmapped Tab 30"
            mock_circuit_30.circuit_id = "unmapped_tab_30"

            mock_circuit_32 = MagicMock()
            mock_circuit_32.name = "Unmapped Tab 32"
            mock_circuit_32.circuit_id = "unmapped_tab_32"

            coordinator.data.circuits = {
                "unmapped_tab_30": mock_circuit_30,
                "unmapped_tab_32": mock_circuit_32,
            }

            # Set up config entry in coordinator for entity ID construction
            coordinator.config_entry = config_entry

            # Store both coordinator and config entry
            coordinators[pattern_name] = {"coordinator": coordinator, "config_entry": config_entry}

        # Store coordinators in hass.data using the correct structure
        hass.data = {
            DOMAIN: {
                "test_entry_id_friendly": {"coordinator": coordinators["friendly"]["coordinator"]},
                "test_entry_id_circuit": {"coordinator": coordinators["circuit"]["coordinator"]},
            }
        }

        # Store coordinators info for easy access in tests
        hass._test_coordinators = coordinators
        return hass

    async def test_yaml_contains_proper_entity_ids_for_different_naming_patterns(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that SyntheticSensorsBridge generates different entity IDs based on naming pattern."""

        with tempfile.TemporaryDirectory() as temp_dir:
            # Get the config entries from the hass fixture
            circuit_config = hass_with_solar_data._test_coordinators["circuit"]["config_entry"]
            friendly_config = hass_with_solar_data._test_coordinators["friendly"]["config_entry"]

            # Debug: Check the config entry options
            print(f"Circuit pattern config options: {circuit_config.options}")

            # First, generate YAML with circuit numbers pattern
            bridge_circuit = SyntheticSensorsBridge(hass_with_solar_data, circuit_config, temp_dir)
            await bridge_circuit.generate_solar_config(
                30, 32
            )  # Use circuits 30, 32 to match fixture

            circuit_yaml_path = Path(temp_dir) / "span-ha-synthetic.yaml"
            if not circuit_yaml_path.exists():
                # If generation failed, let's check what files were created
                files = list(Path(temp_dir).glob("*"))
                pytest.fail(f"Circuit pattern YAML not generated. Files in temp_dir: {files}")

            with open(circuit_yaml_path, encoding="utf-8") as f:
                generated_circuit_yaml = yaml.safe_load(f)

            # Now generate YAML with friendly names pattern (in a new temp dir to avoid conflicts)
            with tempfile.TemporaryDirectory() as temp_dir2:
                # Debug: Check the config entry options
                print(f"Friendly pattern config options: {friendly_config.options}")

                bridge_friendly = SyntheticSensorsBridge(
                    hass_with_solar_data, friendly_config, temp_dir2
                )
                await bridge_friendly.generate_solar_config(30, 32)  # Same circuits

                friendly_yaml_path = Path(temp_dir2) / "span-ha-synthetic.yaml"
                if not friendly_yaml_path.exists():
                    files = list(Path(temp_dir2).glob("*"))
                    pytest.fail(f"Friendly pattern YAML not generated. Files in temp_dir2: {files}")

                with open(friendly_yaml_path, encoding="utf-8") as f:
                    generated_friendly_yaml = yaml.safe_load(f)

            # Load reference fixture files for comparison
            fixtures_dir = Path(__file__).parent / "fixtures"

            with open(
                fixtures_dir / "span-ha-synthetic-circuit-pattern.yaml", encoding="utf-8"
            ) as f:
                expected_circuit_yaml = yaml.safe_load(f)

            with open(
                fixtures_dir / "span-ha-synthetic-friendly-pattern.yaml", encoding="utf-8"
            ) as f:
                expected_friendly_yaml = yaml.safe_load(f)

            # Verify generated circuit pattern matches expected pattern
            circuit_sensors = generated_circuit_yaml.get("sensors", {})
            expected_circuit_sensors = expected_circuit_yaml.get("sensors", {})

            # Check that we have sensors and they match the expected pattern
            assert len(circuit_sensors) > 0, "Circuit pattern should generate sensors"

            # For debugging: print what was actually generated vs expected
            print(f"Generated circuit sensors: {list(circuit_sensors.keys())}")
            print(f"Expected circuit sensors: {list(expected_circuit_sensors.keys())}")

            # Test key entity IDs from the circuit pattern
            for expected_key, expected_config in expected_circuit_sensors.items():
                if expected_key in circuit_sensors:
                    expected_entity_id = expected_config["entity_id"]
                    actual_entity_id = circuit_sensors[expected_key]["entity_id"]
                    assert actual_entity_id == expected_entity_id, (
                        f"Circuit pattern entity ID mismatch for {expected_key}: "
                        f"expected {expected_entity_id}, got {actual_entity_id}"
                    )

            # Verify generated friendly pattern matches expected pattern
            friendly_sensors = generated_friendly_yaml.get("sensors", {})
            expected_friendly_sensors = expected_friendly_yaml.get("sensors", {})

            assert len(friendly_sensors) > 0, "Friendly pattern should generate sensors"

            print(f"Generated friendly sensors: {list(friendly_sensors.keys())}")
            print(f"Expected friendly sensors: {list(expected_friendly_sensors.keys())}")

            # Test key entity IDs from the friendly pattern
            for expected_key, expected_config in expected_friendly_sensors.items():
                if expected_key in friendly_sensors:
                    expected_entity_id = expected_config["entity_id"]
                    actual_entity_id = friendly_sensors[expected_key]["entity_id"]
                    assert actual_entity_id == expected_entity_id, (
                        f"Friendly pattern entity ID mismatch for {expected_key}: "
                        f"expected {expected_entity_id}, got {actual_entity_id}"
                    )

            # Verify the patterns produce different outputs
            assert generated_circuit_yaml != generated_friendly_yaml, (
                "Circuit and friendly naming patterns should produce different YAML content"
            )

    async def test_yaml_patterns_generate_reverse_direction(
        self,
        hass_with_solar_data: HomeAssistant,
    ):
        """Test that both naming patterns work in reverse direction (friendly → circuit).

        This test validates the same anti-pattern fix but generates friendly names first,
        then circuit numbers, to ensure both directions work correctly.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            # Get the config entries from the hass fixture
            friendly_config = hass_with_solar_data._test_coordinators["friendly"]["config_entry"]
            circuit_config = hass_with_solar_data._test_coordinators["circuit"]["config_entry"]

            # First, generate YAML with friendly names pattern
            bridge_friendly = SyntheticSensorsBridge(
                hass_with_solar_data, friendly_config, temp_dir
            )
            await bridge_friendly.generate_solar_config(30, 32)

            friendly_yaml_path = Path(temp_dir) / "span-ha-synthetic.yaml"
            if not friendly_yaml_path.exists():
                files = list(Path(temp_dir).glob("*"))
                pytest.fail(f"Friendly pattern YAML not generated. Files in temp_dir: {files}")

            with open(friendly_yaml_path, encoding="utf-8") as f:
                generated_friendly_yaml = yaml.safe_load(f)

            # Now generate YAML with circuit numbers pattern (in a new temp dir)
            with tempfile.TemporaryDirectory() as temp_dir2:
                bridge_circuit = SyntheticSensorsBridge(
                    hass_with_solar_data, circuit_config, temp_dir2
                )
                await bridge_circuit.generate_solar_config(30, 32)  # Same circuits

                circuit_yaml_path = Path(temp_dir2) / "span-ha-synthetic.yaml"
                if not circuit_yaml_path.exists():
                    files = list(Path(temp_dir2).glob("*"))
                    pytest.fail(f"Circuit pattern YAML not generated. Files in temp_dir2: {files}")

                with open(circuit_yaml_path, encoding="utf-8") as f:
                    generated_circuit_yaml = yaml.safe_load(f)

            # Extract entity IDs from the generated YAML (reverse order from main test)
            friendly_sensors = list(generated_friendly_yaml.get("sensors", {}).keys())
            circuit_sensors = list(generated_circuit_yaml.get("sensors", {}).keys())

            # Expected entity IDs for friendly names pattern (USE_CIRCUIT_NUMBERS=False)
            expected_friendly_sensors = [
                "span_panel_solar_inverter_instant_power",
                "span_panel_solar_inverter_energy_produced",
                "span_panel_solar_inverter_energy_consumed",
            ]

            # Expected entity IDs for circuit numbers pattern (USE_CIRCUIT_NUMBERS=True)
            expected_circuit_sensors = [
                "span_panel_circuit_30_32_instant_power",
                "span_panel_circuit_30_32_energy_produced",
                "span_panel_circuit_30_32_energy_consumed",
            ]

            # EXPLICIT VALIDATION: Verify each pattern produces the expected entity IDs
            for expected_entity_id in expected_friendly_sensors:
                assert expected_entity_id in friendly_sensors, (
                    f"Friendly pattern missing expected entity: {expected_entity_id}. "
                    f"Generated: {friendly_sensors}"
                )

            for expected_entity_id in expected_circuit_sensors:
                assert expected_entity_id in circuit_sensors, (
                    f"Circuit pattern missing expected entity: {expected_entity_id}. "
                    f"Generated: {circuit_sensors}"
                )

            # Verify entity IDs match exactly (same validation as main test, different order)
            for actual_entity_id, expected_entity_id in zip(
                friendly_sensors, expected_friendly_sensors
            ):
                assert actual_entity_id == expected_entity_id, (
                    f"Friendly pattern entity ID mismatch: "
                    f"expected {expected_entity_id}, got {actual_entity_id}"
                )

            for actual_entity_id, expected_entity_id in zip(
                circuit_sensors, expected_circuit_sensors
            ):
                assert actual_entity_id == expected_entity_id, (
                    f"Circuit pattern entity ID mismatch: "
                    f"expected {expected_entity_id}, got {actual_entity_id}"
                )

            # Verify the patterns produce different outputs
            assert generated_friendly_yaml != generated_circuit_yaml, (
                "Friendly and circuit naming patterns should produce different YAML content"
            )

            # Load reference fixtures to validate against known-good patterns
            fixtures_dir = Path(__file__).parent / "fixtures"

            with open(
                fixtures_dir / "span-ha-synthetic-friendly-pattern.yaml", encoding="utf-8"
            ) as f:
                expected_friendly_yaml = yaml.safe_load(f)

            with open(
                fixtures_dir / "span-ha-synthetic-circuit-pattern.yaml", encoding="utf-8"
            ) as f:
                expected_circuit_yaml = yaml.safe_load(f)

            # Final validation: generated output should match fixture patterns exactly
            assert generated_friendly_yaml == expected_friendly_yaml, (
                "Generated friendly pattern YAML should match the fixture"
            )
            assert generated_circuit_yaml == expected_circuit_yaml, (
                "Generated circuit pattern YAML should match the fixture"
            )
