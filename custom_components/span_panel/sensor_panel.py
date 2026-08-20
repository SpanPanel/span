"""Panel-level sensors for Span Panel integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import UNDEFINED
from span_panel_api import SpanBatterySnapshot, SpanMidSnapshot, SpanPanelSnapshot

from .coordinator import SpanPanelCoordinator
from .helpers import (
    build_bess_unique_id_for_entry,
    build_mid_unique_id_for_entry,
    construct_panel_unique_id_for_entry,
    construct_synthetic_unique_id_for_entry,
    get_panel_entity_suffix,
)
from .sensor_base import SpanEnergySensorBase, SpanSensorBase
from .sensor_definitions import (
    SpanBessMetadataSensorEntityDescription,
    SpanMidSensorEntityDescription,
    SpanPanelBatterySensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelStatusSensorEntityDescription,
    SpanPVMetadataSensorEntityDescription,
    SpanShedForecastSensorEntityDescription,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)


def _grid_forming_device_name(snapshot: SpanPanelSnapshot) -> str | None:
    """Return the forming device's readable name, when the library knows it.

    The state stays the source *class* — `GRID`, `BATTERY`, `PV` — because that is the
    closed enum automations compare against and it must not change. v1.0 additionally
    knows *which* device, which flat never did, and that belongs here: an attribute
    refines a value already on screen without adding entity-list noise, and cannot break
    an automation that never referenced it.

    Deliberately the display name and not the wire id. `sim-40t-001-SIM-BESS-40T-001` is
    a Homie device id, not a Home Assistant one, and an opaque string on a dashboard is
    worse than none. The id stays in the snapshot for correlation and diagnostics.

    DUAL-SCHEMA: `None` on any flat panel, which publishes no MID, so the attribute simply
    does not appear there. The library field is always present now that the pin is
    3.0.0b3 — the conditional is about what the *panel* publishes, not about which
    library is installed, and the earlier `getattr` guarding the latter has gone.
    """
    mid = snapshot.mid
    if mid is None:
        return None
    return mid.grid_forming_device_name


class SpanPanelPanelStatus(SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelSnapshot]):
    """Span Panel data status sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize the Span Panel data status sensor."""
        super().__init__(data_coordinator, description, snapshot)

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str:
        """Generate unique ID for panel data sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, snapshot, description.key, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str:
        """Generate friendly name for panel data sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Sensor"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        """Get the data source for the panel data status sensor."""
        return snapshot


class SpanShedForecastSensor(
    SpanSensorBase[SpanShedForecastSensorEntityDescription, SpanPanelSnapshot]
):
    """One of the two backup-planning estimates, with its refinements attached.

    Created only where the panel publishes the estimate this sensor reads, so a
    panel with no `shed-forecast` node — every flat panel, and any v1.0 panel
    whose firmware omits the capability — gets no entity rather than one stuck
    at unknown. See `create_shed_forecast_sensors`.
    """

    # `_residual_field_paths` stays empty on purpose. The attribute reads below
    # are not declarable: neither adapter carries a metadata row for those three
    # fields, so declaring them here would put them in `declared_field_paths()`
    # where the producible gate rejects anything one adapter cannot emit. They
    # are enumerated in `RESIDUAL_EXEMPT_PATHS` as `Producibility.NEITHER`
    # instead, which is where the `mid.*` attribute reads live for the same
    # reason.

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanShedForecastSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize the shed-forecast sensor, keeping a typed handle on its description.

        `SensorEntity.entity_description` is annotated as the base
        `SensorEntityDescription`, so reading the two extra members off it would
        need either a narrowing override — which mypy rejects on a mutable
        attribute — or a `getattr`, which is the same thing with the check
        removed. Keeping the description under a name of our own is what makes
        `full_charge_fn` and `full_charge_attribute` statically checked; the same
        move `SpanPanelPowerSensor` makes for `_description_key`.
        """
        super().__init__(data_coordinator, description, snapshot)
        self._forecast = description

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanShedForecastSensorEntityDescription,
    ) -> str:
        """Generate unique ID for a shed-forecast sensor."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, snapshot, description.key, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanShedForecastSensorEntityDescription,
    ) -> str:
        """Generate friendly name for a shed-forecast sensor."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Shed Forecast"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        """Get the data source for the shed-forecast sensor."""
        return snapshot

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The hypothetical-full-charge twin, and the estimate's confidence.

        Both are omitted when the panel does not publish them, rather than
        appearing as `None`. An attribute that is present and empty reads as a
        reading the panel failed to produce; an absent one reads as a firmware
        that does not carry it, which is what this is.

        Which twin belongs to this sensor comes from the description, not from a
        comparison against `key` — the pairing is stated once, where the two
        readers sit beside each other.
        """
        snapshot = self.coordinator.data
        if snapshot is None:
            return None

        attributes: dict[str, Any] = {}

        full_charge = self._forecast.full_charge_fn(snapshot)
        if full_charge is not None:
            attributes[self._forecast.full_charge_attribute] = full_charge

        confidence = snapshot.shed_forecast_confidence
        if confidence is not None:
            attributes["forecast_confidence"] = confidence

        return attributes or None


class SpanPanelStatus(SpanSensorBase[SpanPanelStatusSensorEntityDescription, SpanPanelSnapshot]):
    """Span Panel hardware status sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelStatusSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize the Span Panel hardware status sensor."""
        super().__init__(data_coordinator, description, snapshot)

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelStatusSensorEntityDescription,
    ) -> str:
        """Generate unique ID for panel status sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, snapshot, description.key, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelStatusSensorEntityDescription,
    ) -> str:
        """Generate friendly name for panel status sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Status"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        """Get the data source for the panel status sensor."""
        return snapshot

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes for the software version sensor."""
        if not self.coordinator.data:
            return None

        snapshot = self.coordinator.data
        attributes: dict[str, Any] = {}

        attributes["panel_size"] = snapshot.panel_size
        if snapshot.wifi_ssid is not None:
            attributes["wifi_ssid"] = snapshot.wifi_ssid

        if self.entity_description.key == "grid_forming_entity":
            forming = _grid_forming_device_name(snapshot)
            if forming is not None:
                attributes["grid_forming_device"] = forming

        return attributes or None


class SpanPanelBattery(
    SpanSensorBase[SpanPanelBatterySensorEntityDescription, SpanBatterySnapshot]
):
    """Span Panel battery sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelBatterySensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        device_info_override: DeviceInfo | None = None,
    ) -> None:
        """Initialize the Span Panel battery sensor."""
        super().__init__(data_coordinator, description, snapshot)

        if device_info_override is not None:
            self._attr_device_info = device_info_override

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelBatterySensorEntityDescription,
    ) -> str:
        """Generate unique ID for battery sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, snapshot, description.key, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelBatterySensorEntityDescription,
    ) -> str:
        """Generate friendly name for battery sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Battery"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanBatterySnapshot:
        """Get the data source for the battery sensor."""
        return snapshot.battery


class SpanPanelPowerSensor(SpanSensorBase[SpanPanelDataSensorEntityDescription, SpanPanelSnapshot]):
    """Panel power sensor with calculated amperage attribute."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        device_info_override: DeviceInfo | None = None,
    ) -> None:
        """Initialize the enhanced panel power sensor."""
        self._description_key = description.key
        super().__init__(data_coordinator, description, snapshot)

        if device_info_override is not None:
            self._attr_device_info = device_info_override

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str:
        """Generate unique ID for panel power sensors."""
        entity_suffix = get_panel_entity_suffix(description.key)
        return construct_synthetic_unique_id_for_entry(
            self.coordinator, snapshot, entity_suffix, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str:
        """Generate friendly name for panel power sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Power"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        """Get the data source for the panel power sensor."""
        return snapshot

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including amperage calculation."""
        if not self.coordinator.data:
            return None

        attributes: dict[str, Any] = {}

        # Add voltage attribute (standard panel voltage)
        attributes["voltage"] = 240

        # Calculate amperage from power (P = V * I, so I = P / V)
        if self.native_value is not None and isinstance(self.native_value, int | float):
            try:
                amperage = float(self.native_value) / 240.0
                attributes["amperage"] = round(amperage, 2)
            except (ValueError, ZeroDivisionError):
                attributes["amperage"] = 0.0
        else:
            attributes["amperage"] = 0.0

        return attributes


class SpanPanelEnergySensor(
    SpanEnergySensorBase[SpanPanelDataSensorEntityDescription, SpanPanelSnapshot]
):
    """Panel energy sensor with grace period tracking."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelDataSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize the panel energy sensor."""
        super().__init__(data_coordinator, description, snapshot)

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str:
        """Generate unique ID for panel energy sensors."""
        entity_suffix = get_panel_entity_suffix(description.key)
        return construct_synthetic_unique_id_for_entry(
            self.coordinator, snapshot, entity_suffix, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelDataSensorEntityDescription,
    ) -> str:
        """Generate friendly name for panel energy sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "Energy"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        """Get the data source for the panel energy sensor."""
        return snapshot

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and voltage."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add voltage attribute (standard panel voltage)
        attributes["voltage"] = 240

        return attributes or None


class SpanBessMetadataSensor(
    SpanSensorBase[SpanBessMetadataSensorEntityDescription, SpanBatterySnapshot]
):
    """BESS metadata sensor entity on the BESS sub-device."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanBessMetadataSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        device_info_override: DeviceInfo,
    ) -> None:
        """Initialize the BESS metadata sensor."""
        super().__init__(data_coordinator, description, snapshot)
        self._attr_device_info = device_info_override

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanBessMetadataSensorEntityDescription,
    ) -> str:
        """Generate unique ID for BESS metadata sensors."""
        return build_bess_unique_id_for_entry(
            self.coordinator, snapshot, description.key, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanBessMetadataSensorEntityDescription,
    ) -> str:
        """Generate friendly name for BESS metadata sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "BESS Sensor"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanBatterySnapshot:
        """Get the data source for the BESS metadata sensor."""
        return snapshot.battery


class SpanMidSensor(SpanSensorBase[SpanMidSensorEntityDescription, SpanMidSnapshot]):
    """A sensor on the Microgrid Interconnect Device sub-device."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanMidSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        device_info_override: DeviceInfo,
    ) -> None:
        """Initialize the MID sensor."""
        super().__init__(data_coordinator, description, snapshot)
        self._attr_device_info = device_info_override

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanMidSensorEntityDescription,
    ) -> str:
        """Generate unique ID for MID sensors."""
        return build_mid_unique_id_for_entry(
            self.coordinator, snapshot, description.key, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanMidSensorEntityDescription,
    ) -> str:
        """Generate friendly name for MID sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "MID Sensor"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanMidSnapshot:
        """Get the data source for the MID sensor.

        The MID is optional, so a snapshot without one has no data source. Entities are
        only created when `has_mid` is true, and a panel that stops publishing its MID
        makes them unavailable rather than reaching this.
        """
        mid = snapshot.mid
        if mid is None:
            raise ValueError("MID sensor asked for a data source on a snapshot with no MID")
        return mid


class SpanPVMetadataSensor(
    SpanSensorBase[SpanPVMetadataSensorEntityDescription, SpanPanelSnapshot]
):
    """PV metadata sensor entity on the main panel device."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPVMetadataSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize the PV metadata sensor."""
        super().__init__(data_coordinator, description, snapshot)

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPVMetadataSensorEntityDescription,
    ) -> str:
        """Generate unique ID for PV metadata sensors."""
        return construct_panel_unique_id_for_entry(
            self.coordinator, snapshot, description.key, self._device_name
        )

    def _generate_friendly_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPVMetadataSensorEntityDescription,
    ) -> str:
        """Generate friendly name for PV metadata sensors."""
        if description.name is not None and description.name is not UNDEFINED:
            return str(description.name)
        return "PV Sensor"

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanPanelSnapshot:
        """Get the data source for the PV metadata sensor."""
        return snapshot
