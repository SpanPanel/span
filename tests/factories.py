"""Factories for creating test objects with defaults for Span Panel integration."""

import copy
from typing import Any

from custom_components.span_panel.const import (
    CIRCUITS_BREAKER_POSITIONS,
    CIRCUITS_ENERGY_CONSUMED,
    CIRCUITS_ENERGY_PRODUCED,
    CIRCUITS_IS_NEVER_BACKUP,
    CIRCUITS_IS_SHEDDABLE,
    CIRCUITS_IS_USER_CONTROLLABLE,
    CIRCUITS_NAME,
    CIRCUITS_POWER,
    CIRCUITS_PRIORITY,
    CIRCUITS_RELAY,
    CURRENT_RUN_CONFIG,
    DSM_GRID_STATE,
    DSM_STATE,
    MAIN_RELAY_STATE,
    PANEL_POWER,
    STORAGE_BATTERY_PERCENTAGE,
    SYSTEM_CELLULAR_LINK,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_ETHERNET_LINK,
    SYSTEM_WIFI_LINK,
    CircuitPriority,
    CircuitRelayState,
)


class SpanPanelCircuitFactory:
    """Factory for creating span panel circuit test objects."""

    _circuit_defaults = {
        "id": "1",
        CIRCUITS_NAME: "Test Circuit",
        CIRCUITS_RELAY: CircuitRelayState.CLOSED.name,
        CIRCUITS_POWER: 150.5,
        CIRCUITS_ENERGY_CONSUMED: 1500.0,
        CIRCUITS_ENERGY_PRODUCED: 0.0,
        CIRCUITS_BREAKER_POSITIONS: [1],
        CIRCUITS_PRIORITY: CircuitPriority.NICE_TO_HAVE.name,
        CIRCUITS_IS_USER_CONTROLLABLE: True,
        CIRCUITS_IS_SHEDDABLE: True,
        CIRCUITS_IS_NEVER_BACKUP: False,
    }

    @staticmethod
    def create_circuit(
        circuit_id: str = "1",
        name: str = "Test Circuit",
        relay_state: str = CircuitRelayState.CLOSED.name,
        instant_power: float = 150.5,
        consumed_energy: float = 1500.0,
        produced_energy: float = 0.0,
        tabs: list[int] | None = None,
        priority: str = CircuitPriority.NICE_TO_HAVE.name,
        is_user_controllable: bool = True,
        is_sheddable: bool = True,
        is_never_backup: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a circuit with optional defaults."""
        circuit = copy.deepcopy(SpanPanelCircuitFactory._circuit_defaults)
        circuit["id"] = circuit_id
        circuit[CIRCUITS_NAME] = name
        circuit[CIRCUITS_RELAY] = relay_state
        circuit[CIRCUITS_POWER] = instant_power
        circuit[CIRCUITS_ENERGY_CONSUMED] = consumed_energy
        circuit[CIRCUITS_ENERGY_PRODUCED] = produced_energy
        circuit[CIRCUITS_BREAKER_POSITIONS] = tabs or [int(circuit_id)]
        circuit[CIRCUITS_PRIORITY] = priority
        circuit[CIRCUITS_IS_USER_CONTROLLABLE] = is_user_controllable
        circuit[CIRCUITS_IS_SHEDDABLE] = is_sheddable
        circuit[CIRCUITS_IS_NEVER_BACKUP] = is_never_backup

        # Add any additional overrides
        for k, v in kwargs.items():
            circuit[k] = v

        return circuit

    @staticmethod
    def create_kitchen_outlet_circuit() -> dict[str, Any]:
        """Create a kitchen outlet circuit."""
        return SpanPanelCircuitFactory.create_circuit(
            circuit_id="1",
            name="Kitchen Outlets",
            instant_power=245.3,
            consumed_energy=2450.8,
            tabs=[1],
        )

    @staticmethod
    def create_living_room_lights_circuit() -> dict[str, Any]:
        """Create a living room lights circuit."""
        return SpanPanelCircuitFactory.create_circuit(
            circuit_id="2",
            name="Living Room Lights",
            instant_power=85.2,
            consumed_energy=850.5,
            tabs=[2],
        )

    @staticmethod
    def create_solar_panel_circuit() -> dict[str, Any]:
        """Create a solar panel circuit (producing energy)."""
        return SpanPanelCircuitFactory.create_circuit(
            circuit_id="15",
            name="Solar Panels",
            instant_power=-1200.0,  # Negative indicates production
            consumed_energy=0.0,
            produced_energy=12000.5,
            tabs=[15],
            priority=CircuitPriority.MUST_HAVE.name,
            is_user_controllable=False,
        )

    @staticmethod
    def create_non_controllable_circuit() -> dict[str, Any]:
        """Create a non-user-controllable circuit."""
        return SpanPanelCircuitFactory.create_circuit(
            circuit_id="30",
            name="Main Panel Feed",
            is_user_controllable=False,
            priority=CircuitPriority.MUST_HAVE.name,
            tabs=[30],
        )


class SpanPanelDataFactory:
    """Factory for creating span panel data test objects."""

    _panel_defaults = {
        PANEL_POWER: 2500.75,
        CURRENT_RUN_CONFIG: "PANEL_ON_GRID",
        DSM_GRID_STATE: "DSM_GRID_UP",
        DSM_STATE: "DSM_ON_GRID",
        MAIN_RELAY_STATE: "CLOSED",
        "instantGridPowerW": 2500.75,
        "feedthroughPowerW": 0.0,
        "gridSampleStartMs": 1640995200000,
        "gridSampleEndMs": 1640995215000,
        "mainMeterEnergy": {
            "producedEnergyWh": 0.0,
            "consumedEnergyWh": 2500.0,
        },
        "feedthroughEnergy": {
            "producedEnergyWh": 0.0,
            "consumedEnergyWh": 0.0,
        },
    }

    @staticmethod
    def create_panel_data(
        grid_power: float = 2500.75,
        dsm_grid_state: str = "DSM_GRID_UP",
        dsm_state: str = "DSM_ON_GRID",
        main_relay_state: str = "CLOSED",
        current_run_config: str = "PANEL_ON_GRID",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create panel data with optional defaults."""
        panel_data = copy.deepcopy(SpanPanelDataFactory._panel_defaults)
        panel_data[PANEL_POWER] = grid_power
        panel_data["instantGridPowerW"] = grid_power
        panel_data[DSM_GRID_STATE] = dsm_grid_state
        panel_data[DSM_STATE] = dsm_state
        panel_data[MAIN_RELAY_STATE] = main_relay_state
        panel_data[CURRENT_RUN_CONFIG] = current_run_config

        # Add any additional overrides
        for k, v in kwargs.items():
            panel_data[k] = v

        return panel_data

    @staticmethod
    def create_on_grid_panel_data() -> dict[str, Any]:
        """Create panel data for on-grid operation."""
        return SpanPanelDataFactory.create_panel_data(
            grid_power=1850.5,
            dsm_grid_state="DSM_GRID_UP",
            dsm_state="DSM_ON_GRID",
            current_run_config="PANEL_ON_GRID",
        )

    @staticmethod
    def create_backup_panel_data() -> dict[str, Any]:
        """Create panel data for backup operation."""
        return SpanPanelDataFactory.create_panel_data(
            grid_power=0.0,
            dsm_grid_state="DSM_GRID_DOWN",
            dsm_state="DSM_ON_BACKUP",
            current_run_config="PANEL_ON_BACKUP",
        )


class SpanPanelStatusFactory:
    """Factory for creating span panel status test objects."""

    _status_defaults = {
        "software": {
            "firmwareVersion": "1.2.3",
            "updateStatus": "IDLE",
            "env": "prod",
        },
        "system": {
            "serial": "ABC123456789",
            "manufacturer": "Span",
            "model": "Panel",
            "doorState": SYSTEM_DOOR_STATE_CLOSED,
            "uptime": 86400000,  # 24 hours in ms
        },
        "network": {
            SYSTEM_ETHERNET_LINK: True,
            SYSTEM_WIFI_LINK: True,
            SYSTEM_CELLULAR_LINK: False,
        },
    }

    @staticmethod
    def create_status(
        serial_number: str = "ABC123456789",
        software_version: str = "1.2.3",
        door_state: str = SYSTEM_DOOR_STATE_CLOSED,
        ethernet_link: bool = True,
        cellular_link: bool = False,
        wifi_link: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create status data with optional defaults."""
        status = copy.deepcopy(SpanPanelStatusFactory._status_defaults)
        status["system"]["serial"] = serial_number
        status["software"]["firmwareVersion"] = software_version
        status["system"]["doorState"] = door_state
        status["network"][SYSTEM_ETHERNET_LINK] = ethernet_link
        status["network"][SYSTEM_CELLULAR_LINK] = cellular_link
        status["network"][SYSTEM_WIFI_LINK] = wifi_link

        # Add any additional overrides
        for k, v in kwargs.items():
            if "." in k:
                # Handle nested keys like "system.uptime"
                parts = k.split(".")
                current = status
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = v
            else:
                status[k] = v

        return status


class SpanPanelStorageBatteryFactory:
    """Factory for creating span panel storage battery test objects."""

    _battery_defaults = {
        STORAGE_BATTERY_PERCENTAGE: 85,
    }

    @staticmethod
    def create_battery_data(
        battery_percentage: int = 85,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create battery data with optional defaults."""
        battery = copy.deepcopy(SpanPanelStorageBatteryFactory._battery_defaults)
        battery[STORAGE_BATTERY_PERCENTAGE] = battery_percentage

        # Add any additional overrides
        for k, v in kwargs.items():
            battery[k] = v

        return battery


class SpanPanelApiResponseFactory:
    """Factory for creating complete API response objects for testing."""

    @staticmethod
    def create_complete_panel_response(
        circuits: list[dict[str, Any]] | None = None,
        panel_data: dict[str, Any] | None = None,
        status_data: dict[str, Any] | None = None,
        battery_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a complete panel response with all components."""
        if circuits is None:
            circuits = [
                SpanPanelCircuitFactory.create_kitchen_outlet_circuit(),
                SpanPanelCircuitFactory.create_living_room_lights_circuit(),
                SpanPanelCircuitFactory.create_solar_panel_circuit(),
            ]

        if panel_data is None:
            panel_data = SpanPanelDataFactory.create_on_grid_panel_data()

        if status_data is None:
            status_data = SpanPanelStatusFactory.create_status()

        if battery_data is None:
            battery_data = SpanPanelStorageBatteryFactory.create_battery_data()

        # Include circuit data as "branches" in panel data for solar calculations
        # The SpanPanelData.from_dict method expects branches to be indexed starting from 0
        # Create a list of 32 branches (max circuits) with empty defaults and populate with actual circuits
        branches = []
        for _ in range(32):  # SPAN panels support up to 32 circuits
            # Default empty branch
            default_branch = {
                "instantPowerW": 0.0,
                "importedActiveEnergyWh": 0.0,
                "exportedActiveEnergyWh": 0.0,
            }
            branches.append(default_branch)

        # Populate actual circuit data at the correct indices
        for circuit in circuits:
            circuit_id = int(circuit["id"])
            if 1 <= circuit_id <= 32:
                branch_index = circuit_id - 1  # Convert to 0-based index
                branches[branch_index] = {
                    "instantPowerW": circuit.get(CIRCUITS_POWER, 0.0),
                    "importedActiveEnergyWh": circuit.get(CIRCUITS_ENERGY_PRODUCED, 0.0),
                    "exportedActiveEnergyWh": circuit.get(CIRCUITS_ENERGY_CONSUMED, 0.0),
                }

        # Add branches to panel data
        panel_data["branches"] = branches

        return {
            "circuits": {circuit["id"]: circuit for circuit in circuits},
            "panel": panel_data,
            "status": status_data,
            "battery": battery_data,
        }

    @staticmethod
    def create_minimal_panel_response() -> dict[str, Any]:
        """Create a minimal panel response for basic testing."""
        return SpanPanelApiResponseFactory.create_complete_panel_response(
            circuits=[SpanPanelCircuitFactory.create_kitchen_outlet_circuit()],
        )
