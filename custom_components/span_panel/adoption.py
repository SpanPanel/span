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
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
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
"""Platforms `classify` names that this integration cannot yet construct.

Not a reversal of the rule -- `classify` is the rule and it is complete. A
control needs a write, and every write this integration performs goes through a
curated, adapter-named topic: `set_circuit_relay`, `set_circuit_priority`,
`set_evse_charge_limit`. There is no generic property write, and adding one would
put a new member on `SchemaAdapter`, whose required set is derived from the
protocol itself -- so it would be required of every adapter package and would
invalidate built adapter wheels.

That is a contract change and belongs in its own one, with its own version bump.
Until then a property that classifies as a control surfaces as a reading, and
`adopted_control_count` is what makes the gap countable rather than invisible.
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
        raw = self._published()
        if raw is None:
            return None
        lowered = raw.strip().lower()
        if lowered in ("true", "false"):
            return lowered == "true"
        return None


def adopted_control_count(snapshot: SpanPanelSnapshot) -> int:
    """How many declared properties would be controls if this could build them.

    Zero on every panel that publishes no settable property on an unmodelled
    device, which is every panel seen so far. Non-zero is the signal that the
    generic write path `CONTROL_PLATFORMS` describes is worth its contract bump,
    measured rather than assumed.
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

    Properties that classify as a control land here too, as readings, until the
    write path exists -- see `CONTROL_PLATFORMS`.
    """
    return [
        AdoptedSensor(coordinator, identifier, device, declaration, panel_device_id=panel_device_id)
        for device, identifier in _adopted(snapshot, registry)
        for declaration in device.properties
        if classify(declaration) != Platform.BINARY_SENSOR
    ]


def create_adopted_binary_sensors(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    registry: DeviceRegistry,
    *,
    panel_device_id: str,
) -> list[AdoptedBinarySensor]:
    """Every adopted property declared `boolean` and not settable."""
    return [
        AdoptedBinarySensor(
            coordinator, identifier, device, declaration, panel_device_id=panel_device_id
        )
        for device, identifier in _adopted(snapshot, registry)
        for declaration in device.properties
        if classify(declaration) == Platform.BINARY_SENSOR
    ]


def _adopted(
    snapshot: SpanPanelSnapshot, registry: DeviceRegistry
) -> list[tuple[AdoptedDevice, str]]:
    """Each adopted device paired with the identifier this install uses for it."""
    return [
        (device, resolve_identifier(registry, snapshot.serial_number, device))
        for device in snapshot.adopted_devices
    ]
