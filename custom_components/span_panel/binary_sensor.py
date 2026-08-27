"""Binary Sensors for status entities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from types import MappingProxyType
from typing import Any, ClassVar

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import SpanEvseSnapshot, SpanPanelSnapshot

from .adoption import create_adopted_binary_sensors
from .const import (
    CONF_DEVICE_NAME,
    PANEL_STATUS,
    SYSTEM_DOOR_STATE,
    SYSTEM_DOOR_STATE_CLOSED,
    SYSTEM_DOOR_STATE_OPEN,
    SYSTEM_ETHERNET_LINK,
    SYSTEM_WIFI_LINK,
    USE_CIRCUIT_NUMBERS,
)
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .extension import create_extension_binary_sensors
from .field_paths import DerivedReason, FieldPathDeclarationMixin
from .helpers import (
    build_binary_sensor_unique_id_for_entry,
    build_evse_unique_id_for_entry,
    has_bess,
    has_mid,
    has_pcs,
    resolve_evse_display_suffix,
)
from .runtime import SpanPanelConfigEntry
from .util import EMPTY_EVSE, bess_device_info, evse_device_info, pv_device_info

# pylint: disable=invalid-overridden-method


_LOGGER: logging.Logger = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


@dataclass(frozen=True)
class SpanPanelRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel binary sensors."""

    value_fn: Callable[[SpanPanelSnapshot], bool | None]


@dataclass(frozen=True, kw_only=True)
class SpanPanelBinarySensorEntityDescription(
    BinarySensorEntityDescription, SpanPanelRequiredKeysMixin
):
    """Describes an SpanPanelCircuits sensor entity."""


# Door state has been observed to return UNKNOWN if the door
# has not been operated recently so we check for invalid values
# pylint: disable=unexpected-keyword-arg
BINARY_SENSORS: tuple[
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
    SpanPanelBinarySensorEntityDescription,
] = (
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_DOOR_STATE,
        field_path="panel.door_state",
        translation_key="door_state",
        device_class=BinarySensorDeviceClass.TAMPER,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: (
            None
            if s.door_state not in [SYSTEM_DOOR_STATE_CLOSED, SYSTEM_DOOR_STATE_OPEN]
            else s.door_state != SYSTEM_DOOR_STATE_CLOSED
        ),
    ),
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_ETHERNET_LINK,
        field_path="panel.eth0_link",
        translation_key="ethernet_link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.eth0_link,
    ),
    SpanPanelBinarySensorEntityDescription(
        key=SYSTEM_WIFI_LINK,
        field_path="panel.wlan_link",
        translation_key="wifi_link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.wlan_link,
    ),
    SpanPanelBinarySensorEntityDescription(
        key=PANEL_STATUS,
        # Reports coordinator reachability, not a snapshot field — the value_fn
        # is a placeholder the entity class overrides.
        derived=DerivedReason.NO_SOURCE_FIELD,
        translation_key="panel_status",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: True,  # Placeholder - actual logic handled in sensor class
    ),
)


def _grid_islandable(snapshot: SpanPanelSnapshot) -> bool | None:
    """Whether this panel can island, across both schema generations.

    Flat publishes `core/grid-islandable` outright. v1.0 has no such property and
    that is deliberate -- `devices/bess.md` says a consumer "distinguishes the
    variants without any dedicated type property: a MID `grid` child means
    premises-segment backup ... neither means no backup", and "there is no single
    'islanded?' bit to reconcile".

    So under v1.0 the answer is read from the classifier the spec nominates rather
    than from a property that no longer exists. That is a consumer deriving a
    convenience from what is published, not a producer inventing a claim -- the
    panel still says exactly what the spec says it should.

    Keeping the entity alive matters because the alternative is what a firmware
    upgrade did in practice: it went `unavailable` with `restored: true`, which
    reaches a user as a sensor that broke rather than one whose source moved. The
    deprecation is announced separately, as a repair issue.

    DUAL-SCHEMA: when the flat path retires this becomes `has_mid(snapshot)` and the
    first branch goes.
    """
    if snapshot.grid_islandable is not None:
        return snapshot.grid_islandable
    return has_mid(snapshot)


GRID_ISLANDABLE_SENSOR = SpanPanelBinarySensorEntityDescription(
    key="grid_islandable",
    field_path="panel.grid_islandable",
    derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
    translation_key="grid_islandable",
    device_class=BinarySensorDeviceClass.POWER,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=_grid_islandable,
)

BESS_CONNECTED_SENSOR = SpanPanelBinarySensorEntityDescription(
    key="bess_connected",
    field_path="battery.connected",
    derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
    translation_key="bess_connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda s: s.battery.connected,
)

PV_PANEL_LINK_SENSOR = SpanPanelBinarySensorEntityDescription(
    key="pv_panel_link",
    field_path="pv.connected",
    derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
    translation_key="pv_panel_link",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda s: s.pv.connected,
)
"""Can the enclosure talk to the solar inverter?

`bess_connected`'s counterpart for the other DER the panel feeds through a
circuit. The BESS got one because the upstream lugs' `connection/fed-by-*`
record was already read; the PV's and the charger's live on the *circuit* that
feeds them, as `connection` 0.1 specifies, and nothing read that half — so the
one device class whose link the panel happened to report through the lugs was
the only one a user could see.

On the inverter's own sub-device, beside `pv_vendor` and `pv_product`, which is
where it moved when the PV got a device of its own -- the same place
`bess_connected` sits relative to the battery.

`SCHEMA_CONDITIONAL_FIELD` *and* `field_path`: flat firmware publishes
`connected` on the BESS and on nothing else, so the both-adapters gate cannot be
satisfied, while the entity still needs its Repair mention and its
unavailability when the panel stops resolving the property.
"""


PCS_ACTIVE_SENSOR = SpanPanelBinarySensorEntityDescription(
    key="pcs_active",
    field_path="pcs.active",
    derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
    translation_key="pcs_active",
    device_class=BinarySensorDeviceClass.RUNNING,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda s: None if s.pcs is None else s.pcs.active,
)
"""Is the Power Control System limiting import right now?

The one property of this capability that changes on its own, and therefore the
one an automation triggers on: `pcs/enabled` is a commissioning fact and the four
constraint limits move only on reconfiguration, but `active` flips when the panel
starts throttling. A binary sensor rather than a third enum, because the question
is binary and `pcs_binding_constraint` already answers "which limit" for anyone
who needs it.

Diagnostic, and enabled by default. It reports the panel constraining the user's
supply, which is worth seeing, but it belongs beside the other panel-state
sensors rather than among the power readings.

`None` when the panel runs no PCS, which is what a flat panel and any v1.0
firmware without the node report — but the entity is not created there at all, so
the branch is reached only if the node disappears mid-session, where unknown is
the right answer.

`SCHEMA_CONDITIONAL_FIELD` *and* `field_path`, by the producible rule: flat
declares no `pcs` capability, so the both-adapters gate cannot be satisfied,
while the entity still needs its Repair mention and its unavailability when the
panel stops resolving the property.
"""


_HARDWARE_STATUS_SENSORS: frozenset[str] = frozenset(
    {SYSTEM_DOOR_STATE, SYSTEM_ETHERNET_LINK, SYSTEM_WIFI_LINK}
)
"""The sensors that stay available while the panel is offline, reading unknown.

Read by `available` and by `_handle_coordinator_update`, which is why it is named
once here: the two have to agree, and a set restated in each is a set that can be
extended in one and not the other -- leaving a sensor that reports unknown while
offline but drops out of the registry's availability, or the reverse.
"""


class SpanPanelBinarySensor[T: SpanPanelBinarySensorEntityDescription](
    SpanPanelEntity, BinarySensorEntity
):
    """Binary Sensor status entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        device_info_override: DeviceInfo | None = None,
    ) -> None:
        """Initialize Span Panel Circuit entity."""
        super().__init__(data_coordinator, context=description)
        snapshot: SpanPanelSnapshot = data_coordinator.data

        self.entity_description = description
        self._attr_device_class = description.device_class
        self._value_fn = description.value_fn

        self._device_name = data_coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, data_coordinator.config_entry.title
        )

        if device_info_override is not None:
            self._attr_device_info = device_info_override
        else:
            self._attr_device_info = self._build_device_info(data_coordinator, snapshot)

        self._attr_unique_id = self._construct_binary_sensor_unique_id(
            data_coordinator, snapshot, description.key
        )

    @property
    def available(self) -> bool:
        """Return entity availability.

        - Panel status sensor: always available to show online/offline state
        - Hardware status sensors: remain available when offline to show Unknown state
        - Other binary sensors (switches): become unavailable when panel is offline

        The unresolved-field probe runs ahead of all of that, for the same
        reason it precedes the grace-period branch in `SpanSensorBase`: the
        offline branch below returns True, so a probe after it would leave
        `door_state`, `eth0_link` and `wlan_link` reporting a field the adapter
        cannot resolve. `panel_status` and the derived sensors declare no
        `field_path`, so the probe never fires for them.

        `panel_status` is the one entity a dead transport does not take with
        it, and the exception is the same one that already exempts it from the
        offline branch: it reports reachability, so it has to survive the
        condition it exists to report. It reads `off` rather than holding a
        stale `on` -- see `_handle_coordinator_update`. Every other binary
        sensor here reads a snapshot field, and holding one from before the
        transport died is exactly what the transport probe is for.
        """
        if self._reads_an_unresolved_field:
            return False

        # Panel status sensor should always be available to show online/offline state
        if hasattr(self.entity_description, "key") and self.entity_description.key == PANEL_STATUS:
            return True

        if not self._transport_available:
            return False

        # Hardware status sensors should remain available when offline to show Unknown
        if (
            hasattr(self.entity_description, "key")
            and self.entity_description.key in _HARDWARE_STATUS_SENSORS
        ):
            if getattr(self.coordinator, "panel_offline", False):
                return True

        if getattr(self, "_attr_available", True) is False:
            return False

        return super().available

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Special handling for panel_status sensor
        if hasattr(self.entity_description, "key") and self.entity_description.key == PANEL_STATUS:
            # Both flags, because both mean "no panel at the other end". A dead
            # transport is deliberately not marked offline -- that flag makes
            # the other entities hold their last reading -- and reading it here
            # is what stops the one connectivity sensor in the integration from
            # reporting connected while the connection is gone for good.
            self._attr_is_on = not (
                self.coordinator.panel_offline or self.coordinator.transport_dead
            )
            self._attr_available = True
            super()._handle_coordinator_update()
            return

        # Check for panel offline status first to prevent accessing None data
        if self.coordinator.panel_offline or self.coordinator.data is None:
            if (
                hasattr(self.entity_description, "key")
                and self.entity_description.key in _HARDWARE_STATUS_SENSORS
            ):
                self._attr_is_on = None
                self._attr_available = True
                _LOGGER.debug(
                    "Hardware status sensor %s: panel offline or no data - showing as unknown",
                    self.entity_id,
                )
            else:
                self._attr_available = False
                _LOGGER.debug(
                    "Binary sensor %s: panel offline or no data - will be unavailable",
                    self.entity_id,
                )

            super()._handle_coordinator_update()
            return

        # Panel is online and data is available — snapshot provides status fields directly
        snapshot = self.coordinator.data
        status_value = self._value_fn(snapshot)

        self._attr_is_on = status_value
        # None means the panel reported an indeterminate state (e.g. door=UNKNOWN),
        # not that the entity is broken — keep available so HA shows "unknown".
        self._attr_available = True

        super()._handle_coordinator_update()

    def _construct_binary_sensor_unique_id(
        self,
        data_coordinator: SpanPanelCoordinator,
        snapshot: SpanPanelSnapshot,
        description_key: str,
    ) -> str:
        """Construct unique ID for binary sensor entities."""
        return build_binary_sensor_unique_id_for_entry(
            data_coordinator, snapshot, description_key, self._device_name
        )


class SpanPanelWifiLinkBinarySensor(SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription]):
    """The Wi-Fi link, which also reports the network the link is to.

    Its own class, not a branch on `SpanPanelBinarySensor`, because
    `_residual_field_paths` is a `ClassVar` and that base class serves every
    panel binary sensor — the door, the two links, the panel status, the PCS
    activity, the PV link. Declaring the SSID there would claim all of them read
    it, and the declaration is not decoration: it is what a Repair consults to
    name the entities a dead field took down with it, so an unresolved
    `panel.wifi_ssid` would name the door sensor. `SpanPanelStatus` is its own
    class in `sensor_panel` for exactly this reason.
    """

    _residual_field_paths: ClassVar[tuple[str, ...]] = ("panel.wifi_ssid",)
    """The SSID, read for an attribute rather than by the `value_fn`.

    A plain residual and not an exemption: both adapters map the property
    (`core/wifi-ssid` on flat, `status/wifi-ssid` on v1.0), so the producible
    gate covers it.

    The only declaration of this path in the integration. It was on
    `SpanPanelStatus` while that sensor rendered the SSID, and it moved with the
    read rather than being left behind: the declaration is what a Repair
    consults to name the entity a dead field took down with it, so a stale copy
    would name a sensor that no longer reads the field. Because this is now the
    only declaration, `residual_field_paths()` has to import `binary_sensor` for
    the subclass walk to see it -- the walk sees only imported modules, and this
    path would otherwise leave the producible gate silently.
    """

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The network this link is up on, when the panel names it.

        The coherent host for the SSID: the entity that reports whether Wi-Fi is
        up is the one that should report which network it is up on. Both values
        come from the same node on the wire — `status/wifi` and
        `status/wifi-ssid` on v1.0, `core/wifi` and `core/wifi-ssid` on flat.

        The Software Version sensor used to publish it, for the historical
        reason that `panel_size` was already occupying its attribute block. That
        copy is gone -- see `SpanPanelStatus.extra_state_attributes` for why the
        compatibility argument for keeping one did not hold up.

        Omitted rather than reported as `None` when the panel publishes no SSID:
        an attribute present and empty reads as a reading that failed, which is
        a different claim from the panel never having made one.
        """
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.wifi_ssid is None:
            return None
        return {"wifi_ssid": snapshot.wifi_ssid}


_PANEL_BINARY_SENSOR_CLASSES: Mapping[
    str, type[SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription]]
] = MappingProxyType({SYSTEM_WIFI_LINK: SpanPanelWifiLinkBinarySensor})
"""Panel binary sensors needing a class of their own, by description key.

Everything absent from this map is a plain `SpanPanelBinarySensor`. A named map
rather than a conditional inside the setup comprehension: the comprehension says
"build one entity per description" and should keep saying only that, and the
next description that needs its own class is then a one-line addition here
rather than a second branch to read past.
"""


def _panel_binary_sensor_class(
    description: SpanPanelBinarySensorEntityDescription,
) -> type[SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription]]:
    """Return the entity class that serves one panel binary sensor description."""
    return _PANEL_BINARY_SENSOR_CLASSES.get(description.key, SpanPanelBinarySensor)


# ---------------------------------------------------------------------------
# EVSE (EV Charger) binary sensors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanEvseBinarySensorRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for EVSE binary sensors."""

    value_fn: Callable[[SpanEvseSnapshot], bool | None]


@dataclass(frozen=True, kw_only=True)
class SpanEvseBinarySensorEntityDescription(
    BinarySensorEntityDescription, SpanEvseBinarySensorRequiredKeysMixin
):
    """Describes an EVSE binary sensor entity."""


_EV_CONNECTED_STATUSES: frozenset[str] = frozenset(
    {"PREPARING", "CHARGING", "SUSPENDED_EV", "SUSPENDED_EVSE", "FINISHING"}
)

EVSE_BINARY_SENSORS: tuple[
    SpanEvseBinarySensorEntityDescription,
    SpanEvseBinarySensorEntityDescription,
] = (
    SpanEvseBinarySensorEntityDescription(
        key="evse_charging",
        field_path="evse.status",
        translation_key="evse_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda e: (e.status or "") == "CHARGING",
    ),
    SpanEvseBinarySensorEntityDescription(
        key="evse_ev_connected",
        field_path="evse.status",
        translation_key="evse_ev_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda e: (e.status or "") in _EV_CONNECTED_STATUSES,
    ),
)

EVSE_PANEL_LINK_SENSOR = SpanEvseBinarySensorEntityDescription(
    key="evse_panel_link",
    field_path="evse.connected",
    derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
    translation_key="evse_panel_link",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda e: e.connected,
)
"""Can the enclosure talk to this charger?

**Not `evse_ev_connected`, which sits two definitions above it.** That one reads
the charger's own `status/status` and answers "is a vehicle plugged in" — a
`PLUG` device class, enabled by default, the fact a user builds a charging
automation on. This reads the *feeding circuit's* `connection/feeds-device-status`
and answers "can the panel reach the charger at all" — a `CONNECTIVITY` device
class, diagnostic. The two disagree exactly when it matters: a charger
mid-session over a lost link reports a plugged-in vehicle and a dead link at the
same time, because the last session state the panel heard is still the last
session state the panel heard.

Deliberately outside `EVSE_BINARY_SENSORS`, which is the unconditional pair.
This one is created per charger and only where the record exists, following
`bess_connected` and `pcs_active` rather than its two neighbours.
"""


class SpanEvseBinarySensor(SpanPanelEntity, BinarySensorEntity):
    """EVSE (EV charger) binary sensor entity."""

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: SpanEvseBinarySensorEntityDescription,
        evse_id: str,
    ) -> None:
        """Initialize EVSE binary sensor."""
        super().__init__(data_coordinator, context=description)
        snapshot: SpanPanelSnapshot = data_coordinator.data
        self._evse_id = evse_id
        self.entity_description = description
        self._attr_device_class = description.device_class
        self._value_fn = description.value_fn

        # Build EVSE sub-device info
        panel_name = (
            data_coordinator.config_entry.data.get(
                CONF_DEVICE_NAME, data_coordinator.config_entry.title
            )
            or "Span Panel"
        )
        panel_identifier = snapshot.serial_number

        evse = snapshot.evse.get(evse_id, EMPTY_EVSE)
        use_circuit_numbers = data_coordinator.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
        display_suffix = resolve_evse_display_suffix(evse, snapshot, use_circuit_numbers)
        self._attr_device_info = evse_device_info(
            panel_identifier,
            evse,
            panel_name,
            display_suffix,
            panel_device_id=data_coordinator.config_entry.runtime_data.panel_device_id,
        )

        device_name = data_coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, data_coordinator.config_entry.title
        )
        self._attr_unique_id = build_evse_unique_id_for_entry(
            data_coordinator, snapshot, evse_id, description.key, device_name
        )

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.panel_offline or self.coordinator.data is None:
            self._attr_is_on = None
            super()._handle_coordinator_update()
            return

        snapshot = self.coordinator.data
        evse = snapshot.evse.get(self._evse_id, EMPTY_EVSE)
        self._attr_is_on = self._value_fn(evse)
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up status sensor platform."""

    _LOGGER.debug("ASYNC SETUP ENTRY BINARYSENSOR")

    coordinator = config_entry.runtime_data.coordinator

    entities: list[
        SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription] | SpanEvseBinarySensor
    ] = [
        _panel_binary_sensor_class(description)(coordinator, description)
        for description in BINARY_SENSORS
    ]

    snapshot: SpanPanelSnapshot = coordinator.data

    # Created unconditionally, because on both generations the answer is knowable.
    #
    # This gate has been narrowed twice for the same symptom. Gating on the flat
    # property alone left the entity `unavailable` after a v1.0 upgrade -- the
    # panel had not lost the capability, only the property that used to report
    # it. Adding `has_mid` rescued panels that have a battery and left every
    # battery-less one exactly where it was, because it admitted the entity only
    # when the answer was going to be `True`. For a boolean whose `False` is
    # informative that is backwards: no MID is not missing information, it is the
    # information. `devices/bess.md` makes the signal structural -- "a MID `grid`
    # child means premises-segment backup ... neither means no backup" -- so a
    # panel without one does not island, and saying so is the answer rather than
    # the absence of one.
    #
    # Nothing about flat changes. Its property is published, so `_grid_islandable`
    # returns it exactly as before; and a flat panel that stops publishing it has
    # a metadata row that fails to resolve, which is what makes the entity
    # unavailable and raises the Repair. That path is untouched, and it is the one
    # that must not become a default presented as a reading.
    #
    # DUAL-SCHEMA: nothing to remove here when the flat path retires -- an
    # unconditional append is already the end state. The branch that goes is the
    # first one in `_grid_islandable`, which is where the flat property is read.
    entities.append(SpanPanelBinarySensor(coordinator, GRID_ISLANDABLE_SENSOR))

    # Add BESS connected sensor on the BESS sub-device when battery is commissioned
    if has_bess(snapshot):
        panel_name = (
            coordinator.config_entry.data.get(CONF_DEVICE_NAME, coordinator.config_entry.title)
            or "Span Panel"
        )

        bess_info = bess_device_info(
            snapshot.serial_number,
            snapshot.battery,
            panel_name,
            panel_device_id=config_entry.runtime_data.panel_device_id,
        )
        entities.append(
            SpanPanelBinarySensor(
                coordinator, BESS_CONNECTED_SENSOR, device_info_override=bess_info
            )
        )

    # Add the PCS activity sensor where the panel runs a Power Control System.
    # Gated on the node, not on a value: every property this capability publishes
    # is legally zero or false, so a value gate would delete the entity of every
    # panel whose PCS is merely switched off — see `has_pcs`.
    if has_pcs(snapshot):
        entities.append(SpanPanelBinarySensor(coordinator, PCS_ACTIVE_SENSOR))

    # The enclosure's view of the link to the solar inverter, where a circuit
    # publishes one. Gated on the record existing and never on what kind of
    # circuit publishes it — `distribution-enclosure.md` makes a mixed-load
    # circuit publishing no `feeds-*` the normal case, so absence is the panel
    # saying it does not know rather than a fault, and the enum it does publish
    # has no UNKNOWN member for it to say that with. See `PV_PANEL_LINK_SENSOR`.
    if snapshot.pv.connected is not None:
        configured_name = coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, coordinator.config_entry.title
        )
        entities.append(
            SpanPanelBinarySensor(
                coordinator,
                PV_PANEL_LINK_SENSOR,
                device_info_override=pv_device_info(
                    snapshot.serial_number,
                    snapshot.pv,
                    configured_name or "Span Panel",
                    panel_device_id=config_entry.runtime_data.panel_device_id,
                ),
            )
        )

    # Add EVSE binary sensors for each commissioned charger
    if snapshot.evse:
        for evse_id, evse in snapshot.evse.items():
            entities.extend(
                SpanEvseBinarySensor(coordinator, evse_desc, evse_id)
                for evse_desc in EVSE_BINARY_SENSORS
            )
            # Per charger, not per panel: two chargers can be fed by two
            # circuits of which only one publishes the record.
            if evse.connected is not None:
                entities.append(SpanEvseBinarySensor(coordinator, EVSE_PANEL_LINK_SENSOR, evse_id))

    # Declared booleans on devices this integration models nothing for. Disabled
    # and diagnostic, so a panel that gains a vendor device gains no dashboard
    # clutter -- only something the user can find and enable.
    async_add_entities(
        [
            *entities,
            *create_adopted_binary_sensors(
                coordinator,
                snapshot,
                dr.async_get(hass),
                panel_device_id=config_entry.runtime_data.panel_device_id,
            ),
            # Vendor extensions on devices this integration *does* model. A
            # separate inventory from adoption's for the same reason adoption is
            # separate from the curated descriptions: different question, and
            # `extension` owns the answer.
            *create_extension_binary_sensors(
                coordinator,
                snapshot,
                dr.async_get(hass),
                er.async_get(hass),
            ),
        ]
    )
