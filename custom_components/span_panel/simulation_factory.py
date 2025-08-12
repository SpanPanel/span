"""Simulation factory for SPAN Panel integration.

This module provides a factory pattern for handling simulation mode setup
without polluting the main integration code. It allows the integration to
treat simulation mode as if it were a real panel for YAML generation.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import SpanPanelCoordinator
from .synthetic_sensors import SyntheticSensorCoordinator

_LOGGER = logging.getLogger(__name__)


class SimulationModeFactory:
    """Factory for handling simulation mode setup and configuration."""

    @staticmethod
    def is_simulation_mode() -> bool:
        """Check if we're running in simulation mode.

        Returns:
            True if SPAN_USE_REAL_SIMULATION environment variable is set

        """
        return os.environ.get("SPAN_USE_REAL_SIMULATION", "").lower() in ("1", "true", "yes")

    @staticmethod
    def create_simulation_coordinator(
        coordinator: SyntheticSensorCoordinator,
    ) -> SimulationCoordinator:
        """Create a simulation coordinator that wraps the synthetic coordinator.

        Args:
            coordinator: The synthetic sensor coordinator to wrap

        Returns:
            SimulationCoordinator that provides simulation-specific behavior

        """
        return SimulationCoordinator(coordinator)

    @staticmethod
    def setup_simulation_logging() -> None:
        """Set up enhanced logging for simulation mode."""
        if SimulationModeFactory.is_simulation_mode():
            _LOGGER.info("🔧 Simulation mode enabled - enhanced logging active")
            # Set debug level for simulation-related components
            logging.getLogger("custom_components.span_panel.synthetic_sensors").setLevel(
                logging.DEBUG
            )
            logging.getLogger("custom_components.span_panel.synthetic_panel_circuits").setLevel(
                logging.DEBUG
            )
            logging.getLogger("custom_components.span_panel.synthetic_named_circuits").setLevel(
                logging.DEBUG
            )


class SimulationCoordinator:
    """Coordinator that provides simulation-specific behavior for synthetic sensors.

    This class wraps the main SyntheticSensorCoordinator and provides
    simulation-specific setup and logging without modifying the core logic.
    """

    def __init__(self, coordinator: SyntheticSensorCoordinator):
        """Initialize the simulation coordinator.

        Args:
            coordinator: The synthetic sensor coordinator to wrap

        """
        self.coordinator = coordinator
        self._is_simulation = SimulationModeFactory.is_simulation_mode()

    async def setup_configuration(self, config_entry: ConfigEntry) -> Any:
        """Set up configuration with simulation-specific behavior.

        This method delegates to the main coordinator but adds simulation-specific
        logging and behavior.
        """
        if not self._is_simulation:
            # Delegate to normal coordinator behavior
            return await self.coordinator._setup_live_configuration(config_entry)

        # Simulation mode: use the same logic but with enhanced logging
        _LOGGER.info("🔧 Simulation mode: Generating YAML from simulated panel data")

        # For simulation mode, we use the same configuration logic as live mode
        # but with enhanced logging to show it's working with simulated data
        result = await self.coordinator._setup_live_configuration(config_entry)

        _LOGGER.info("🎯 Simulation mode: YAML generation completed successfully")
        return result

    def get_coordinator(self) -> SyntheticSensorCoordinator:
        """Get the underlying synthetic sensor coordinator."""
        return self.coordinator


def create_synthetic_coordinator_with_simulation_support(
    hass: HomeAssistant, coordinator: SpanPanelCoordinator, device_name: str
) -> SimulationCoordinator | SyntheticSensorCoordinator:
    """Create a synthetic coordinator with optional simulation support.

    This factory function creates the appropriate coordinator based on whether
    simulation mode is enabled, keeping the main integration code clean.

    Args:
        hass: Home Assistant instance
        coordinator: SPAN panel coordinator
        device_name: Device name for the coordinator

    Returns:
        Either a SimulationCoordinator (if simulation mode) or SyntheticSensorCoordinator

    """
    # Set up simulation logging if needed
    SimulationModeFactory.setup_simulation_logging()

    # Create the base coordinator
    base_coordinator = SyntheticSensorCoordinator(hass, coordinator, device_name)

    # Wrap with simulation coordinator if in simulation mode
    if SimulationModeFactory.is_simulation_mode():
        return SimulationModeFactory.create_simulation_coordinator(base_coordinator)

    # Return the base coordinator for normal operation
    return base_coordinator
