"""Entities for devices the panel publishes and this integration models nothing for.

The panel is a hub for whatever plugs into it, and the eBus schema is explicitly
vendor-extensible. A device type this integration has never modelled therefore
arrives as an expected event rather than a hypothetical one -- and until now it
arrived as nothing at all: no device, no entity, no sign it was there.

**The unit of adoption is a device, never a property.** A new property on a device
this integration already models is a curation task with a short turnaround, and
minting an entity for it automatically would spend an `entity_id` permanently on
a shape a human would likely have chosen differently. That cost only bites where
curation is coming. On a device type nobody has modelled, no better identity is
coming, so a disabled diagnostic entity is strictly better than the silence.

**Nothing adopted enters long-term statistics.** No adopted entity carries a
`state_class`, ever. Three reasons, and the third is the one that shapes the
module: `state_class` is not declared on the wire and is not derivable from one
(`feedthroughEnergyProducedWh` is `TOTAL` beside `mainMeterEnergyProducedWh` as
`TOTAL_INCREASING` -- same unit, same device class); a wrong one writes corrupt
statistics that fixing the producer does not repair; and enrolling a property
nobody asked for into long-term statistics is a permanent write to every
install's recorder database. A user who wants statistics from an adopted reading
can wrap it in a template sensor, a Riemann sum or a utility meter, which is
their call to make on an entity they chose to enable.

**These entities declare no field paths.** `snapshot.adopted_devices` is outside
the curated field-path vocabulary by construction: it carries no metadata row, so
the producible gate has nothing to check it against and `residual_field_paths()`
must not collect it. That is why this module is absent from that walk's import
list, and why the classes below declare no `_residual_field_paths`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.helpers.device_registry import DeviceInfo
from span_panel_api import AdoptedDevice, AdoptedProperty, SpanPanelSnapshot

from .const import DOMAIN
from .entity import SpanPanelEntity
from .util import ADOPTED_IDENTIFIER_TOKEN

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceRegistry

    from .coordinator import SpanPanelCoordinator

_LOGGER = logging.getLogger(__name__)

BOOLEAN_DATATYPE: Final = "boolean"
ENUM_DATATYPE: Final = "enum"
NUMERIC_DATATYPES: Final = frozenset({"float", "integer"})

DEVICE_CLASS_BY_UNIT: dict[str, SensorDeviceClass] = {
    "W": SensorDeviceClass.POWER,
    "kW": SensorDeviceClass.POWER,
    "Wh": SensorDeviceClass.ENERGY,
    "kWh": SensorDeviceClass.ENERGY,
    "V": SensorDeviceClass.VOLTAGE,
    "A": SensorDeviceClass.CURRENT,
    "Hz": SensorDeviceClass.FREQUENCY,
    "VA": SensorDeviceClass.APPARENT_POWER,
    "var": SensorDeviceClass.REACTIVE_POWER,
    "°C": SensorDeviceClass.TEMPERATURE,
    "°F": SensorDeviceClass.TEMPERATURE,
    "s": SensorDeviceClass.DURATION,
}
"""Units this integration is willing to claim a device class for, enumerated.

Enumerated rather than inferred, and the omissions are the point. `%` is absent
because its uses in this vocabulary are not one class -- a state of charge, a
confidence, a duty cycle -- and guessing `BATTERY` for all of them mislabels the
rest. A unit outside this map yields **no** device class rather than a guess: an
unlabelled reading is honest, a mislabelled one is not.

Note what is *not* here: a `state_class`. A device class is a display decision
and a wrong one is fixed by a line in the next release. A state class writes
long-term statistics, and a wrong one is not repaired by fixing it afterwards.
"""


def classify(declaration: AdoptedProperty) -> Platform:
    """Return the platform a declared property surfaces on.

    Driven by the declaration, in the order the declaration constrains it:

    | Declaration | Platform |
    | --- | --- |
    | `boolean`, settable | `SWITCH` |
    | `boolean` | `BINARY_SENSOR` |
    | `enum`, settable, with a `format` | `SELECT` |
    | numeric, settable, with a `format` | `NUMBER` |
    | anything else | `SENSOR` |

    **A settable property with no usable value domain falls back to a reading,
    and that is not caution.** A select with no option list and a number with no
    bounds are not safer controls; they are broken ones. `format` is where Homie
    carries the domain, so its absence is the absence of the thing a control
    needs.

    Disabled-by-default is what gates a control, not read-only. Enabling an
    entity is a deliberate act and commanding it is a second one, the panel
    authorises the write regardless of what is created here, and this
    integration already ships switches that open and close breakers.
    """
    settable_with_domain = declaration.settable and bool(declaration.format)
    if declaration.datatype == BOOLEAN_DATATYPE:
        return Platform.SWITCH if declaration.settable else Platform.BINARY_SENSOR
    if declaration.datatype == ENUM_DATATYPE and settable_with_domain:
        return Platform.SELECT
    if declaration.datatype in NUMERIC_DATATYPES and settable_with_domain:
        return Platform.NUMBER
    return Platform.SENSOR


CONTROL_PLATFORMS: Final = frozenset({Platform.SWITCH, Platform.SELECT, Platform.NUMBER})
"""The platforms `classify` names that write back to the panel.

Built, since 2026-08-20. The write goes through `set_adopted_property`, whose
authorisation is a snapshot lookup rather than its arguments: it resolves the
property against the current `adopted_devices` and publishes to the topic that
property carries. A device the adapter models produces no adopted record, so it
cannot be addressed that way however the arguments are spelled -- which is what
keeps this from becoming a generic write around `set_circuit_relay`,
`set_circuit_priority` and `set_evse_charge_limit`.

That mattered: two of those do real work on the way out. The islanding assertion
translates its value, and the charge ceiling refuses one above what the charger
was commissioned for.
"""


def adopted_anchor(device: AdoptedDevice) -> str:
    """Return the identity this device would be adopted under, before any freezing.

    The serial when the device publishes one, because the specification is
    explicit that consumers correlate representations of a physical device by
    `info/serial-number` and never by device id -- ids are opaque, and a proxied
    id is `{proxier-id}-{proxied-id}`, so the same hardware carries different
    ids under different enclosures by design.

    The wire id otherwise, as this panel's local handle.

    This is only the *candidate*. What an install actually uses is frozen at
    first sighting; see `resolve_identifier`.
    """
    return device.serial_number or device.device_id


def adopted_identifier(panel_serial: str, anchor: str) -> str:
    """Return a registry identifier for one adopted device."""
    return f"{panel_serial}_{ADOPTED_IDENTIFIER_TOKEN}_{anchor}"


def resolve_identifier(registry: DeviceRegistry, panel_serial: str, device: AdoptedDevice) -> str:
    """Return the identifier this install already uses for this device, or a new one.

    **An adopted device freezes its identity anchor at first sighting.** Both
    candidate spellings are looked up before either is minted, because both
    drift in practice and each covers the other's case:

    - a serial arriving *after* adoption would move the device from its wire id
      onto the serial, and
    - a producer that derives its wire id from a serial moves the id itself when
      the serial appears -- which is why this repository holds PV's
      `info/serial-number` unvalued, since publishing it moves the PV device
      from `<panel>-pv-1` to `<panel>-<serial>`.

    Either move is a device *replacement* to the registry, taking the device's
    entities and their history with it. Whatever was seen first is what is kept;
    a better anchor arriving later is recorded on the card and changes nothing.

    The registry is the memory, so this needs no new persistence: a device that
    exists was adopted before, and one that does not is being adopted now.
    """
    for candidate in (device.device_id, device.serial_number):
        if candidate is None:
            continue
        identifier = adopted_identifier(panel_serial, candidate)
        if registry.async_get_device(identifiers={(DOMAIN, identifier)}) is not None:
            return identifier
    return adopted_identifier(panel_serial, adopted_anchor(device))


def adopted_device_info(
    identifier: str,
    device: AdoptedDevice,
    *,
    panel_device_id: str,
) -> DeviceInfo:
    """Device card for an adopted device, from its `info` node.

    The same reading `bess_device_info` has done since v1.0, applied to a device
    nobody modelled. `info` describes the thing rather than reporting a reading,
    so it lands here and never as entities -- a panel publishing its own build
    metadata should not arrive as a handful of string sensors.

    `name` falls back to the wire vocabulary because there is nothing better: an
    adopted device has no translation key until somebody curates it, which is
    the main thing curating it is for.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        name=device.name or _humanised(device.device_type.rsplit(".", 1)[-1]),
        manufacturer=device.vendor_name or "Unknown",
        model=device.model or _humanised(device.device_type.rsplit(".", 1)[-1]),
        serial_number=device.serial_number,
        sw_version=device.software_version,
        hw_version=device.hardware_version,
        via_device_id=panel_device_id,
    )


def _humanised(wire_token: str) -> str:
    """`backup-generator` -> `Backup Generator`, for a name with no translation.

    Deliberately plain. An adopted entity renders from wire vocabulary until it
    is promoted, and dressing that up would disguise which entities are curated
    and which are waiting to be.
    """
    return wire_token.replace("-", " ").replace("_", " ").title()


class AdoptedEntity(SpanPanelEntity):
    """Base for an entity built from a declaration rather than from a description.

    Disabled and diagnostic without exception. Adoption's job is to make a device
    reachable, not to put it on somebody's dashboard: the user decides what is
    worth enabling, having seen the device exists.
    """

    _attr_entity_registry_enabled_default = False
    _attr_entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        identifier: str,
        device: AdoptedDevice,
        declaration: AdoptedProperty,
        *,
        panel_device_id: str,
    ) -> None:
        """Bind this entity to one property of one adopted device."""
        super().__init__(coordinator)
        self._device_wire_id = device.device_id
        self._declaration_path = declaration.path
        self._attr_unique_id = (
            f"span_{identifier}_{declaration.node_id}_{declaration.property_id}".replace("-", "_")
        )
        self._attr_name = _humanised(declaration.property_id)
        self._attr_device_info = adopted_device_info(
            identifier, device, panel_device_id=panel_device_id
        )

    def _published(self) -> str | None:
        """Return this property's current value, or None when the panel publishes none.

        Read back out of the snapshot each time rather than captured at
        construction: the device is matched by its wire id, so a device that
        leaves the tree and returns keeps reporting through the same entity.
        """
        snapshot: SpanPanelSnapshot = self.coordinator.data
        for device in snapshot.adopted_devices:
            if device.device_id != self._device_wire_id:
                continue
            for declaration in device.properties:
                if declaration.path == self._declaration_path:
                    return declaration.value
        return None


class AdoptedSensor(AdoptedEntity, SensorEntity):
    """A reading from an adopted device, with no `state_class`."""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        identifier: str,
        device: AdoptedDevice,
        declaration: AdoptedProperty,
        *,
        panel_device_id: str,
    ) -> None:
        """Take the unit and device class from what the panel declared."""
        super().__init__(
            coordinator, identifier, device, declaration, panel_device_id=panel_device_id
        )
        self.entity_description = SensorEntityDescription(
            key=declaration.path,
            device_class=DEVICE_CLASS_BY_UNIT.get(declaration.unit or ""),
            native_unit_of_measurement=declaration.unit,
        )
        self._attr_name = _humanised(declaration.property_id)

    @property
    def native_value(self) -> str | float | None:
        """Return the published value, parsed to a number only where one is declared.

        A declared numeric that arrives unparseable is reported as `None` rather
        than as its raw text: the entity has a unit and a device class, and
        putting a string behind those would be a worse lie than reporting
        nothing.
        """
        raw = self._published()
        if raw is None:
            return None
        if self.entity_description.native_unit_of_measurement is None:
            return raw
        try:
            return float(raw)
        except ValueError:
            _LOGGER.debug(
                "Adopted %s published %r, which is not a number", self._declaration_path, raw
            )
            return None


class AdoptedBinarySensor(AdoptedEntity, BinarySensorEntity):
    """A declared `boolean` from an adopted device that the panel does not accept writes to."""

    @property
    def is_on(self) -> bool | None:
        """Homie spells a boolean `true`/`false`; anything else is not an answer."""
        return _boolean(self._published())


class AdoptedControl(AdoptedEntity):
    """Base for an adopted entity that writes back to the panel.

    The write is refused by the library unless the property is still there and
    still settable, so nothing here re-checks it: a control for a device that has
    left the tree raises rather than publishing into a topic nothing subscribes
    to, and that is the correct outcome to surface.
    """

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        identifier: str,
        device: AdoptedDevice,
        declaration: AdoptedProperty,
        *,
        panel_device_id: str,
    ) -> None:
        """Remember the wire address this control publishes to."""
        super().__init__(
            coordinator, identifier, device, declaration, panel_device_id=panel_device_id
        )
        self._node_id = declaration.node_id
        self._property_id = declaration.property_id

    async def _publish(self, value: str) -> None:
        """Write one value and refresh, or raise what the library raised.

        No `hasattr` guard, unlike the curated controls. Those ask because a
        transport may not implement an optional protocol at all; this entity only
        exists because a v1.0 tree reported an adopted device, and that is the
        same transport that carries the write.
        """
        await self.coordinator.client.set_adopted_property(
            self._device_wire_id, self._node_id, self._property_id, value
        )
        await self.coordinator.async_request_refresh()


class AdoptedSwitch(AdoptedControl, SwitchEntity):
    """A declared `boolean` the panel accepts writes to."""

    @property
    def is_on(self) -> bool | None:
        """Homie spells a boolean `true`/`false`; anything else is not an answer."""
        return _boolean(self._published())

    async def async_turn_on(self, **kwargs: object) -> None:
        """Publish the vocabulary Homie defines for a boolean, not HA's."""
        await self._publish("true")

    async def async_turn_off(self, **kwargs: object) -> None:
        """Publish the vocabulary Homie defines for a boolean, not HA's."""
        await self._publish("false")


class AdoptedSelect(AdoptedControl, SelectEntity):
    """A declared `enum` the panel accepts writes to, with its declared options."""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        identifier: str,
        device: AdoptedDevice,
        declaration: AdoptedProperty,
        *,
        panel_device_id: str,
    ) -> None:
        """Take the option list from the declaration, which is the whole domain."""
        super().__init__(
            coordinator, identifier, device, declaration, panel_device_id=panel_device_id
        )
        self._attr_options = parse_enum_format(declaration.format)

    @property
    def current_option(self) -> str | None:
        """The published value, but only when it is one of the declared options.

        A value outside the declared set is reported as unknown rather than as a
        selection. Home Assistant rejects a `current_option` outside `options`,
        and quietly widening the list to admit whatever arrived would hide a
        panel disagreeing with its own declaration.
        """
        published = self._published()
        return published if published in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        """Publish the option verbatim -- it came from the panel's own list."""
        await self._publish(option)


class AdoptedNumber(AdoptedControl, NumberEntity):
    """A declared numeric the panel accepts writes to, with its declared bounds."""

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        identifier: str,
        device: AdoptedDevice,
        declaration: AdoptedProperty,
        *,
        panel_device_id: str,
    ) -> None:
        """Take the bounds from the declaration, which is what makes this a number."""
        bounds = parse_number_format(declaration.format)
        super().__init__(
            coordinator, identifier, device, declaration, panel_device_id=panel_device_id
        )
        self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = bounds
        self._attr_native_unit_of_measurement = declaration.unit
        self._integral = declaration.datatype == "integer"

    @property
    def native_value(self) -> float | None:
        """The published value as a number, or None when it is not one."""
        published = self._published()
        if published is None:
            return None
        try:
            return float(published)
        except ValueError:
            _LOGGER.debug(
                "Adopted %s published %r, which is not a number", self._declaration_path, published
            )
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Publish the value in the datatype the property declares.

        An `integer` property gets an integer literal. Publishing `5.0` where the
        declaration says `integer` is a payload outside the declared datatype,
        and this library has no business sending one.
        """
        await self._publish(str(int(value)) if self._integral else str(value))


def _boolean(published: str | None) -> bool | None:
    """Return Homie's `true`/`false`, with anything else meaning no answer."""
    if published is None:
        return None
    lowered = published.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return None


def parse_enum_format(declared: str | None) -> list[str]:
    """Return the options a Homie `enum` `$format` lists.

    Comma-separated, per Homie 5. An empty result means the declaration carried
    no usable domain, which `classify` has already used to route the property to
    a sensor -- so this never returns empty for a property that reached a select.
    """
    if not declared:
        return []
    return [option.strip() for option in declared.split(",") if option.strip()]


def parse_number_format(declared: str | None) -> tuple[float, float, float]:
    """Return the `min:max:step` a Homie numeric `$format` states.

    Step defaults to 1 when the declaration gives only a range, which Homie
    permits. `classify` has already required a format, so the two bounds are
    present by the time this is called.
    """
    parts = (declared or "").split(":")
    minimum = float(parts[0]) if len(parts) > 0 and parts[0] else 0.0
    maximum = float(parts[1]) if len(parts) > 1 and parts[1] else 100.0
    step = float(parts[2]) if len(parts) > 2 and parts[2] else 1.0
    return minimum, maximum, step


def adopted_control_count(snapshot: SpanPanelSnapshot) -> int:
    """How many adopted properties this panel exposes as controls rather than readings.

    Reported in diagnostics beside the device list. A control on a device nobody
    modelled is the highest-consequence thing adoption creates, so the count is
    worth having in the one artefact that reaches a maintainer.
    """
    return sum(
        1
        for device in snapshot.adopted_devices
        for declaration in device.properties
        if classify(declaration) in CONTROL_PLATFORMS
    )


def create_adopted_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    registry: DeviceRegistry,
    *,
    panel_device_id: str,
) -> list[AdoptedSensor]:
    """Every adopted property that is not a declared boolean.

    Everything `classify` routes to `SENSOR`: every property that is not a
    declared boolean and not a settable one with a usable value domain.
    """
    return _create(
        AdoptedSensor,
        coordinator,
        snapshot,
        registry,
        Platform.SENSOR,
        panel_device_id=panel_device_id,
    )


def create_adopted_binary_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    registry: DeviceRegistry,
    *,
    panel_device_id: str,
) -> list[AdoptedBinarySensor]:
    """Every adopted property declared `boolean` that the panel accepts no write to."""
    return _create(
        AdoptedBinarySensor,
        coordinator,
        snapshot,
        registry,
        Platform.BINARY_SENSOR,
        panel_device_id=panel_device_id,
    )


def create_adopted_switches(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    registry: DeviceRegistry,
    *,
    panel_device_id: str,
) -> list[AdoptedSwitch]:
    """Every adopted property declared `boolean` and settable."""
    return _create(
        AdoptedSwitch,
        coordinator,
        snapshot,
        registry,
        Platform.SWITCH,
        panel_device_id=panel_device_id,
    )


def create_adopted_selects(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    registry: DeviceRegistry,
    *,
    panel_device_id: str,
) -> list[AdoptedSelect]:
    """Every adopted `enum` that is settable and declares its option list."""
    return _create(
        AdoptedSelect,
        coordinator,
        snapshot,
        registry,
        Platform.SELECT,
        panel_device_id=panel_device_id,
    )


def create_adopted_numbers(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    registry: DeviceRegistry,
    *,
    panel_device_id: str,
) -> list[AdoptedNumber]:
    """Every adopted numeric that is settable and declares its bounds."""
    return _create(
        AdoptedNumber,
        coordinator,
        snapshot,
        registry,
        Platform.NUMBER,
        panel_device_id=panel_device_id,
    )


def _create[AdoptedT: AdoptedEntity](
    entity_class: type[AdoptedT],
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    registry: DeviceRegistry,
    platform: Platform,
    *,
    panel_device_id: str,
) -> list[AdoptedT]:
    """Build one platform's share of the adopted properties.

    One partition function rather than five bodies, so `classify` stays the only
    place a property's platform is decided. Five bodies would each restate the
    predicate, and a property could then reach two platforms or none.
    """
    return [
        entity_class(coordinator, identifier, device, declaration, panel_device_id=panel_device_id)
        for device, identifier in _adopted(snapshot, registry)
        for declaration in device.properties
        if classify(declaration) is platform
    ]


def _adopted(
    snapshot: SpanPanelSnapshot, registry: DeviceRegistry
) -> list[tuple[AdoptedDevice, str]]:
    """Each adopted device paired with the identifier this install uses for it."""
    return [
        (device, resolve_identifier(registry, snapshot.serial_number, device))
        for device in snapshot.adopted_devices
    ]
