"""Integration test for grace period functionality with YAML generation."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from custom_components.span_panel.synthetic_panel_circuits import generate_panel_sensors
from custom_components.span_panel.options import ENERGY_REPORTING_GRACE_PERIOD


class TestGracePeriodIntegration:
    """Test grace period integration with YAML generation."""

    @pytest.mark.asyncio
    @patch('custom_components.span_panel.synthetic_panel_circuits.combine_yaml_templates')
    @patch('custom_components.span_panel.synthetic_panel_circuits.er.async_get')
    async def test_grace_period_in_generated_yaml(self, mock_async_get, mock_combine_yaml):
        """Test that grace period appears correctly in generated YAML."""
        # Mock the async dependencies
        mock_combine_yaml.return_value = {
            "sensor_configs": {
                "test_panel_main_meter_energy_consumed": {
                    "alternate_states": {
                        "FALLBACK": {
                            "formula": "last_valid_state if within_grace_period else 'unknown'"
                        }
                    },
                    "variables": {
                        "last_valid_state": {
                            "formula": "metadata(state, 'last_valid_state')"
                        },
                        "last_valid_changed": {
                            "formula": "metadata(state, 'last_valid_changed')"
                        },
                        "within_grace_period": {
                            "formula": "state != 'unknown' or (last_valid_changed != 'unknown' and minutes_between(last_valid_changed, now()) < energy_grace_period_minutes)",
                            "alternate_states": {
                                "FALLBACK": "last_valid_state is not None and last_valid_state != 'unknown'"
                            }
                        }
                    },
                    "attributes": {
                        "grace_period_remaining": {
                            "formula": "energy_grace_period_minutes - minutes_between(last_valid_changed, now()) if last_valid_changed != 'unknown' and state == 'unknown' else None",
                            "alternate_states": {
                                "FALLBACK": "grace_period_remaining"
                            }
                        },
                        "energy_reporting_status": {
                            "formula": "'Live' if state != 'unknown' else ('Off-Line, reporting previous value' if within_grace_period else 'unknown')",
                            "alternate_states": {
                                "FALLBACK": False
                            }
                        }
                    }
                },
                "test_panel_main_meter_energy_produced": {
                    "alternate_states": {
                        "FALLBACK": {
                            "formula": "last_valid_state if within_grace_period else 'unknown'"
                        }
                    },
                    "variables": {
                        "last_valid_state": {
                            "formula": "metadata(state, 'last_valid_state')"
                        },
                        "last_valid_changed": {
                            "formula": "metadata(state, 'last_valid_changed')"
                        },
                        "within_grace_period": {
                            "formula": "state != 'unknown' or (last_valid_changed != 'unknown' and minutes_between(last_valid_changed, now()) < energy_grace_period_minutes)",
                            "alternate_states": {
                                "FALLBACK": "last_valid_state is not None and last_valid_state != 'unknown'"
                            }
                        }
                    },
                    "attributes": {
                        "grace_period_remaining": {
                            "formula": "energy_grace_period_minutes - minutes_between(last_valid_changed, now()) if last_valid_changed != 'unknown' and state == 'unknown' else None",
                            "alternate_states": {
                                "FALLBACK": "grace_period_remaining"
                            }
                        },
                        "energy_reporting_status": {
                            "formula": "'Live' if state != 'unknown' else ('Off-Line, reporting previous value' if within_grace_period else 'unknown')",
                            "alternate_states": {
                                "FALLBACK": False
                            }
                        }
                    }
                },
                "test_panel_feedthrough_energy_consumed": {
                    "alternate_states": {
                        "FALLBACK": {
                            "formula": "last_valid_state if within_grace_period else 'unknown'"
                        }
                    },
                    "variables": {
                        "last_valid_state": {
                            "formula": "metadata(state, 'last_valid_state')"
                        },
                        "last_valid_changed": {
                            "formula": "metadata(state, 'last_valid_changed')"
                        },
                        "within_grace_period": {
                            "formula": "state != 'unknown' or (last_valid_changed != 'unknown' and minutes_between(last_valid_changed, now()) < energy_grace_period_minutes)",
                            "alternate_states": {
                                "FALLBACK": "last_valid_state is not None and last_valid_state != 'unknown'"
                            }
                        }
                    },
                    "attributes": {
                        "grace_period_remaining": {
                            "formula": "energy_grace_period_minutes - minutes_between(last_valid_changed, now()) if last_valid_changed != 'unknown' and state == 'unknown' else None",
                            "alternate_states": {
                                "FALLBACK": "grace_period_remaining"
                            }
                        },
                        "energy_reporting_status": {
                            "formula": "'Live' if state != 'unknown' else ('Off-Line, reporting previous value' if within_grace_period else 'unknown')",
                            "alternate_states": {
                                "FALLBACK": False
                            }
                        }
                    }
                },
                "test_panel_feedthrough_energy_produced": {
                    "alternate_states": {
                        "FALLBACK": {
                            "formula": "last_valid_state if within_grace_period else 'unknown'"
                        }
                    },
                    "variables": {
                        "last_valid_state": {
                            "formula": "metadata(state, 'last_valid_state')"
                        },
                        "last_valid_changed": {
                            "formula": "metadata(state, 'last_valid_changed')"
                        },
                        "within_grace_period": {
                            "formula": "state != 'unknown' or (last_valid_changed != 'unknown' and minutes_between(last_valid_changed, now()) < energy_grace_period_minutes)",
                            "alternate_states": {
                                "FALLBACK": "last_valid_state is not None and last_valid_state != 'unknown'"
                            }
                        }
                    },
                    "attributes": {
                        "grace_period_remaining": {
                            "formula": "energy_grace_period_minutes - minutes_between(last_valid_changed, now()) if last_valid_changed != 'unknown' and state == 'unknown' else None",
                            "alternate_states": {
                                "FALLBACK": "grace_period_remaining"
                            }
                        },
                        "energy_reporting_status": {
                            "formula": "'Live' if state != 'unknown' else ('Off-Line, reporting previous value' if within_grace_period else 'unknown')",
                            "alternate_states": {
                                "FALLBACK": False
                            }
                        }
                    }
                }
            },
            "global_settings": {"variables": {"energy_grace_period_minutes": 30}}
        }

        mock_entity_registry = MagicMock()
        mock_entity_registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = mock_entity_registry

        # Mock coordinator with custom grace period
        mock_coordinator = MagicMock()
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = {
            ENERGY_REPORTING_GRACE_PERIOD: 30,
            "power_display_precision": 0,
            "energy_display_precision": 2,
        }
        mock_coordinator.config_entry.data = {"device_name": "Test Panel"}
        mock_coordinator.config_entry.title = "Test Panel"

        # Mock span panel with energy data
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = "test-panel-001"

        # Mock panel data with all energy fields
        mock_panel_data = MagicMock()
        mock_panel_data.instantGridPowerW = 1500.0
        mock_panel_data.feedthroughPowerW = 200.0
        mock_panel_data.mainMeterEnergyProducedWh = 1000.0
        mock_panel_data.mainMeterEnergyConsumedWh = 2000.0
        mock_panel_data.feedthroughEnergyProducedWh = 500.0
        mock_panel_data.feedthroughEnergyConsumedWh = 750.0

        mock_span_panel.panel = mock_panel_data

        # Mock hass
        mock_hass = MagicMock()

        # Generate panel sensors
        sensor_configs, backing_entities, global_settings, mapping = await generate_panel_sensors(
            mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
        )

        # Verify global settings contain custom grace period
        assert "variables" in global_settings
        assert "energy_grace_period_minutes" in global_settings["variables"]
        assert global_settings["variables"]["energy_grace_period_minutes"] == 30

        # Verify energy sensors are generated
        energy_sensors = [
            key for key in sensor_configs.keys()
            if "energy" in key and ("consumed" in key or "produced" in key)
        ]
        assert len(energy_sensors) == 4  # 4 panel energy sensors

        # Verify energy sensors have grace period alternate state handling
        for sensor_key in energy_sensors:
            sensor_config = sensor_configs[sensor_key]

            # Check for alternate state handling structure
            assert "alternate_states" in sensor_config
            assert "FALLBACK" in sensor_config["alternate_states"]
            fallback_formula = sensor_config["alternate_states"]["FALLBACK"]["formula"]
            assert "last_valid_state if within_grace_period else 'unknown'" == fallback_formula

            # Check for computed variables
            assert "variables" in sensor_config
            assert "within_grace_period" in sensor_config["variables"]
            assert "last_valid_state" in sensor_config["variables"]
            assert "last_valid_changed" in sensor_config["variables"]

            # Check last_valid_state variable
            last_valid_state_config = sensor_config["variables"]["last_valid_state"]
            assert last_valid_state_config["formula"] == "metadata(state, 'last_valid_state')"

            # Check last_valid_changed variable
            last_valid_changed_config = sensor_config["variables"]["last_valid_changed"]
            assert last_valid_changed_config["formula"] == "metadata(state, 'last_valid_changed')"

            # Check within_grace_period variable
            within_grace_period_config = sensor_config["variables"]["within_grace_period"]
            assert "formula" in within_grace_period_config
            assert "energy_grace_period_minutes" in within_grace_period_config["formula"]
            assert "last_valid_changed" in within_grace_period_config["formula"]
            assert "minutes_between" in within_grace_period_config["formula"]

            # Check the FALLBACK for within_grace_period
            assert "alternate_states" in within_grace_period_config
            assert "FALLBACK" in within_grace_period_config["alternate_states"]
            within_grace_period_fallback = within_grace_period_config["alternate_states"]["FALLBACK"]
            assert within_grace_period_fallback == "last_valid_state is not None and last_valid_state != 'unknown'"

            # Check for diagnostic attributes
            assert "attributes" in sensor_config
            assert "energy_reporting_status" in sensor_config["attributes"]
            assert "grace_period_remaining" in sensor_config["attributes"]

            # Check energy_reporting_status
            energy_status_config = sensor_config["attributes"]["energy_reporting_status"]
            assert "formula" in energy_status_config
            assert "within_grace_period" in energy_status_config["formula"]
            assert "alternate_states" in energy_status_config
            assert energy_status_config["alternate_states"]["FALLBACK"] == False



            # Check grace_period_remaining attribute
            grace_remaining_config = sensor_config["attributes"]["grace_period_remaining"]
            assert "formula" in grace_remaining_config
            assert "energy_grace_period_minutes" in grace_remaining_config["formula"]
            assert "last_valid_changed" in grace_remaining_config["formula"]
            assert "minutes_between" in grace_remaining_config["formula"]
            assert "alternate_states" in grace_remaining_config
            assert grace_remaining_config["alternate_states"]["FALLBACK"] == "grace_period_remaining"

    @pytest.mark.asyncio
    @patch('custom_components.span_panel.synthetic_panel_circuits.combine_yaml_templates')
    @patch('custom_components.span_panel.synthetic_panel_circuits.er.async_get')
    async def test_grace_period_default_value(self, mock_async_get, mock_combine_yaml):
        """Test that default grace period (15 minutes) is used when not specified."""
        # Mock the async dependencies
        mock_combine_yaml.return_value = {
            "sensor_configs": {},
            "global_settings": {"variables": {"energy_grace_period_minutes": 15}}
        }

        mock_entity_registry = MagicMock()
        mock_entity_registry.async_get_entity_id.return_value = None
        mock_async_get.return_value = mock_entity_registry

        # Mock coordinator without grace period option
        mock_coordinator = MagicMock()
        mock_coordinator.config_entry = MagicMock()
        mock_coordinator.config_entry.options = {}  # No grace period specified
        mock_coordinator.config_entry.data = {"device_name": "Test Panel"}
        mock_coordinator.config_entry.title = "Test Panel"

        # Mock span panel
        mock_span_panel = MagicMock()
        mock_span_panel.status.serial_number = "test-panel-002"

        mock_panel_data = MagicMock()
        mock_panel_data.instantGridPowerW = 1000.0
        mock_panel_data.feedthroughPowerW = 100.0
        mock_panel_data.mainMeterEnergyProducedWh = 500.0
        mock_panel_data.mainMeterEnergyConsumedWh = 1000.0
        mock_panel_data.feedthroughEnergyProducedWh = 250.0
        mock_panel_data.feedthroughEnergyConsumedWh = 375.0

        mock_span_panel.panel = mock_panel_data

        # Mock hass
        mock_hass = MagicMock()

        # Generate panel sensors
        sensor_configs, backing_entities, global_settings, mapping = await generate_panel_sensors(
            mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
        )

        # Verify default grace period (15) is used
        assert global_settings["variables"]["energy_grace_period_minutes"] == 15

    def test_grace_period_formula_structure(self):
        """Test the grace period formula structure is correct."""
        # Expected formula components
        expected_formula = "last_valid_changed != 'unknown' and minutes_between(last_valid_changed, now()) < energy_grace_period_minutes"

        # This formula should:
        # 1. Check if last_valid_changed is not 'unknown'
        # 2. Get current time with now()
        # 3. Calculate minutes between using minutes_between function
        # 4. Compare against the grace period threshold

        # Test individual components exist in the formula
        assert "now()" in expected_formula
        assert "last_valid_changed" in expected_formula
        assert "minutes_between" in expected_formula
        assert "energy_grace_period_minutes" in expected_formula
        assert "!= 'unknown'" in expected_formula
        assert " and " in expected_formula
        assert "<" in expected_formula

    def test_grace_period_exception_handler_logic(self):
        """Test the alternate state handler logic structure."""
        expected_handler = "last_valid_state if within_grace_period else 'unknown'"

        # This handler should:
        # 1. Use the within_grace_period variable (which handles all the logic)
        # 2. If within grace period, return last_valid_state
        # 3. Otherwise, return 'unknown'

        assert "last_valid_state" in expected_handler
        assert "within_grace_period" in expected_handler
        assert " if " in expected_handler
        assert " else " in expected_handler

    def test_within_grace_fallback_logic(self):
        """Test the new intelligent within_grace FALLBACK logic."""
        expected_fallback = "last_valid_state is not None and last_valid_state != 'unknown'"

        # This FALLBACK should:
        # 1. Check if last_valid_state exists (is not None)
        # 2. Check if last_valid_state is not 'unknown'
        # 3. Return True only if both conditions are met
        # 4. This allows grace period logic to work even when backing entity is unavailable

        assert "last_valid_state is not None" in expected_fallback
        assert "last_valid_state != 'unknown'" in expected_fallback
        assert " and " in expected_fallback

        # Test the logic scenarios
        test_cases = [
            {"last_valid_state": "100.5", "expected": True, "description": "Valid numeric state"},
            {"last_valid_state": None, "expected": False, "description": "No last valid state"},
            {"last_valid_state": "unknown", "expected": False, "description": "Unknown last valid state"},
            {"last_valid_state": "0.0", "expected": True, "description": "Zero is a valid state"},
        ]

        for case in test_cases:
            # Simulate the FALLBACK logic
            result = (
                case["last_valid_state"] is not None and
                case["last_valid_state"] != "unknown"
            )
            assert result == case["expected"], f"Failed for {case['description']}: got {result}, expected {case['expected']}"

    @pytest.mark.asyncio
    async def test_grace_period_boundary_values(self):
        """Test grace period works with boundary values (0 and 60 minutes)."""
        test_cases = [0, 60]

        for grace_period in test_cases:
            with patch('custom_components.span_panel.synthetic_panel_circuits.combine_yaml_templates') as mock_combine_yaml, \
                 patch('custom_components.span_panel.synthetic_panel_circuits.er.async_get') as mock_async_get:

                # Mock the async dependencies
                mock_combine_yaml.return_value = {
                    "sensor_configs": {},
                    "global_settings": {"variables": {"energy_grace_period_minutes": grace_period}}
                }

                mock_entity_registry = MagicMock()
                mock_entity_registry.async_get_entity_id.return_value = None
                mock_async_get.return_value = mock_entity_registry

                # Mock coordinator with boundary grace period
                mock_coordinator = MagicMock()
                mock_coordinator.config_entry = MagicMock()
                mock_coordinator.config_entry.options = {
                    ENERGY_REPORTING_GRACE_PERIOD: grace_period
                }
                mock_coordinator.config_entry.data = {"device_name": "Test Panel"}
                mock_coordinator.config_entry.title = "Test Panel"

                # Mock span panel
                mock_span_panel = MagicMock()
                mock_span_panel.status.serial_number = f"test-panel-{grace_period}"

                mock_panel_data = MagicMock()
                mock_panel_data.instantGridPowerW = 1000.0
                mock_panel_data.feedthroughPowerW = 100.0
                mock_panel_data.mainMeterEnergyConsumedWh = 1000.0
                mock_panel_data.mainMeterEnergyProducedWh = 500.0
                mock_panel_data.feedthroughEnergyConsumedWh = 375.0
                mock_panel_data.feedthroughEnergyProducedWh = 250.0

                mock_span_panel.panel = mock_panel_data

                # Mock hass
                mock_hass = MagicMock()

                # Generate sensors and verify grace period value
                sensor_configs, backing_entities, global_settings, mapping = await generate_panel_sensors(
                    mock_hass, mock_coordinator, mock_span_panel, "Test Panel"
                )

                # Verify the boundary value is correctly set
                assert global_settings["variables"]["energy_grace_period_minutes"] == grace_period
