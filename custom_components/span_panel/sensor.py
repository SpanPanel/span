"""Support for Span Panel monitor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any, Generic, TypeVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CIRCUITS_ENERGY_CONSUMED,
    CIRCUITS_ENERGY_PRODUCED,
    CIRCUITS_POWER,
    COORDINATOR,
    CURRENT_RUN_CONFIG,
    DOMAIN,
    DSM_GRID_STATE,
    DSM_STATE,
    MAIN_RELAY_STATE,
    STATUS_SOFTWARE_VER,
    STORAGE_BATTERY_PERCENTAGE,
    USE_DEVICE_PREFIX,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_entity_id,
    construct_synthetic_entity_id,
    construct_synthetic_friendly_name,
    get_user_friendly_suffix,
)
from .options import BATTERY_ENABLE, INVERTER_ENABLE
from .span_panel import SpanPanel
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery
from .util import panel_to_device_info


@dataclass(frozen=True)
class SyntheticSensorConfig:
    """Configuration for a synthetic sensor entity."""

    friendly_name: str
    circuit_numbers: list[int]
    key_prefix: str  # e.g., "solar_inverter", "battery_bank", etc.
    value_fn_map: dict[str, Callable[[SpanPanelData], float | str]]


def create_synthetic_sensor_descriptions(
    config: SyntheticSensorConfig,
) -> list[SpanPanelDataSensorEntityDescription]:
    """Create synthetic sensor descriptions from a configuration."""
    descriptions = []

    for template in SYNTHETIC_SENSOR_TEMPLATES:
        # Create a unique key for this synthetic sensor
        synthetic_key = f"{config.key_prefix}_{template.key}"

        # Get the appropriate value function from the configuration
        value_fn = config.value_fn_map.get(template.key, lambda panel_data: 0.0)

        # Create the configured description
        synthetic_description = SpanPanelDataSensorEntityDescription(
            key=synthetic_key,
            name=template.name,
            device_class=template.device_class,
            entity_category=template.entity_category,
            entity_registry_enabled_default=template.entity_registry_enabled_default,
            entity_registry_visible_default=template.entity_registry_visible_default,
            force_update=template.force_update,
            icon=template.icon,
            has_entity_name=template.has_entity_name,
            translation_key=template.translation_key,
            translation_placeholders=template.translation_placeholders,
            unit_of_measurement=template.unit_of_measurement,
            native_unit_of_measurement=template.native_unit_of_measurement,
            state_class=template.state_class,
            suggested_display_precision=template.suggested_display_precision,
            suggested_unit_of_measurement=template.suggested_unit_of_measurement,
            value_fn=value_fn,
        )

        descriptions.append(synthetic_description)

    return descriptions


@dataclass(frozen=True)
class SpanPanelCircuitsRequiredKeysMixin:
    """Required keys mixin for Span Panel circuit sensors."""

    value_fn: Callable[[SpanPanelCircuit], float]


@dataclass(frozen=True)
class SpanPanelCircuitsSensorEntityDescription(
    SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin
):
    """Describes a Span Panel circuit sensor entity."""


@dataclass(frozen=True)
class SpanPanelDataRequiredKeysMixin:
    """Required keys mixin for Span Panel data sensors."""

    value_fn: Callable[[SpanPanelData], float | str]


@dataclass(frozen=True)
class SpanPanelDataSensorEntityDescription(
    SensorEntityDescription, SpanPanelDataRequiredKeysMixin
):
    """Describes a Span Panel data sensor entity."""


@dataclass(frozen=True)
class SpanPanelStatusRequiredKeysMixin:
    """Required keys mixin for Span Panel status sensors."""

    value_fn: Callable[[SpanPanelHardwareStatus], str]


@dataclass(frozen=True)
class SpanPanelStatusSensorEntityDescription(
    SensorEntityDescription, SpanPanelStatusRequiredKeysMixin
):
    """Describes a Span Panel status sensor entity."""


@dataclass(frozen=True)
class SpanPanelStorageBatteryRequiredKeysMixin:
    """Required keys mixin for Span Panel storage battery sensors."""

    value_fn: Callable[[SpanPanelStorageBattery], int]


@dataclass(frozen=True)
class SpanPanelStorageBatterySensorEntityDescription(
    SensorEntityDescription, SpanPanelStorageBatteryRequiredKeysMixin
):
    """Describes a Span Panel storage battery sensor entity."""


# pylint: disable=unexpected-keyword-arg
CIRCUITS_SENSORS: tuple[
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
] = (
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_POWER,
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda circuit: abs(circuit.instant_power),
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_PRODUCED,
        name="Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.produced_energy,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key=CIRCUITS_ENERGY_CONSUMED,
        name="Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda circuit: circuit.consumed_energy,
    ),
)

PANEL_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="instantGridPowerW",
        name="Current Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda panel_data: panel_data.instant_grid_power,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughPowerW",
        name="Feed Through Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda panel_data: panel_data.feedthrough_power,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergy.producedEnergyWh",
        name="Main Meter Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.main_meter_energy_produced,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergy.consumedEnergyWh",
        name="Main Meter Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.main_meter_energy_consumed,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergy.producedEnergyWh",
        name="Feed Through Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.feedthrough_energy_produced,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergy.consumedEnergyWh",
        name="Feed Through Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda panel_data: panel_data.feedthrough_energy_consumed,
    ),
)

# Generic synthetic sensor templates that can be applied to any multi-circuit entity
SYNTHETIC_SENSOR_TEMPLATES: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="instant_power",
        name="Instant Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda panel_data: 0.0,  # Placeholder - will be replaced
    ),
    SpanPanelDataSensorEntityDescription(
        key="energy_produced",
        name="Energy Produced",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda panel_data: 0.0,  # Placeholder - will be replaced
    ),
    SpanPanelDataSensorEntityDescription(
        key="energy_consumed",
        name="Energy Consumed",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda panel_data: 0.0,  # Placeholder - will be replaced
    ),
)

PANEL_DATA_STATUS_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key=CURRENT_RUN_CONFIG,
        name="Current Run Config",
        value_fn=lambda panel_data: panel_data.current_run_config,
    ),
    SpanPanelDataSensorEntityDescription(
        key=DSM_GRID_STATE,
        name="DSM Grid State",
        value_fn=lambda panel_data: panel_data.dsm_grid_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key=DSM_STATE,
        name="DSM State",
        value_fn=lambda panel_data: panel_data.dsm_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key=MAIN_RELAY_STATE,
        name="Main Relay State",
        value_fn=lambda panel_data: panel_data.main_relay_state,
    ),
)

STATUS_SENSORS: tuple[SpanPanelStatusSensorEntityDescription] = (
    SpanPanelStatusSensorEntityDescription(
        key=STATUS_SOFTWARE_VER,
        name="Software Version",
        value_fn=lambda status: getattr(status, "firmware_version", "unknown_version"),
    ),
)

STORAGE_BATTERY_SENSORS: tuple[SpanPanelStorageBatterySensorEntityDescription] = (
    SpanPanelStorageBatterySensorEntityDescription(
        key=STORAGE_BATTERY_PERCENTAGE,
        name="SPAN Storage Battery Percentage",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda storage_battery: (storage_battery.storage_battery_percentage),
    ),
)

ICON = "mdi:flash"
_LOGGER: logging.Logger = logging.getLogger(__name__)

T = TypeVar("T", bound=SensorEntityDescription)
D = TypeVar("D")  # For the type returned by get_data_source


class SpanSensorBase(CoordinatorEntity[SpanPanelCoordinator], SensorEntity, Generic[T, D]):
    """Base class for Span Panel Sensors."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize Span Panel Sensor base entity."""
        super().__init__(data_coordinator, context=description)
        # See developer_attrtribute_readme.md for why we use
        # entity_description instead of _attr_entity_descriptio
        self.entity_description = description

        if hasattr(description, "device_class"):
            self._attr_device_class = description.device_class

        device_info: DeviceInfo = panel_to_device_info(span_panel)
        self._attr_device_info = device_info
        base_name: str | None = getattr(description, "name", None)

        if (
            data_coordinator.config_entry is not None
            and data_coordinator.config_entry.options.get(USE_DEVICE_PREFIX, False)
            and "name" in device_info
        ):
            self._attr_name = f"{device_info['name']} {base_name or ''}"
        else:
            self._attr_name = base_name or ""

        if span_panel.status.serial_number and description.key:
            self._attr_unique_id = (
                f"span_{span_panel.status.serial_number}_{description.key}"
            )

        self._attr_icon = "mdi:flash"
        _LOGGER.debug("CREATE SENSOR SPAN [%s]", self._attr_name)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_native_value()
        super()._handle_coordinator_update()

    def _update_native_value(self) -> None:
        """Update the native value of the sensor."""
        if not self.coordinator.last_update_success:
            self._attr_native_value = None
            return

        value_function: Callable[[D], float | int | str | None] | None = getattr(
            self.entity_description, "value_fn", None
        )
        if value_function is None:
            self._attr_native_value = None
            return

        try:
            data_source: D = self.get_data_source(self.coordinator.data)

            # Debug logging specifically for circuit power sensors
            if hasattr(self, "id") and hasattr(data_source, "instant_power"):
                circuit_id = getattr(self, "id", "unknown")
                instant_power = getattr(data_source, "instant_power", None)
                description_key = getattr(self.entity_description, "key", "unknown")
                _LOGGER.debug(
                    "CIRCUIT_POWER_DEBUG: Circuit %s, sensor %s, instant_power=%s, data_source type=%s",
                    circuit_id,
                    description_key,
                    instant_power,
                    type(data_source).__name__,
                )

                # Extra debug for circuit 15 (the problematic one)
                if circuit_id == "15":
                    _LOGGER.debug(
                        "CIRCUIT_15_POWER_DEBUG: Circuit 15 detailed - instant_power=%s, produced_energy=%s, consumed_energy=%s",
                        instant_power,
                        getattr(data_source, "produced_energy", None),
                        getattr(data_source, "consumed_energy", None),
                    )

            raw_value: float | int | str | None = value_function(data_source)

            # Debug the function result
            if hasattr(self, "id") and hasattr(data_source, "instant_power"):
                circuit_id = getattr(self, "id", "unknown")
                description_key = getattr(self.entity_description, "key", "unknown")
                _LOGGER.debug(
                    "CIRCUIT_POWER_RESULT: Circuit %s, sensor %s, raw_value after value_function=%s",
                    circuit_id,
                    description_key,
                    raw_value,
                )

            _LOGGER.debug("native_value:[%s] [%s]", self._attr_name, raw_value)

            if raw_value is None:
                self._attr_native_value = None
            elif isinstance(raw_value, float | int):
                self._attr_native_value = float(raw_value)
            else:
                # For string values, keep as string - this is valid for Home Assistant sensors
                self._attr_native_value = str(raw_value)  # type: ignore[assignment]
        except (AttributeError, KeyError, IndexError) as e:
            _LOGGER.debug(
                "CIRCUIT_POWER_ERROR: Error in _update_native_value for %s: %s",
                self._attr_name,
                e,
            )
            self._attr_native_value = None

    def get_data_source(self, span_panel: SpanPanel) -> D:
        """Get the data source for the sensor."""
        raise NotImplementedError("Subclasses must implement this method")


class SpanPanelCircuitSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanPanelCircuit]
):
    """Span Panel circuit sensor entity."""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        circuit_id: str,
        name: str,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        # Get the circuit from the span_panel to access its properties
        circuit = span_panel.circuits.get(circuit_id)
        if not circuit:
            raise ValueError(f"Circuit {circuit_id} not found")

        # Get the circuit number (tab position)
        circuit_number = circuit.tabs[0] if circuit.tabs else circuit_id

        entity_suffix = get_user_friendly_suffix(description.key)
        self.entity_id = construct_entity_id(  # type: ignore[assignment]
            coordinator, span_panel, "sensor", name, circuit_number, entity_suffix
        )

        friendly_name = f"{name} {description.name}"

        # Create a new description with the friendly name
        circuit_description = SpanPanelCircuitsSensorEntityDescription(
            key=description.key,
            name=friendly_name,
            device_class=description.device_class,
            entity_category=description.entity_category,
            entity_registry_enabled_default=description.entity_registry_enabled_default,
            entity_registry_visible_default=description.entity_registry_visible_default,
            force_update=description.force_update,
            icon=description.icon,
            has_entity_name=description.has_entity_name,
            translation_key=description.translation_key,
            translation_placeholders=description.translation_placeholders,
            unit_of_measurement=description.unit_of_measurement,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            suggested_unit_of_measurement=description.suggested_unit_of_measurement,
            value_fn=description.value_fn,
        )

        super().__init__(coordinator, circuit_description, span_panel)
        self.id: str = circuit_id
        self._attr_unique_id = (
            f"span_{span_panel.status.serial_number}_{circuit_id}_{description.key}"
        )

        # Ensure the native_unit_of_measurement is set correctly from the description
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement

        # Store initial circuit name for change detection in auto-sync names
        self._previous_circuit_name = name

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Check for circuit name changes
        span_panel: SpanPanel = self.coordinator.data
        circuit = span_panel.circuits.get(self.id)
        if circuit:
            current_circuit_name = circuit.name

            # Only request reload if the circuit name has actually changed
            if current_circuit_name != self._previous_circuit_name:
                _LOGGER.info(
                    "Auto-sync detected circuit name change from '%s' to '%s', requesting integration reload",
                    self._previous_circuit_name,
                    current_circuit_name,
                )

                # Update stored previous name for next comparison
                self._previous_circuit_name = current_circuit_name

                # Request integration reload for next update cycle
                self.coordinator.request_reload()

        super()._handle_coordinator_update()

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelCircuit:
        """Get the data source for the circuit sensor."""
        return span_panel.circuits[self.id]


class SpanPanelPanel(SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]):
    """Span Panel data sensor entity."""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel sensor."""
        return span_panel.panel


class SpanPanelPanelStatus(
    SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]
):
    """Span Panel status sensor entity."""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the panel status sensor."""
        return span_panel.panel


class SpanPanelStatus(
    SpanSensorBase[SpanPanelStatusSensorEntityDescription, SpanPanelHardwareStatus]
):
    """Span Panel hardware status sensor entity."""

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelHardwareStatus:
        """Get the data source for the panel status sensor."""
        return span_panel.status


class SpanPanelStorageBatteryStatus(
    SpanSensorBase[SpanPanelStorageBatterySensorEntityDescription, SpanPanelStorageBattery]
):
    """Span Panel storage battery sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelStorageBatterySensorEntityDescription,
        span_panel: SpanPanel,
    ) -> None:
        """Initialize the storage battery sensor."""
        super().__init__(data_coordinator, description, span_panel)
        self._attr_icon = "mdi:battery"

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelStorageBattery:
        """Get the data source for the storage battery status sensor."""
        return span_panel.storage_battery


class SpanPanelSyntheticSensor(
    SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelData]
):
    """Generic span panel synthetic sensor entity for multi-circuit entities."""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        span_panel: SpanPanel,
        circuit_numbers: list[int],
        friendly_name: str | None = None,
        key_prefix: str | None = None,
    ) -> None:
        """Initialize Span Panel Synthetic Sensor entity."""
        # Extract template key from synthetic key for entity ID construction
        # Synthetic keys follow pattern: "{prefix}_{template_key}"
        # We need the template key for proper entity ID suffix generation
        if key_prefix and description.key.startswith(f"{key_prefix}_"):
            # Remove the known prefix to get the template key
            template_key = description.key[len(key_prefix) + 1 :]
        elif "_" in description.key:
            # Fallback: extract everything after the first underscore
            template_key = description.key.split("_", 1)[1]
        else:
            # Fallback for unexpected format
            template_key = description.key

        # Use the helper function to construct appropriate entity ID
        entity_suffix = get_user_friendly_suffix(template_key)
        self.entity_id = construct_synthetic_entity_id(  # type: ignore[assignment]
            coordinator,
            span_panel,
            "sensor",
            circuit_numbers,
            entity_suffix,
            friendly_name,
        )

        # Create display name using friendly name if provided
        description_name = getattr(description, "name", "Unknown") or "Unknown"
        if friendly_name:
            display_name: str = f"{friendly_name} {description_name}"
        else:
            # No friendly name provided - use circuit-based fallback
            display_name = construct_synthetic_friendly_name(
                circuit_numbers=circuit_numbers,
                suffix_description=description_name,
                user_friendly_name=None,
            )

        # Create a new description with the friendly name
        synthetic_description = SpanPanelDataSensorEntityDescription(
            key=description.key,
            name=display_name,
            device_class=description.device_class,
            entity_category=description.entity_category,
            entity_registry_enabled_default=description.entity_registry_enabled_default,
            entity_registry_visible_default=description.entity_registry_visible_default,
            force_update=description.force_update,
            icon=description.icon,
            has_entity_name=description.has_entity_name,
            translation_key=description.translation_key,
            translation_placeholders=description.translation_placeholders,
            unit_of_measurement=description.unit_of_measurement,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            suggested_unit_of_measurement=description.suggested_unit_of_measurement,
            value_fn=description.value_fn,
        )

        super().__init__(coordinator, synthetic_description, span_panel)

        # For synthetic sensors, we want complete control over the friendly name
        self._attr_name = display_name

        # Create unique ID based on circuit numbers
        circuit_spec = "_".join(str(num) for num in circuit_numbers)
        self._attr_unique_id = f"span_{span_panel.status.serial_number}_synthetic_{circuit_spec}_{description.key}"

        # Ensure the native_unit_of_measurement is set correctly from the description
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement

    def get_data_source(self, span_panel: SpanPanel) -> SpanPanelData:
        """Get the data source for the synthetic sensor."""
        return span_panel.panel


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SpanPanelCoordinator = data[COORDINATOR]
    span_panel: SpanPanel = coordinator.data

    entities: list[SpanSensorBase[Any, Any]] = []

    for description in PANEL_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Config entry should never be None here, but we check for safety
    solar_enabled = config_entry.options.get(INVERTER_ENABLE, False)
    solar_descriptions = []  # Initialize for logging
    _LOGGER.info(
        "Solar sensor setup - enabled: %s, options: %s",
        solar_enabled,
        config_entry.options,
    )

    if solar_enabled:
        # Get inverter leg configuration from options
        from .options import INVERTER_LEG1, INVERTER_LEG2

        inverter_leg1 = config_entry.options.get(INVERTER_LEG1, 0)
        inverter_leg2 = config_entry.options.get(INVERTER_LEG2, 0)

        # Create solar inverter synthetic sensor configuration
        solar_config = SyntheticSensorConfig(
            friendly_name="Solar Inverter",
            circuit_numbers=[inverter_leg1, inverter_leg2],
            key_prefix="solar_inverter",
            value_fn_map={
                "instant_power": lambda panel_data: panel_data.solar_inverter_instant_power,
                "energy_produced": lambda panel_data: panel_data.solar_inverter_energy_produced,
                "energy_consumed": lambda panel_data: panel_data.solar_inverter_energy_consumed,
            },
        )

        # Create the synthetic sensor descriptions
        solar_descriptions = create_synthetic_sensor_descriptions(solar_config)

        # Create entities from the synthetic descriptions
        _LOGGER.info("Creating %d solar sensor entities", len(solar_descriptions))
        for description_i in solar_descriptions:
            solar_sensor = SpanPanelSyntheticSensor(
                coordinator,
                description_i,
                span_panel,
                solar_config.circuit_numbers,
                solar_config.friendly_name,
                solar_config.key_prefix,
            )
            _LOGGER.info(
                "Created solar sensor: %s (unique_id: %s)",
                solar_sensor.entity_id,
                solar_sensor.unique_id,
            )
            entities.append(solar_sensor)

    for description_ss in STATUS_SENSORS:
        entities.append(SpanPanelStatus(coordinator, description_ss, span_panel))

    # Create circuit sensors (excluding synthetics)
    circuit_sensor_count = 0
    for description_cs in CIRCUITS_SENSORS:
        for id_c, circuit_data in span_panel.circuits.items():
            entities.append(
                SpanPanelCircuitSensor(
                    coordinator, description_cs, id_c, circuit_data.name, span_panel
                )
            )
            circuit_sensor_count += 1

    _LOGGER.info(
        "Created %d circuit sensors for %d circuits (%d sensors per circuit)",
        circuit_sensor_count,
        len(span_panel.circuits),
        len(CIRCUITS_SENSORS),
    )
    if config_entry is not None and config_entry.options.get(BATTERY_ENABLE, False):
        for description_sb in STORAGE_BATTERY_SENSORS:
            entities.append(
                SpanPanelStorageBatteryStatus(coordinator, description_sb, span_panel)
            )

    async_add_entities(entities)
