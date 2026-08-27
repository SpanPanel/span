"""Circuit-level sensors for Span Panel integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, ClassVar

from homeassistant.helpers.device_registry import DeviceInfo
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from .const import USE_CIRCUIT_NUMBERS
from .coordinator import SpanPanelCoordinator
from .helpers import (
    construct_circuit_identifier_from_tabs,
    construct_circuit_unique_id_for_entry,
    construct_tabs_attribute,
    construct_unmapped_friendly_name,
    construct_voltage_attribute,
    get_user_friendly_suffix,
)
from .sensor_base import SpanEnergySensorBase, SpanSensorBase
from .sensor_definitions import SpanPanelCircuitsSensorEntityDescription


def _get_circuit_data_source(circuit_id: str, snapshot: SpanPanelSnapshot) -> SpanCircuitSnapshot:
    """Look up a circuit in the snapshot, raising KeyError if temporarily missing."""
    circuit = snapshot.circuits.get(circuit_id)
    if circuit is None:
        raise KeyError(f"Circuit {circuit_id} not found in panel data")
    return circuit


# Device types that use "Solar" as the fallback identifier when unnamed,
# matching v1 naming conventions (e.g., "Solar Power", "Solar Produced Energy").
_SOLAR_DEVICE_TYPES: frozenset[str] = frozenset({"pv"})

# Device types that use "EV Charger" as the fallback identifier when unnamed.
_EVSE_DEVICE_TYPES: frozenset[str] = frozenset({"evse"})


def _unnamed_circuit_fallback(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    """Return a descriptive identifier for an unnamed circuit.

    PV circuits use "Solar" (matching v1 naming), EVSE circuits use "EV Charger",
    all others use tab-based naming.
    """
    device_type = getattr(circuit, "device_type", "circuit")
    if device_type in _SOLAR_DEVICE_TYPES:
        return "Solar"
    if device_type in _EVSE_DEVICE_TYPES:
        return "EV Charger"
    return construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)


def _resolve_circuit_identifier(
    circuit: SpanCircuitSnapshot,
    circuit_id: str,
    options: Mapping[str, Any],
) -> str:
    """Resolve the circuit identifier respecting user naming preference.

    Always answers, because this is the half of the entity id the naming flags
    decide and there is nothing sensible for Home Assistant to fall back to. An
    unnamed circuit in friendly-name mode used to answer `None` and let Core
    compose from the description label alone, which gives every unnamed circuit
    on the panel the same `sensor.<panel>_power` and leaves the registry to tell
    them apart with `_2`, `_3`, ... in whatever order they were added. The tab
    fallback names them after the breaker position they occupy instead.
    """
    use_circuit_numbers = options.get(USE_CIRCUIT_NUMBERS, False)

    if use_circuit_numbers:
        return construct_circuit_identifier_from_tabs(circuit.tabs, circuit_id)

    name: str = circuit.name
    if name:
        return name

    return _unnamed_circuit_fallback(circuit, circuit_id)


def _resolve_circuit_identifier_for_sync(circuit: SpanCircuitSnapshot, circuit_id: str) -> str:
    """Resolve the circuit identifier for name-sync (always panel name, with fallback)."""
    name: str = circuit.name
    if name:
        return name
    return _unnamed_circuit_fallback(circuit, circuit_id)


_API_KEY_MAP: Mapping[str, str] = MappingProxyType(
    {
        # Instantaneous readings.
        "circuit_power": "instantPowerW",
        "circuit_current": "current",
        "circuit_breaker_rating": "breaker_rating",
        # Totals, under the original API names migration normalized them from.
        "circuit_energy_produced": "producedEnergyWh",
        "circuit_energy_consumed": "consumedEnergyWh",
        "circuit_energy_net": "netEnergyWh",
    }
)
"""Catalog key to the wire key a circuit sensor's identity is built from.

Both the unique id and the entity-id suffix key on this rather than on the
circuit id, because the description handed to Core is rebuilt around the circuit
id and no longer remembers which sensor it is.

One table for both families, which are disjoint by construction: `sensor.py`
hands the first three keys only to `SpanCircuitPowerSensor` and the last three
only to `SpanCircuitEnergySensor`, so neither can shadow the other. It used to
be two class attributes of the same name, which is how they were mistaken for a
duplicate of each other.
"""


class SpanCircuitSensorBase(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanCircuitSnapshot]
):
    """What a circuit's power and energy sensors share: how they identify themselves.

    Both are handed a catalog description and a circuit id, rebuild the
    description around that id so the base class looks the reading up by it, and
    key their unique id and entity-id suffix on `_API_KEY_MAP`. What genuinely
    differs stays on the two subclasses: the residual field paths, the displayed
    name, and the state attributes.

    Cooperative `super()` throughout, because the energy sensor mixes this with
    `SpanEnergySensorBase`: the `__init__` below reaches the grace-period base on
    that class and `SpanSensorBase` directly on the power one.
    """

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
        device_info_override: DeviceInfo | None = None,
    ) -> None:
        """Initialize a circuit sensor bound to one circuit."""
        # Set before `super().__init__`, which calls the three methods below
        # while it composes the entity's identity.
        self.circuit_id = circuit_id
        self.original_key = description.key
        self._is_sub_device = device_info_override is not None

        # The key becomes the circuit id because the base class looks its data
        # up by it; `original_key` keeps what the catalog called this sensor.
        #
        # `replace` rather than the field-by-field copy this replaced: that copy
        # was restated in both classes and had already drifted -- the energy one
        # omitted `entity_category` -- and any field added to the description
        # later would have been dropped by both.
        super().__init__(data_coordinator, replace(description, key=circuit_id), snapshot)

        if device_info_override is not None:
            self._attr_device_info = device_info_override

    @property
    def _api_key(self) -> str:
        """The wire key this sensor's unique id and entity-id suffix are built from."""
        return _API_KEY_MAP.get(self.original_key, self.original_key)

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str:
        """Generate unique ID for circuit sensors."""
        return construct_circuit_unique_id_for_entry(
            self.coordinator, snapshot, self.circuit_id, self._api_key, self._device_name
        )

    def _object_id_parts(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> tuple[str, str] | None:
        """Return the circuit identifier and the suffix this sensor's id ends with."""
        circuit = snapshot.circuits.get(self.circuit_id)
        if not circuit:
            return None
        identifier = _resolve_circuit_identifier(
            circuit, self.circuit_id, self.coordinator.config_entry.options
        )
        return identifier, get_user_friendly_suffix(self._api_key)

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanCircuitSnapshot:
        """Get the circuit this sensor reads."""
        return _get_circuit_data_source(self.circuit_id, snapshot)


class SpanCircuitPowerSensor(SpanCircuitSensorBase):
    """Circuit power/current/breaker-rating sensor with extra state attributes."""

    # Beyond the value the description declares: `name` and `tabs` build the
    # entity's identity (~56-83), and `tabs`, `relay_state`, `relay_requester`
    # and `priority` are republished as state attributes (~231-243).
    _residual_field_paths: ClassVar[tuple[str, ...]] = (
        "circuit.name",
        "circuit.tabs",
        "circuit.relay_state",
        "circuit.relay_requester",
        "circuit.priority",
    )

    def _generate_panel_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str:
        """Generate panel name for circuit sensors (always uses panel circuit name)."""
        if self._is_sub_device:
            return str(description.name or "Sensor")

        circuit = snapshot.circuits.get(self.circuit_id)
        if not circuit:
            return construct_unmapped_friendly_name(
                self.circuit_id, str(description.name or "Sensor")
            )

        circuit_identifier = _resolve_circuit_identifier_for_sync(circuit, self.circuit_id)
        return f"{circuit_identifier} {description.name or 'Sensor'}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return None

        circuit = self.coordinator.data.circuits.get(self.circuit_id)
        if not circuit:
            return None

        attributes: dict[str, Any] = {}

        # Panel position (tabs)
        tabs_result = construct_tabs_attribute(circuit)
        if tabs_result is not None:
            attributes["tabs"] = tabs_result

        # Voltage derived from tab count
        voltage = construct_voltage_attribute(circuit)
        if voltage is not None:
            attributes["voltage"] = voltage

        attributes["always_on"] = circuit.always_on
        attributes["relay_state"] = circuit.relay_state
        attributes["relay_requester"] = circuit.relay_requester
        attributes["shed_priority"] = circuit.priority
        attributes["is_sheddable"] = circuit.is_sheddable

        # This circuit's participation in the enclosure's Power Control System,
        # beside its load-shed participation above. Two policies on the same
        # relay, and the catalog keeps them apart because they answer different
        # questions — limit site import, versus preserve backup runtime — so the
        # attribute names do too: `pcs_priority` is an integer shed ordering
        # under an import limit, `shed_priority` the backup tier.
        #
        # Attributes rather than entities: a 40-space panel would otherwise gain
        # eighty entities carrying two facts that change only when somebody
        # reconfigures the panel.
        #
        # Omitted when the circuit publishes neither, which is every flat
        # circuit and any v1.0 circuit outside a PCS. Both properties are `MAY`,
        # so absence is conformant firmware; an attribute present and empty would
        # read as a reading that failed, and `False` / `0` would each be a claim
        # the panel never made.
        if circuit.pcs_managed is not None:
            attributes["pcs_managed"] = circuit.pcs_managed
        if circuit.pcs_priority is not None:
            attributes["pcs_priority"] = circuit.pcs_priority

        return attributes


class SpanCircuitEnergySensor(
    SpanCircuitSensorBase,
    SpanEnergySensorBase[SpanPanelCircuitsSensorEntityDescription, SpanCircuitSnapshot],
):
    """Circuit energy sensor with grace period tracking."""

    # Naming only; this sensor publishes no circuit attributes.
    _residual_field_paths: ClassVar[tuple[str, ...]] = ("circuit.name", "circuit.tabs")

    async def async_added_to_hass(self) -> None:
        """Register consumed/produced sensors on the coordinator for net energy lookup."""
        await super().async_added_to_hass()
        energy_type = self._ENERGY_TYPE_MAP.get(self.original_key)
        if energy_type:
            self.coordinator.register_circuit_energy_sensor(self.circuit_id, energy_type, self)

    def _generate_panel_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str:
        """Generate panel name for circuit energy sensors (always uses panel circuit name)."""
        if self._is_sub_device:
            return str(description.name)

        circuit = snapshot.circuits.get(self.circuit_id)
        if not circuit:
            return f"Circuit {self.circuit_id} {description.name}"

        circuit_identifier = _resolve_circuit_identifier_for_sync(circuit, self.circuit_id)
        return f"{circuit_identifier} {description.name}"

    # Map original_key to the energy type used for coordinator dip offset tracking
    _ENERGY_TYPE_MAP: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "circuit_energy_consumed": "consumed",
            "circuit_energy_produced": "produced",
        }
    )

    def _process_raw_value(self, raw_value: float | str | None) -> None:
        """Process raw value, adjusting net energy for dip compensation consistency.

        Consumed/produced sensors apply dip offsets via the base class. The net
        energy sensor reads those offsets from the registered sibling sensors
        so its value stays equal to compensated_consumed - compensated_produced.
        """
        super()._process_raw_value(raw_value)

        if self.original_key == "circuit_energy_net" and isinstance(self._attr_native_value, float):
            consumed_offset = self.coordinator.get_circuit_dip_offset(self.circuit_id, "consumed")
            produced_offset = self.coordinator.get_circuit_dip_offset(self.circuit_id, "produced")
            net_adjustment = consumed_offset - produced_offset
            if net_adjustment:
                self._attr_native_value += net_adjustment

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period and circuit info."""
        # Get base grace period attributes
        base_attributes = super().extra_state_attributes or {}
        attributes = dict(base_attributes)

        # Add circuit-specific attributes if we have data
        if self.coordinator.data:
            circuit = self.coordinator.data.circuits.get(self.circuit_id)

            if circuit:
                tabs = construct_tabs_attribute(circuit)
                if tabs is not None:
                    attributes["tabs"] = tabs

                voltage = construct_voltage_attribute(circuit)
                if voltage is not None:
                    attributes["voltage"] = voltage

        return attributes or None


class SpanUnmappedCircuitSensor(
    SpanSensorBase[SpanPanelCircuitsSensorEntityDescription, SpanCircuitSnapshot]
):
    """Span Panel unmapped circuit sensor entity - native sensors for synthetic calculations."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanPanelCircuitsSensorEntityDescription,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
    ) -> None:
        """Initialize the Span Panel unmapped circuit sensor."""
        self.circuit_id = circuit_id
        # Store the original description key for unique ID and entity ID generation
        self.original_key = description.key

        # Override the description key to use the circuit_id for data lookup
        description_with_circuit = SpanPanelCircuitsSensorEntityDescription(
            key=circuit_id,
            name=description.name,
            native_unit_of_measurement=description.native_unit_of_measurement,
            state_class=description.state_class,
            suggested_display_precision=description.suggested_display_precision,
            device_class=description.device_class,
            value_fn=description.value_fn,
            field_path=description.field_path,
            derived=description.derived,
            entity_registry_enabled_default=True,
            entity_registry_visible_default=False,
            legacy_names=description.legacy_names,
        )

        super().__init__(data_coordinator, description_with_circuit, snapshot)

    def _generate_unique_id(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str:
        """Generate unique ID for unmapped circuit sensors."""
        return construct_circuit_unique_id_for_entry(
            self.coordinator,
            snapshot,
            self.circuit_id,
            self.original_key,
            self._device_name,
        )

    def _generate_panel_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> str:
        """Name an unmapped tab, which has no circuit name for a mode to differ over."""
        return self._unmapped_name(description)

    def _unmapped_name(self, description: SpanPanelCircuitsSensorEntityDescription) -> str:
        """Return "Unmapped Tab 32 Consumed Energy" and the like."""
        tab_number = self.circuit_id.replace("unmapped_tab_", "")
        description_name = str(description.name) if description.name else "Sensor"
        return construct_unmapped_friendly_name(tab_number, description_name)

    def _object_id_parts(
        self,
        snapshot: SpanPanelSnapshot,
        description: SpanPanelCircuitsSensorEntityDescription,
    ) -> tuple[str, str] | None:
        """Return the tab this sensor backs; unmapped tabs carry no naming flag."""
        return (
            f"Unmapped Tab {self.circuit_id.replace('unmapped_tab_', '')}",
            get_user_friendly_suffix(self.original_key),
        )

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> SpanCircuitSnapshot:
        """Get the data source for the unmapped circuit sensor."""
        return _get_circuit_data_source(self.circuit_id, snapshot)
