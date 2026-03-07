"""SPAN Panel Simulation Factory for realistic test data generation.

This factory leverages the span-panel-api simulation mode with YAML configurations
to generate realistic SPAN panel data that exactly matches what the integration expects,
using actual SPAN panel response structures.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING or os.environ.get("SPAN_USE_REAL_SIMULATION", "").lower() in (
    "1",
    "true",
    "yes",
):
    from span_panel_api import SpanPanelClient


class SpanPanelSimulationFactory:
    """Factory for creating simulation-based SPAN panel data using YAML configurations."""

    @classmethod
    async def _get_config_path(cls, config_name: str = "simulation_config_32_circuit") -> str:
        """Get path to a simulation configuration file.

        Args:
            config_name: Name of the config file (without .yaml extension)

        Returns:
            Full path to the configuration file

        """
        # Look for config in the integration's simulation_configs directory
        current_file = Path(__file__)
        integration_root = current_file.parent.parent.parent / "custom_components" / "span_panel"
        config_path = integration_root / "simulation_configs" / f"{config_name}.yaml"

        if await asyncio.to_thread(config_path.exists):
            return str(config_path)

        # Fallback: look in span-panel-api examples
        span_api_examples = current_file.parent.parent.parent.parent / "span-panel-api" / "examples"
        fallback_path = span_api_examples / f"{config_name}.yaml"

        if await asyncio.to_thread(fallback_path.exists):
            return str(fallback_path)

        raise FileNotFoundError(f"Could not find simulation config: {config_name}.yaml")

    @classmethod
    async def create_simulation_client(
        cls,
        host: str = "test-panel-001",
        config_name: str = "simulation_config_32_circuit",
        **kwargs: Any
    ) -> SpanPanelClient:
        """Create a simulation client with YAML-based realistic data.

        Args:
            host: Host identifier (becomes serial number in simulation mode)
            config_name: Name of the YAML config file to use
            **kwargs: Additional client configuration parameters

        Returns:
            SpanPanelClient configured for simulation mode with YAML config

        """
        config_path = await cls._get_config_path(config_name)

        return SpanPanelClient(
            host=host,
            simulation_mode=True,
            simulation_config_path=config_path,
            **kwargs
        )

    @classmethod
    async def get_realistic_panel_data(
        cls,
        host: str = "test-panel-001",
        config_name: str = "simulation_config_32_circuit",
        circuit_overrides: dict[str, dict] | None = None,
        global_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get panel data using YAML-based simulation mode.

        Args:
            host: Host identifier for the simulated panel
            config_name: Name of the YAML config file to use
            circuit_overrides: Per-circuit overrides to apply dynamically
            global_overrides: Global overrides (e.g., power_multiplier)

        Returns:
            Dictionary containing all panel data types the integration needs

        """
        client = await cls.create_simulation_client(host=host, config_name=config_name)
        async with client:
            # Apply any dynamic overrides if specified
            if circuit_overrides or global_overrides:
                await client.set_circuit_overrides(
                    circuit_overrides=circuit_overrides or {},
                    global_overrides=global_overrides or {}
                )

            # Get all data types the integration needs
            circuits = await client.get_circuits()
            panel_state = await client.get_panel_state()
            status = await client.get_status()
            storage = await client.get_storage_soe()

            return {
                'circuits': circuits,
                'panel_state': panel_state,
                'status': status,
                'storage': storage
            }

    @classmethod
    async def get_realistic_circuits_only(
        cls,
        host: str = "test-circuits-001",
        config_name: str = "simulation_config_32_circuit",
        circuit_overrides: dict[str, dict] | None = None,
        global_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get only circuits data for tests that don't need full panel state.

        Args:
            host: Host identifier for the simulated panel
            config_name: Name of the YAML config file to use
            circuit_overrides: Per-circuit overrides to apply dynamically
            global_overrides: Global overrides (e.g., power_multiplier)

        Returns:
            CircuitsOut object from simulation

        """
        client = await cls.create_simulation_client(host=host, config_name=config_name)
        async with client:
            # Apply any dynamic overrides if specified
            if circuit_overrides or global_overrides:
                await client.set_circuit_overrides(
                    circuit_overrides=circuit_overrides or {},
                    global_overrides=global_overrides or {}
                )

            circuits = await client.get_circuits()
            return dict(circuits) if circuits else {}

    @classmethod
    def get_preset_scenarios(cls) -> dict[str, dict[str, Any]]:
        """Get predefined simulation scenarios for common test cases.

        Returns:
            Dictionary of scenario names to simulation parameters

        """
        return {
            "normal_operation": {
                "config_name": "simulation_config_32_circuit",
                "global_overrides": {}
            },
            "high_load": {
                "config_name": "simulation_config_32_circuit",
                "global_overrides": {"power_multiplier": 1.5},
                "circuit_overrides": {
                    "ev_charger_garage": {
                        "power_override": 11000.0,  # Max EV charging
                        "relay_state": "CLOSED"
                    }
                }
            },
            "circuit_failures": {
                "config_name": "simulation_config_32_circuit",
                "circuit_overrides": {
                    "living_room_outlets": {"relay_state": "OPEN"},
                    "office_outlets": {"relay_state": "OPEN"}
                }
            },
            "low_power_stable": {
                "config_name": "simple_test_config",  # Use simpler config
                "global_overrides": {"power_multiplier": 0.3}
            },
            "solar_peak": {
                "config_name": "simulation_config_32_circuit",
                "circuit_overrides": {
                    "solar_inverter_main": {
                        "power_override": -8000.0,  # Peak solar production
                        "relay_state": "CLOSED"
                    }
                }
            },
            "grid_stress": {
                "config_name": "simulation_config_32_circuit",
                "global_overrides": {"power_multiplier": 2.0},
                "circuit_overrides": {
                    "main_hvac": {"relay_state": "OPEN"},  # Load shedding
                    "heat_pump_backup": {"relay_state": "OPEN"}
                }
            }
        }

    @classmethod
    async def get_panel_data_for_scenario(cls, scenario_name: str) -> dict[str, Any]:
        """Get panel data for a predefined scenario.

        Args:
            scenario_name: Name of the scenario from get_preset_scenarios()

        Returns:
            Panel data configured for the specified scenario

        Raises:
            ValueError: If scenario_name is not found

        """
        scenarios = cls.get_preset_scenarios()
        if scenario_name not in scenarios:
            available = ", ".join(scenarios.keys())
            raise ValueError(f"Unknown scenario '{scenario_name}'. Available: {available}")

        scenario_config = scenarios[scenario_name]
        return await cls.get_realistic_panel_data(**scenario_config)

    @classmethod
    async def get_real_circuit_ids(cls, config_name: str = "simulation_config_32_circuit") -> dict[str, str]:
        """Get the actual circuit IDs from YAML simulation config with their names.

        Args:
            config_name: Name of the YAML config file to use

        Returns:
            Dictionary mapping circuit IDs to their friendly names

        """
        client = await cls.create_simulation_client(config_name=config_name)
        async with client:
            circuits = await client.get_circuits()
            return {
                circuit_id: circuit.name
                for circuit_id, circuit in circuits.circuits.additional_properties.items()
            }

    @classmethod
    async def get_circuit_ids_by_type(cls, config_name: str = "simulation_config_32_circuit") -> dict[str, list[str]]:
        """Get circuit IDs grouped by appliance type for targeted testing.

        Args:
            config_name: Name of the YAML config file to use

        Returns:
            Dictionary mapping appliance types to lists of circuit IDs

        """
        # Get all circuits dynamically from the YAML config
        circuit_ids = await cls.get_real_circuit_ids(config_name)

        # Categorize circuits based on their names
        categorized: dict[str, list[str]] = {
            "lights": [],
            "ev_chargers": [],
            "hvac": [],
            "appliances": [],
            "outlets": [],
            "solar": [],
            "pool": [],
            "essential": []
        }

        for circuit_id, name in circuit_ids.items():
            name_lower = name.lower()

            # Categorize based on circuit names from YAML config
            if "light" in name_lower:
                categorized["lights"].append(circuit_id)
            elif "ev" in name_lower or "charger" in name_lower:
                categorized["ev_chargers"].append(circuit_id)
            elif any(term in name_lower for term in ["hvac", "heat pump"]):
                categorized["hvac"].append(circuit_id)
            elif any(term in name_lower for term in ["dishwasher", "dryer", "microwave", "oven", "refrigerator", "washing"]):
                categorized["appliances"].append(circuit_id)
            elif "outlet" in name_lower:
                categorized["outlets"].append(circuit_id)
            elif "solar" in name_lower or "inverter" in name_lower:
                categorized["solar"].append(circuit_id)
            elif "pool" in name_lower:
                categorized["pool"].append(circuit_id)
            elif any(term in name_lower for term in ["master", "bedroom", "kitchen", "bathroom"]):
                categorized["essential"].append(circuit_id)
            else:
                # Default to essential for unrecognized circuits
                categorized["essential"].append(circuit_id)

        return categorized

    @classmethod
    async def find_circuit_ids_by_name(
        cls,
        name_patterns: str | list[str],
        config_name: str = "simulation_config_32_circuit"
    ) -> list[str]:
        """Find circuit IDs by name patterns.

        Args:
            name_patterns: String or list of strings to search for in circuit names (case-insensitive)
            config_name: Name of the YAML config file to use

        Returns:
            List of circuit IDs matching the patterns

        """
        if isinstance(name_patterns, str):
            name_patterns = [name_patterns]

        circuit_ids = await cls.get_real_circuit_ids(config_name)
        matching_ids = []

        for circuit_id, name in circuit_ids.items():
            name_lower = name.lower()
            if any(pattern.lower() in name_lower for pattern in name_patterns):
                matching_ids.append(circuit_id)

        return matching_ids

    @classmethod
    async def get_circuit_details(cls, config_name: str = "simulation_config_32_circuit") -> dict[str, dict[str, Any]]:
        """Get detailed information about all circuits from YAML simulation.

        Args:
            config_name: Name of the YAML config file to use

        Returns:
            Dictionary mapping circuit IDs to their full circuit data

        """
        client = await cls.create_simulation_client(config_name=config_name)
        async with client:
            circuits = await client.get_circuits()
            return {
                circuit_id: {
                    "id": circuit.id,
                    "name": circuit.name,
                    "relay_state": circuit.relay_state,
                    "instant_power_w": circuit.instant_power_w,
                    "produced_energy_wh": circuit.produced_energy_wh,
                    "consumed_energy_wh": circuit.consumed_energy_wh,
                    "tabs": circuit.tabs,
                    "priority": circuit.priority,
                    "is_user_controllable": circuit.is_user_controllable,
                    "is_sheddable": circuit.is_sheddable,
                    "is_never_backup": circuit.is_never_backup,
                }
                for circuit_id, circuit in circuits.circuits.additional_properties.items()
            }

    @classmethod
    async def get_available_configs(cls) -> list[str]:
        """Get list of available YAML configuration files.

        Returns:
            List of config names (without .yaml extension)

        """
        configs = []

        # Check integration configs first (this is the primary location)
        try:
            current_file = Path(__file__)
            integration_root = current_file.parent.parent.parent / "custom_components" / "span_panel"
            config_dir = integration_root / "simulation_configs"

            if await asyncio.to_thread(config_dir.exists):
                files = await asyncio.to_thread(lambda: list(config_dir.glob("*.yaml")))
                for file in files:
                    configs.append(file.stem)
        except Exception:
            pass

        # Check span-panel-api examples as fallback
        try:
            current_file = Path(__file__)
            span_api_examples = current_file.parent.parent.parent.parent / "span-panel-api" / "examples"

            if await asyncio.to_thread(span_api_examples.exists):
                files = await asyncio.to_thread(lambda: list(span_api_examples.glob("*.yaml")))
                for file in files:
                    if file.stem not in configs:  # Avoid duplicates
                        configs.append(file.stem)
        except Exception:
            pass

        return sorted(configs)

    @classmethod
    def get_available_configs_with_names(cls) -> dict[str, str]:
        """Get available configs with user-friendly display names.

        Returns:
            Dictionary mapping config keys to display names

        """
        # Use the same logic as the config flow
        from custom_components.span_panel.config_flow import get_available_simulation_configs
        return get_available_simulation_configs()

    @staticmethod
    def extract_serial_number_from_yaml(yaml_path: str) -> str:
        """Extract the serial number from a YAML simulation config file.

        Args:
            yaml_path: Path to the YAML configuration file

        Returns:
            Serial number from the config file

        """
        content = Path(yaml_path).read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        return str(data["global_settings"]["device_identifier"])
