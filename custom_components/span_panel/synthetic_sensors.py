"""Synthetic sensor management for SPAN Panel integration.

This module handles the setup and management of synthetic sensors using the
ha-synthetic-sensors package with virtual backing entities.
"""

from __future__ import annotations

from contextlib import suppress
import logging
from pathlib import Path
from typing import Any

import aiofiles
from ha_synthetic_sensors import (
    DataProviderCallback,
    DataProviderChangeNotifier,
    DataProviderResult,
    SensorManager,
    StorageManager,
    async_setup_synthetic_sensors_with_entities,
    rebind_backing_entities,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify
import yaml

from .const import DOMAIN, SIGNAL_STAGE_SYNTHETIC_SENSORS
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_backing_entity_id_for_entry,
    construct_sensor_set_id,
    construct_single_circuit_entity_id,
    construct_synthetic_unique_id_for_entry,
    construct_tabs_attribute,
    construct_voltage_attribute,
    get_user_friendly_suffix,
)
from .synthetic_named_circuits import generate_named_circuit_sensors
from .synthetic_panel_circuits import generate_panel_sensors
from .synthetic_utils import BackingEntity, fill_template, load_template

_LOGGER = logging.getLogger(__name__)

# Global coordinators for managing virtual backing entities
_synthetic_coordinators: dict[str, SyntheticSensorCoordinator] = {}


# Custom YAML representer to force quotes on strings that might be misinterpreted
def force_quotes_representer(dumper, data):
    """Force quotes on strings that contain brackets or look like formulas."""
    if isinstance(data, str) and (
        "[" in data or "]" in data or data.startswith("tabs ") or "-" in data
    ):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


# Register the custom representer
yaml.add_representer(str, force_quotes_representer)


class SyntheticSensorCoordinator:
    """Coordinator for synthetic sensor data updates and configuration management.

    This class owns the StorageManager and handles the complete synthetic sensor lifecycle:
    1. Generates sensor configurations and backing entities
    2. Manages the StorageManager and YAML configuration
    3. Listens to SPAN coordinator updates and ensures virtual backing entities are populated
    """

    def __init__(self, hass: HomeAssistant, coordinator: SpanPanelCoordinator, device_name: str):
        """Initialize the synthetic sensor coordinator."""
        self.hass = hass
        self.coordinator = coordinator
        self.device_name = device_name
        self.backing_entity_metadata: dict[str, dict[str, Any]] = {}  # Metadata for data provider
        self.sensor_to_backing_mapping: dict[str, str] = {}
        self.all_backing_entities: list[
            dict[str, Any]
        ] = []  # Complete backing entity list for re-registration
        self.change_notifier: DataProviderChangeNotifier | None = (
            None  # Will be set after sensor manager is created
        )
        self.storage_manager: StorageManager | None = None
        self.sensor_set_id: str | None = None
        self.device_identifier: str | None = None
        # Snapshot of last emitted values per backing entity for selective updates
        self._last_values: dict[str, Any] = {}
        # Store sensor manager reference for metrics enrichment
        self._last_sensor_manager: Any | None = None

        # Subscribe synthetic layer to last stage so we update after native
        # platforms. Schedule on the loop to keep thread-safety guarantees.
        self._unsub = coordinator.async_add_listener(self._handle_coordinator_update)

        def _on_stage() -> None:
            # Ensure synthetic updates run strictly on the event loop
            self.hass.loop.call_soon_threadsafe(self._handle_coordinator_update)

        self._unsub_stage = async_dispatcher_connect(
            hass, SIGNAL_STAGE_SYNTHETIC_SENSORS, _on_stage
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator updates by notifying synthetic sensors of data changes."""
        if not self.coordinator.last_update_success:
            return

        try:
            span_panel = self.coordinator.data
            if not span_panel or not span_panel.panel:
                return

            # Compute changed backing entities only (reduces synthetic work)
            changed_ids: set[str] = set()
            if self.backing_entity_metadata:
                # Iterate known backing entities and compare with last snapshot
                for backing_id, meta in self.backing_entity_metadata.items():
                    current_value = self._extract_value_from_panel(span_panel, meta)
                    previous_value = self._last_values.get(backing_id)
                    if previous_value != current_value:
                        changed_ids.add(backing_id)
                        self._last_values[backing_id] = current_value

            if self.change_notifier and changed_ids:
                self.change_notifier(changed_ids)

        except Exception as e:
            _LOGGER.error("Error handling coordinator update for synthetic sensors: %s", e)

    def get_backing_value(self, entity_id: str) -> Any:
        """Get the current value for a virtual backing entity using live coordinator data."""
        metadata = self.backing_entity_metadata.get(entity_id)
        if metadata is None:
            _LOGGER.warning("No metadata found for backing entity: %s", entity_id)
            return None

        if not self.coordinator.last_update_success or not self.coordinator.data:
            _LOGGER.debug("Coordinator not ready for entity %s", entity_id)
            return 0.0

        try:
            span_panel = self.coordinator.data
            if not span_panel or not span_panel.panel:
                return 0.0

            api_key = metadata["api_key"]
            circuit_id = metadata["circuit_id"]

            if circuit_id == "0":
                # Panel-level data
                value = getattr(span_panel.panel, api_key, None)
            else:
                # Circuit-level data
                circuit = span_panel.circuits.get(circuit_id)
                value = getattr(circuit, api_key, None) if circuit else None

            # Return 0.0 instead of None to prevent "Variables with None values" errors
            return value if value is not None else 0.0

        except AttributeError as e:
            _LOGGER.warning(
                "Failed to get value for %s.%s: %s",
                circuit_id if circuit_id != "0" else "panel",
                api_key,
                e,
            )
            return 0.0

    def _populate_backing_entity_metadata(self, all_backing_entities: list[dict[str, Any]]) -> None:
        """Populate backing entity metadata for data provider callback.

        This metadata is always needed regardless of fresh vs existing installation
        because the data provider callback needs to know how to extract values
        from the coordinator data.
        """
        # Store the complete backing entity list for later CRUD operations
        self.all_backing_entities = all_backing_entities.copy()

        self.backing_entity_metadata = {}
        for backing_entity in all_backing_entities:
            # Extract circuit_id and api_key from data_path for data provider callback
            data_path = backing_entity["data_path"]
            if data_path.startswith("circuits."):
                # Circuit-level data: "circuits.{circuit_id}.{api_key}"
                parts = data_path.split(".", 2)
                if len(parts) == 3:
                    circuit_id = parts[1]
                    api_key = parts[2]
                else:
                    # Fallback for malformed paths
                    circuit_id = "0"
                    api_key = data_path
            else:
                # Panel-level data
                circuit_id = "0"
                api_key = data_path

            # Store metadata for data provider to use (no registration needed)
            self.backing_entity_metadata[backing_entity["entity_id"]] = {
                "api_key": api_key,
                "circuit_id": circuit_id,
                "data_path": data_path,
            }

        _LOGGER.debug(
            "Populated metadata for %d backing entities", len(self.backing_entity_metadata)
        )

        # Initialize last value snapshot using current coordinator data
        try:
            span_panel = self.coordinator.data
            if span_panel:
                for backing_id, meta in self.backing_entity_metadata.items():
                    self._last_values[backing_id] = self._extract_value_from_panel(span_panel, meta)
        except Exception as e:
            # Snapshot is best-effort; proceed without blocking setup
            _LOGGER.debug("Failed to snapshot backing entity values: %s", e)

    def _extract_value_from_panel(self, span_panel: Any, meta: dict[str, Any]) -> Any:
        """Get a numeric value from panel/circuit given metadata (non-blocking)."""
        api_key = meta["api_key"]
        circuit_id = meta["circuit_id"]
        try:
            if circuit_id == "0":
                obj = getattr(span_panel, "panel", None)
                value = getattr(obj, api_key, None) if obj is not None else None
            else:
                circuit = span_panel.circuits.get(circuit_id)
                value = getattr(circuit, api_key, None) if circuit is not None else None
            return 0.0 if value is None else value
        except Exception:
            return 0.0

    def set_change_notifier(self, change_notifier: DataProviderChangeNotifier) -> None:
        """Set the change notification callback for coordinator updates."""
        self.change_notifier = change_notifier
        _LOGGER.debug("Change notifier callback set for synthetic coordinator")

    async def setup_configuration(
        self, config_entry: ConfigEntry, migration_mode: bool = False
    ) -> StorageManager:
        """Set up synthetic sensor configuration and storage manager.

        This method generates sensor configurations, registers backing entities,
        and imports the YAML configuration into storage.

        Args:
            config_entry: The configuration entry
            migration_mode: Whether we're in migration mode

        Returns:
            StorageManager: The configured storage manager

        """
        # Initialize storage manager for synthetic sensors using parent integration domain
        self.storage_manager = StorageManager(
            self.hass,
            DOMAIN,
            integration_domain=DOMAIN,
        )
        await self.storage_manager.async_load()

        # Delegate to the appropriate configuration method
        # Simulation mode handling is now done by the factory
        return await self._setup_live_configuration(config_entry, migration_mode)

    async def _setup_live_configuration(
        self, config_entry: ConfigEntry, migration_mode: bool = False
    ) -> StorageManager:
        """Set up configuration for live panel data (existing implementation)."""
        # Generate panel sensors and backing entities with global settings
        span_panel = self.coordinator.data
        _LOGGER.debug(
            "SYN_SETUP_DEBUG: migration_mode=%s for entry_id=%s",
            migration_mode,
            self.coordinator.config_entry.entry_id,
        )

        (
            panel_sensor_configs,
            panel_backing_entities,
            global_settings,
            panel_mappings,
        ) = await generate_panel_sensors(
            self.hass,
            self.coordinator,
            span_panel,
            self.device_name,
            migration_mode=migration_mode,
        )

        # Generate named circuit sensors and backing entities
        (
            named_circuit_configs,
            named_circuit_backing_entities,
            named_global_settings,
            circuit_mappings,
        ) = await generate_named_circuit_sensors(
            self.hass,
            self.coordinator,
            span_panel,
            self.device_name,
            migration_mode=migration_mode,
        )

        # Combine all sensor configs and backing entities
        all_sensor_configs = {**panel_sensor_configs, **named_circuit_configs}
        all_backing_entities: list[dict[str, Any]] = list(panel_backing_entities) + list(
            named_circuit_backing_entities
        )  # type: ignore[assignment]

        # Use global settings from panel sensors (they should be identical)
        if not global_settings and named_global_settings:
            global_settings = named_global_settings

        # Debug: check for power sensors specifically
        power_sensors = [key for key in all_sensor_configs if "power" in key.lower()]
        circuit_power_sensors = [key for key in power_sensors if "circuit" in key.lower()]

        _LOGGER.debug(
            "Setting up synthetic sensors: %d panel + %d named circuit = %d total sensors",
            len(panel_sensor_configs),
            len(named_circuit_configs),
            len(all_sensor_configs),
        )
        _LOGGER.debug(
            "Power sensor breakdown: %d total power sensors, %d circuit power sensors: %s",
            len(power_sensors),
            len(circuit_power_sensors),
            circuit_power_sensors[:5],  # Show first 5 as examples
        )

        # Always populate backing entity metadata for data provider callback
        # This is needed whether it's a fresh install or existing installation
        # because the data provider needs to know how to get values from coordinator data
        self._populate_backing_entity_metadata(all_backing_entities)

        # Extract device identifier from global settings (with fallback)
        device_identifier = global_settings.get(
            "device_identifier", span_panel.status.serial_number
        )
        self.sensor_set_id = construct_sensor_set_id(device_identifier)
        self.device_identifier = device_identifier
        # Publish a deterministic sensor set id on the main coordinator for others to use
        with suppress(Exception):
            self.coordinator.synthetic_sensor_set_id = self.sensor_set_id

        # Simple 1:1 mapping - use the mappings provided by generation functions
        _LOGGER.debug("Using direct 1:1 sensor-to-backing mappings from generation functions")

        # Combine all mappings from the generation functions
        all_mappings = {}

        # Add panel sensor mappings
        all_mappings.update(panel_mappings)
        _LOGGER.debug("Added %d panel sensor mappings", len(panel_mappings))

        # Add circuit sensor mappings
        all_mappings.update(circuit_mappings)
        _LOGGER.debug("Added %d circuit sensor mappings", len(circuit_mappings))

        # Use the combined mappings directly
        self.sensor_to_backing_mapping = all_mappings

        _LOGGER.debug(
            "Using direct mapping for %d sensors with %d backing entities",
            len(self.sensor_to_backing_mapping),
            len(all_backing_entities),
        )

        # NOTE: Backing entities will be registered automatically by the convenience method
        # The data provider callback will provide live data from the coordinator
        _LOGGER.debug("Sensor-to-backing mapping prepared - ready for synthetic sensor creation")

        # Handle sensor set creation and configuration
        if self.storage_manager:
            # Check if this is a fresh installation
            if not self.storage_manager.sensor_set_exists(self.sensor_set_id):
                _LOGGER.info(
                    "Fresh installation detected - creating sensor set and importing default configuration"
                )

                # Create new sensor set
                await self.storage_manager.async_create_sensor_set(
                    sensor_set_id=self.sensor_set_id,
                    device_identifier=device_identifier,
                    name=f"SPAN Panel {device_identifier}",
                    description="SPAN Panel synthetic sensors for circuit monitoring",
                )
                _LOGGER.debug("Created new sensor set for device %s", device_identifier)

                # Generate and import initial YAML configuration for fresh install
                yaml_content = await _construct_complete_yaml_config(
                    all_sensor_configs, global_settings
                )
                await self.storage_manager.async_from_yaml(
                    yaml_content=yaml_content,
                    sensor_set_id=self.sensor_set_id,
                    device_identifier=device_identifier,
                    replace_existing=False,  # Start with default configuration
                )
                _LOGGER.info("Initial sensor configuration imported for fresh installation")
            else:
                if migration_mode:
                    _LOGGER.info(
                        "Migration mode detected - regenerating and importing YAML for existing sensor set"
                    )
                    yaml_content = await _construct_complete_yaml_config(
                        all_sensor_configs, global_settings
                    )
                    await self.storage_manager.async_from_yaml(
                        yaml_content=yaml_content,
                        sensor_set_id=self.sensor_set_id,
                        device_identifier=device_identifier,
                        replace_existing=True,
                    )
                    _LOGGER.info(
                        "Re-imported sensor configuration during migration (existing set updated)"
                    )
                else:
                    _LOGGER.debug(
                        "Existing sensor set found - using stored configuration (no YAML generation needed)"
                    )
                    # Existing installation - sensors already configured in storage
                    # Just ensure the backing entity metadata is populated for the data provider

        _LOGGER.debug(
            "Sensor set configuration ready - fresh install imported, existing install loaded from storage"
        )

        if not self.storage_manager:
            raise RuntimeError("Storage manager was not initialized")
        return self.storage_manager

    def get_storage_manager(self) -> StorageManager | None:
        """Get the storage manager."""
        return self.storage_manager

    def add_backing_entities(self, new_backing_entities: list[dict[str, Any]]) -> None:
        """Add new backing entities to the complete list and update metadata.

        Args:
            new_backing_entities: List of backing entity dicts to add

        """
        # Add to the complete backing entity list
        self.all_backing_entities.extend(new_backing_entities)

        # Update the metadata dict for data provider callback
        for backing_entity in new_backing_entities:
            data_path = backing_entity["data_path"]
            if data_path.startswith("circuits."):
                # Circuit-level data: "circuits.{circuit_id}.{api_key}"
                parts = data_path.split(".", 2)
                if len(parts) == 3:
                    circuit_id = parts[1]
                    api_key = parts[2]
                else:
                    circuit_id = None
                    api_key = data_path
            else:
                # Panel-level data: just the api_key
                circuit_id = None
                api_key = data_path

            # Store metadata for data provider to use
            self.backing_entity_metadata[backing_entity["entity_id"]] = {
                "api_key": api_key,
                "circuit_id": circuit_id,
                "data_path": data_path,
            }

        _LOGGER.debug(
            "Added %d backing entities. Total: %d",
            len(new_backing_entities),
            len(self.all_backing_entities),
        )

    def remove_backing_entities(self, entity_ids_to_remove: list[str]) -> None:
        """Remove backing entities from the complete list and update metadata.

        Args:
            entity_ids_to_remove: List of entity IDs to remove

        """
        # Remove from the complete backing entity list
        self.all_backing_entities = [
            entity
            for entity in self.all_backing_entities
            if entity["entity_id"] not in entity_ids_to_remove
        ]

        # Remove from metadata dict
        for entity_id in entity_ids_to_remove:
            self.backing_entity_metadata.pop(entity_id, None)

        _LOGGER.debug(
            "Removed %d backing entities. Total: %d",
            len(entity_ids_to_remove),
            len(self.all_backing_entities),
        )

    async def reregister_backing_entities_with_sensor_manager(self, sensor_manager: Any) -> None:
        """Re-register the complete backing entity list with the sensor manager.

        This method rebuilds the configuration with the current backing entity list
        and reloads it in the sensor manager.

        Args:
            sensor_manager: The SensorManager instance to reload

        """
        if not self.storage_manager:
            _LOGGER.error("Cannot re-register backing entities: storage manager not available")
            return

        try:
            # Get the current configuration from storage
            # Use the same device_identifier that was used when creating the sensor set
            target_identifier = self.device_identifier or self.coordinator.data.status.serial_number
            config = self.storage_manager.to_config(device_identifier=target_identifier)

            # Reload the sensor manager with the updated configuration
            # The to_config() method should include any sensors that reference the backing entities
            await sensor_manager.reload_configuration(config)

            _LOGGER.debug(
                "Re-registered %d backing entities with sensor manager",
                len(self.all_backing_entities),
            )
        except Exception as e:
            _LOGGER.error("Failed to re-register backing entities: %s", e, exc_info=True)

    def get_sensor_set_id(self) -> str | None:
        """Get the sensor set ID."""
        return self.sensor_set_id

    def shutdown(self) -> None:
        """Clean up the coordinator."""
        if self._unsub is not None:
            self._unsub()
        # Disconnect staged subscription
        with suppress(Exception):
            if hasattr(self, "_unsub_stage") and self._unsub_stage is not None:
                self._unsub_stage()


async def setup_synthetic_configuration(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: SpanPanelCoordinator,
    migration_mode: bool = False,
) -> StorageManager:
    """Set up synthetic sensor configuration.

    This function creates the synthetic sensor coordinator and delegates the configuration
    setup to the coordinator, which owns the storage manager.

    Args:
        hass: Home Assistant instance
        config_entry: The config entry
        coordinator: SPAN Panel coordinator
        migration_mode: Whether we're in migration mode

    """
    # Get device name from config entry
    device_name = config_entry.data.get("device_name", config_entry.title)

    # Create synthetic sensor coordinator for virtual backing entities
    synthetic_coord = SyntheticSensorCoordinator(hass, coordinator, device_name)
    _synthetic_coordinators[config_entry.entry_id] = synthetic_coord

    # Delegate configuration setup to the coordinator
    storage_manager = await synthetic_coord.setup_configuration(config_entry, migration_mode)

    return storage_manager


async def _construct_complete_yaml_config(
    sensor_configs: dict[str, dict[str, Any]], global_settings: dict[str, Any]
) -> str:
    """Construct complete YAML configuration with global settings at the top.

    Args:
        sensor_configs: Dictionary of sensor configurations
        global_settings: Global settings to include at the top

    Returns:
        Complete YAML configuration string

    """
    # Start with global settings at the top
    yaml_dict = {}

    # Add global settings if available
    if global_settings:
        yaml_dict["global_settings"] = global_settings

    # Add sensors section, removing device_identifier from individual sensors
    # since it should only be in global settings
    if sensor_configs:
        cleaned_sensor_configs = {}
        for sensor_key, sensor_config in sensor_configs.items():
            cleaned_config = sensor_config.copy()
            # Remove device_identifier from individual sensors - it belongs in global settings
            cleaned_config.pop("device_identifier", None)
            cleaned_sensor_configs[sensor_key] = cleaned_config
        yaml_dict["sensors"] = cleaned_sensor_configs

    yaml_result = yaml.dump(
        yaml_dict, default_flow_style=False, sort_keys=False, allow_unicode=True, width=1000
    )
    _LOGGER.debug("Generated YAML configuration for synthetic sensors:")

    return yaml_result


async def async_setup_synthetic_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    coordinator: SpanPanelCoordinator,
    storage_manager: StorageManager,
) -> SensorManager:
    """Set up synthetic sensors using the simplified interface.

    This function uses the ha-synthetic-sensors simplified interface to handle
    all the complex setup automatically, including solar sensors if configured.
    """
    # Create data provider callback
    data_provider = create_data_provider_callback(coordinator)

    # Get synthetic coordinator and use its sensor-to-backing mapping
    synthetic_coord = find_synthetic_coordinator_for(coordinator)
    sensor_to_backing_mapping = synthetic_coord.sensor_to_backing_mapping if synthetic_coord else {}

    # Check if this is a migration - retrieve stored mapping if available
    #    if not sensor_to_backing_mapping and DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]:
    #        migration_mapping = hass.data[DOMAIN][config_entry.entry_id].get("migration_sensor_to_backing_mapping")
    #        if migration_mapping:
    #            sensor_to_backing_mapping = migration_mapping
    #            _LOGGER.info("Using %d sensor-to-backing mappings from migration", len(sensor_to_backing_mapping))
    #            # Set the mapping on the coordinator if it exists
    #            if synthetic_coord:
    #                synthetic_coord.sensor_to_backing_mapping = sensor_to_backing_mapping
    #            # Clean up the temporary storage
    #            del hass.data[DOMAIN][config_entry.entry_id]["migration_sensor_to_backing_mapping"]

    _LOGGER.debug(
        "SETUP_SENSORS_DEBUG: Using %d sensor-to-backing mappings", len(sensor_to_backing_mapping)
    )

    # Define change notifier up-front so it can be bound during registration
    # This ensures named circuits receive updates immediately after creation
    sensor_manager: SensorManager | None = None

    def change_notifier(changed_entity_ids: set[str]) -> None:
        """Notify synthetic sensors when backing entities change."""
        if not changed_entity_ids:
            return
        if sensor_manager is not None:
            hass.async_create_task(
                sensor_manager.async_update_sensors_for_entities(changed_entity_ids)
            )

    # Use the simplified interface and pass the change_notifier so backing entities
    # are registered with an active notifier during initial setup
    # Determine the correct device_identifier for this entry
    # Prefer the identifier captured during configuration setup (per-entry)
    target_device_identifier = (
        synthetic_coord.device_identifier
        if synthetic_coord and synthetic_coord.device_identifier
        else None
    )
    if target_device_identifier is None:
        # Fallbacks: simulator uses device name; live uses serial
        is_simulator = bool(config_entry.data.get("simulation_mode", False))
        if is_simulator:
            target_device_identifier = slugify(
                config_entry.data.get("device_name", config_entry.title)
            )
        else:
            target_device_identifier = coordinator.data.status.serial_number

    sensor_manager = await async_setup_synthetic_sensors_with_entities(
        hass=hass,
        config_entry=config_entry,
        async_add_entities=async_add_entities,
        storage_manager=storage_manager,
        sensor_set_id=synthetic_coord.sensor_set_id if synthetic_coord else None,
        data_provider_callback=data_provider,
        sensor_to_backing_mapping=sensor_to_backing_mapping,
        change_notifier=change_notifier,
    )

    # Connect the change notifier to the synthetic coordinator for coordinator updates
    if synthetic_coord:
        synthetic_coord.set_change_notifier(change_notifier)
        _LOGGER.debug("Connected change notifier to synthetic coordinator")

        # Re-bind all known backing entity IDs with the notifier to the sensor manager
        backing_ids = set(synthetic_coord.backing_entity_metadata.keys())
        if backing_ids:
            try:
                rebind_backing_entities(
                    sensor_manager,
                    backing_ids,
                    change_notifier,
                    trigger_initial_update=False,
                    logger=_LOGGER,
                )
            except Exception as err:
                _LOGGER.warning("Failed to re-register backing entities with notifier: %s", err)

        # Sanity-check mapping values against metadata keys
        if sensor_to_backing_mapping:
            mapping_ids = set(sensor_to_backing_mapping.values())
            missing_in_meta = mapping_ids - backing_ids
            if missing_in_meta:
                _LOGGER.warning(
                    "Backing IDs in mapping missing from coordinator metadata: %s",
                    sorted(missing_in_meta),
                )

        # Trigger an initial update so named circuits compute a first value
        if backing_ids and change_notifier is not None:
            change_notifier(backing_ids)

        # Keep a handle for later metrics enrichment
        try:
            synthetic_coord._last_sensor_manager = sensor_manager
        except Exception as e:
            _LOGGER.debug("Failed to store sensor manager reference: %s", e)

    # Note: The convenience method handles backing entity registration. The synthetic coordinator
    # listens for SPAN coordinator updates and notifies the sensor manager via change_notifier.
    # Solar sensors are not created during initial setup because the native circuit sensors
    # haven't been created yet. Solar sensors will be created when the options change, which
    # happens after the native sensors are ready. This is handled by the update_listener.

    _LOGGER.debug("Successfully set up synthetic sensors with change tracking")
    return sensor_manager


def create_data_provider_callback(coordinator: SpanPanelCoordinator) -> DataProviderCallback:
    """Create data provider callback for virtual backing entities."""

    def data_provider_callback(entity_id: str) -> DataProviderResult:
        """Provide live data from virtual backing entities."""
        try:
            # Find the synthetic coordinator for this device
            synthetic_coord = find_synthetic_coordinator_for(coordinator)
            if not synthetic_coord:
                _LOGGER.debug("No synthetic coordinator found for entity_id: %s", entity_id)
                return {"value": None, "exists": False}

            # Get value from virtual backing entity using live coordinator data
            value = synthetic_coord.get_backing_value(entity_id)
            exists = entity_id in synthetic_coord.backing_entity_metadata

            return {"value": value, "exists": exists}

        except Exception as e:
            _LOGGER.error("Error in data provider callback for %s: %s", entity_id, e)
            return {"value": None, "exists": False}

    return data_provider_callback


def find_synthetic_coordinator_for(
    span_coordinator: SpanPanelCoordinator,
) -> SyntheticSensorCoordinator | None:
    """Find the synthetic coordinator for a given SPAN coordinator."""
    for synthetic_coord in _synthetic_coordinators.values():
        if synthetic_coord.coordinator == span_coordinator:
            return synthetic_coord
    return None


# CRUD Operations for Optional Sensor Management


async def add_battery_sensors(
    sensor_manager: SensorManager, battery_circuits: list[str], coordinator: SpanPanelCoordinator
) -> bool:
    """Add battery sensors - DEPRECATED: Battery is now a native sensor.

    Battery percentage is now implemented as a native sensor. This function is
    kept for compatibility but always returns True.
    """
    _LOGGER.debug("Battery sensors are now native - no synthetic battery sensors to add")
    return True


async def remove_battery_sensors(
    sensor_manager: SensorManager, battery_sensor_ids: list[str]
) -> bool:
    """Remove battery sensors - DEPRECATED: Battery is now a native sensor.

    Battery percentage is now implemented as a native sensor. This function is
    kept for compatibility but always returns True.
    """
    _LOGGER.debug("Battery sensors are now native - no synthetic battery sensors to remove")
    return True


# Helper functions for CRUD operations


def extract_circuit_id_from_entity_id(entity_id: str) -> str:
    """Extract circuit ID from virtual backing entity ID.

    Backing entity IDs follow the pattern from construct_backing_entity_id():
    sensor.span_{serial}_{circuit_id}_backing_{suffix}
    """
    # entity_id format: "sensor.span_abc123_circuit_id_backing_suffix"
    parts = entity_id.split("_")
    if len(parts) >= 4 and "backing" in parts:
        backing_index = parts.index("backing")
        if backing_index > 0:
            return parts[backing_index - 1]  # circuit_id or "0" for panel
    return "0"  # Default to panel


def get_existing_battery_sensor_ids(sensor_manager: SensorManager) -> list[str]:
    """Get existing battery sensor IDs - DEPRECATED: Battery is now a native sensor."""
    # Battery is now a native sensor, so no synthetic battery sensors exist
    return []


async def generate_circuit_sensor_configs(
    coordinator: SpanPanelCoordinator, circuit_ids: list[str], sensor_type: str = "power"
) -> tuple[dict[str, dict[str, Any]], list[BackingEntity], dict[str, Any]]:
    """Generate circuit-based synthetic sensor configurations using templates.

    This function uses the same simple template approach as solar sensors:
    - Load templates as strings
    - Fill placeholders with simple string replacement
    - Combine by string concatenation
    - Return the YAML configuration directly

    Args:
        coordinator: The coordinator instance
        circuit_ids: List of circuit IDs to create sensors for
        sensor_type: Type of sensor ("power", "energy_produced", "energy_consumed")

    Returns:
        Tuple of (sensor_configs_dict, backing_entities, global_settings)

    """

    span_panel = coordinator.data
    backing_entities: list[BackingEntity] = []

    # Get display precision from options
    power_precision = coordinator.config_entry.options.get("power_display_precision", 0)
    energy_precision = coordinator.config_entry.options.get("energy_display_precision", 2)

    # Map sensor types to API fields and templates
    sensor_type_mapping = {
        "power": {
            "api_field": "instantPowerW",
            "template": "circuit_power",
            "suffix": "current_power",
            "name_suffix": "Current Power",
        },
        "energy_produced": {
            "api_field": "producedEnergyWh",
            "template": "circuit_energy_produced",
            "suffix": "energy_produced",
            "name_suffix": "Produced Energy",
        },
        "energy_consumed": {
            "api_field": "consumedEnergyWh",
            "template": "circuit_energy_consumed",
            "suffix": "energy_consumed",
            "name_suffix": "Consumed Energy",
        },
    }

    if sensor_type not in sensor_type_mapping:
        raise ValueError(f"Unsupported sensor type: {sensor_type}")

    type_config = sensor_type_mapping[sensor_type]

    # Load header template and fill it once
    header_template = await load_template("sensor_set_header")
    # Use per-entry identifier for simulators; live panels use serial
    is_simulator = bool(coordinator.config_entry.data.get("simulation_mode", False))
    header_device_identifier = (
        slugify(coordinator.config_entry.data.get("device_name", coordinator.config_entry.title))
        if is_simulator
        else span_panel.status.serial_number
    )
    filled_header = fill_template(
        header_template,
        {
            "device_identifier": header_device_identifier,
            "power_display_precision": str(power_precision),
            "energy_display_precision": str(energy_precision),
        },
    )

    # Load sensor template once
    sensor_template = await load_template(type_config["template"])

    # Process each circuit
    all_filled_sensors = []
    for circuit_id in circuit_ids:
        # Get circuit data
        circuit_data = span_panel.circuits.get(circuit_id)
        if not circuit_data:
            _LOGGER.warning("Circuit %s not found in panel data", circuit_id)
            continue

        # Generate entity ID using the single circuit helper
        entity_id = construct_single_circuit_entity_id(
            coordinator=coordinator,
            span_panel=span_panel,
            platform="sensor",
            suffix=type_config["suffix"],
            circuit_data=circuit_data,
            unique_id=None,  # Let it generate the entity ID
        )

        # Generate unique ID for this circuit sensor using per-entry identifier
        sensor_name = f"{circuit_id}_{type_config['suffix']}"
        device_name = coordinator.config_entry.data.get(
            "device_name", coordinator.config_entry.title
        )
        sensor_unique_id = construct_synthetic_unique_id_for_entry(
            coordinator, span_panel, sensor_name, device_name
        )

        # Generate backing entity ID
        backing_suffix = get_user_friendly_suffix(type_config["api_field"])
        backing_entity_id = construct_backing_entity_id_for_entry(
            coordinator, span_panel, circuit_id, backing_suffix, device_name
        )

        # Create friendly name
        circuit_name = circuit_data.name if circuit_data.name else f"Circuit {circuit_id}"
        friendly_name = f"{circuit_name} {type_config['name_suffix']}"

        # Get circuit attributes
        tabs_result = construct_tabs_attribute(circuit_data)
        tabs_attribute = str(tabs_result) if tabs_result is not None else ""
        voltage_attribute = construct_voltage_attribute(circuit_data) or 240

        # Fill sensor template with placeholders
        filled_sensor = fill_template(
            sensor_template,
            {
                "sensor_key": sensor_unique_id,
                "sensor_name": friendly_name,
                "entity_id": entity_id or "",
                "backing_entity_id": backing_entity_id,
                "tabs_attribute": tabs_attribute,
                "voltage_attribute": str(voltage_attribute),
                "power_display_precision": str(power_precision),
                "energy_display_precision": str(energy_precision),
            },
        )

        all_filled_sensors.append(filled_sensor)

        # Create backing entity
        # Get current value from coordinator data
        current_value = getattr(
            circuit_data,
            type_config["api_field"].replace("W", "_w").replace("Wh", "_wh").lower(),
            0.0,
        )

        backing_entity = BackingEntity(
            entity_id=backing_entity_id,
            value=current_value,
            data_path=f"circuits.{circuit_id}.{type_config['api_field'].replace('W', '_w').replace('Wh', '_wh').lower()}",
        )
        backing_entities.append(backing_entity)

    # Combine header and all filled sensors
    combined_yaml = filled_header + "\n" + "\n".join(all_filled_sensors)

    # Load the combined YAML to get the final configuration
    final_config = yaml.safe_load(combined_yaml)

    # Extract global settings and sensor configs
    global_settings = final_config.get("global_settings", {})
    sensor_configs_dict = final_config.get("sensors", {})

    return sensor_configs_dict, backing_entities, global_settings


async def cleanup_synthetic_sensors(config_entry: ConfigEntry) -> None:
    """Clean up synthetic sensor coordinator on unload."""
    if config_entry.entry_id in _synthetic_coordinators:
        synthetic_coord = _synthetic_coordinators.pop(config_entry.entry_id)
        synthetic_coord.shutdown()
        _LOGGER.debug("Cleaned up synthetic sensor coordinator for %s", config_entry.entry_id)


# Options Handler Functions for CRUD Operations moved to synthetic_solar.py


async def handle_battery_options_change(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinator: SpanPanelCoordinator,
    storage_manager: StorageManager,  # Add storage_manager parameter
    enable_battery: bool,
    battery_circuits: list[str] | None = None,
) -> bool:
    """Handle battery options change - DEPRECATED: Battery is now a native sensor.

    Battery percentage is now implemented as a native sensor that gets created/deleted
    during integration reload based on the BATTERY_ENABLE option. This function is
    kept for compatibility but does nothing.

    Args:
        hass: Home Assistant instance
        config_entry: The config entry
        coordinator: SPAN Panel coordinator
        storage_manager: The existing StorageManager instance
        enable_battery: Whether battery sensors should be enabled
        battery_circuits: List of circuit IDs for battery monitoring (not used)

    Returns:
        True (always successful since battery is now native)

    """
    _LOGGER.debug(
        "Battery options change handled by native sensor implementation during reload. "
        "Battery enabled: %s",
        enable_battery,
    )
    return True


def _get_stored_battery_sensor_ids(
    storage_manager: StorageManager, sensor_set_id: str
) -> list[str]:
    """Get list of battery sensor IDs currently stored."""
    try:
        stored_sensors = storage_manager.list_sensors(sensor_set_id=sensor_set_id)
        battery_ids = []
        for sensor_config in stored_sensors:
            # Check if this is a battery sensor by looking at the unique_id or entity_id
            unique_id = sensor_config.unique_id or ""
            entity_id = sensor_config.entity_id or ""
            if "battery" in unique_id.lower() or "battery" in entity_id.lower():
                battery_ids.append(sensor_config.unique_id)
        return battery_ids
    except Exception as e:
        _LOGGER.error("Error getting stored battery sensor IDs: %s", e)
        return []


# Note: backing entity IDs are built via construct_backing_entity_id_for_entry() in helpers


async def async_export_synthetic_config_service(call: ServiceCall) -> None:
    """Export a single sensor set's YAML to the specified file or directory.

    Requires:
      - directory: target directory or full file path
      - sensor_set_id: the exact sensor set ID to export (e.g., span_simulator_sensors)
    """

    export_directory = call.data.get("directory")
    sensor_set_id = call.data.get("sensor_set_id")

    if not export_directory:
        raise ServiceValidationError("Directory parameter is required")
    if not sensor_set_id or not isinstance(sensor_set_id, str):
        raise ServiceValidationError("sensor_set_id parameter is required and must be a string")

    # Validate and determine output path
    try:
        export_path = Path(export_directory).expanduser().resolve()

        # Check if the path includes a filename (has .yaml extension) or is just a directory
        if export_path.suffix.lower() == ".yaml":
            output_file = export_path
            output_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            export_path.mkdir(parents=True, exist_ok=True)
            safe_name = slugify(sensor_set_id)
            output_file = export_path / f"{safe_name}.yaml"

        # Locate a storage manager that contains this sensor set
        storage_manager: StorageManager | None = None
        for coord in _synthetic_coordinators.values():
            if coord.storage_manager and coord.storage_manager.sensor_set_exists(sensor_set_id):
                storage_manager = coord.storage_manager
                break

        if storage_manager is None:
            raise ServiceValidationError(f"Sensor set not found: {sensor_set_id}")

        # Export just this sensor set
        yaml_content = storage_manager.export_yaml(sensor_set_id)

        # Write file asynchronously
        async with aiofiles.open(output_file, "w", encoding="utf-8") as f:
            await f.write(yaml_content)

        _LOGGER.info("Successfully exported sensor set '%s' to: %s", sensor_set_id, output_file)

    except PermissionError:
        raise ServiceValidationError(
            f"Permission denied: Cannot write to directory {export_directory}"
        )
    except OSError as e:
        raise ServiceValidationError(
            f"Failed to create or write to directory {export_directory}: {e}"
        )
    except Exception as e:
        _LOGGER.error("Unexpected error exporting synthetic config: %s", e)
        raise ServiceValidationError(f"Failed to export configuration: {e}")
