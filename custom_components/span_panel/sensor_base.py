"""Base sensor classes for Span Panel integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import StateType
from span_panel_api import SpanPanelSnapshot

from .const import DOMAIN, ENABLE_ENERGY_DIP_COMPENSATION
from .coordinator import SpanPanelCoordinator
from .energy_dip import (
    DipEvent,
    DipOutcome,
    PendingDip,
    build_dip_attributes,
    process_energy_dip,
)
from .entity import SpanPanelEntity
from .grace_period import (  # noqa: F401
    SpanEnergyExtraStoredData,
    _parse_numeric_state,
    coerce_grace_period_minutes,
    handle_offline_grace_period,
    initialize_from_last_state,
)
from .naming import (
    circuit_object_id_base,
    release_registry_name_written_by_older_release,
)
from .options import ENERGY_REPORTING_GRACE_PERIOD
from .sensor_definitions import SpanPanelCircuitsSensorEntityDescription

_LOGGER: logging.Logger = logging.getLogger(__name__)

# Sentinel value to distinguish "never synced" from "circuit name is None"
_NAME_UNSET: object = object()

# Keys from Span energy sensors' extra_state_attributes that we omit from the recorder
# (SpanEnergySensorBase: panel-wide and circuit energy entities). High-churn grace/dip
# diagnostics dominated DB growth (#197). tabs and voltage are merged in by circuit
# subclasses; they stay on the live entity for Developer tools and automations.
_ENERGY_SENSOR_UNRECORDED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "energy_offset",
        "grace_period_remaining",
        "last_dip_delta",
        "last_valid_changed",
        "last_valid_state",
        "tabs",
        "using_grace_period",
        "voltage",
    }
)


def _description_label(description: SensorEntityDescription) -> str:
    """Return the description's own label, or a neutral word where it declares none.

    A description with a `translation_key` declares no `name` -- it declares
    `None` or leaves the field `UNDEFINED` -- and a sensor that reaches here with
    neither is a programming error rather than a user-visible state, so "Sensor"
    keeps it readable instead of rendering a sentinel.
    """
    name = description.name
    if isinstance(name, str) and name:
        return name
    return "Sensor"


class SpanSensorBase[T: SensorEntityDescription, D](SpanPanelEntity, SensorEntity, ABC):
    """Abstract base class for Span Panel sensors with overridable methods."""

    _attr_has_entity_name = True

    _is_sub_device: bool = False
    """True when this entity is shown on a sub-device's card rather than the panel's.

    Set by the circuit classes when they are handed a `device_info_override`.
    Such a sensor supplies no base at all: it composes from its label on the
    sub-device's card, which is the shape the sub-device's own sensors have.
    """

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize Span Panel Sensor base entity."""
        super().__init__(data_coordinator, context=description)
        self.entity_description = description

        if hasattr(description, "device_class"):
            self._attr_device_class = description.device_class

        if hasattr(description, "options") and description.options:
            self._attr_options = list(description.options)

        # Get device name from config entry data
        self._device_name = data_coordinator.config_entry.data.get(
            "device_name", data_coordinator.config_entry.title
        )

        self._attr_device_info = self._build_device_info(data_coordinator, snapshot)

        # Check if entity already exists in registry for name sync
        if snapshot.serial_number and description.key:
            self._attr_unique_id = self._generate_unique_id(snapshot, description)

            # Entities with translation_key get their name from translations/en.json.
            # Only set _attr_name for entities without translation_key (e.g.,
            # circuit sensors whose names include the dynamic circuit name).
            if not getattr(description, "translation_key", None):
                entity_registry = er.async_get(data_coordinator.hass)
                existing_entity_id = entity_registry.async_get_entity_id(
                    "sensor", DOMAIN, self._attr_unique_id
                )

                # One name path, in both naming modes: the panel's name, carried
                # as `original_name`. That field ranks below the object-id base
                # below, so what is displayed can no longer decide what
                # "Recreate entity IDs" proposes.
                self._attr_name = self._generate_panel_name(snapshot, description)

                # The id itself is Home Assistant's to compose. This entity
                # supplies only its base; `entity_id` is left unset so Core
                # assembles the rest from the user's `entity_id_parts`. A
                # sub-device sensor supplies no base either, composing from its
                # label on the sub-device's card like that device's own sensors.
                parts = self._object_id_parts(snapshot, description)
                if parts is not None and not self._is_sub_device:
                    identifier, suffix = parts
                    self._span_object_id_base = circuit_object_id_base(
                        identifier, suffix, existing_entity_id
                    )

                if existing_entity_id:
                    self._release_synced_registry_name(
                        snapshot, description, entity_registry, existing_entity_id
                    )
        else:
            # Fallback for entities without unique_id
            self._attr_name = self._generate_panel_name(snapshot, description)

        # Set entity registry defaults if they exist in the description
        if hasattr(description, "entity_registry_enabled_default"):
            self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default
        if hasattr(description, "entity_registry_visible_default"):
            self._attr_entity_registry_visible_default = description.entity_registry_visible_default

        # Initialize name sync tracking
        # Use sentinel to distinguish "never synced" from "circuit name is None"
        if snapshot.serial_number and description.key and self._attr_unique_id:
            entity_registry = er.async_get(data_coordinator.hass)
            existing_entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, self._attr_unique_id
            )
            if not existing_entity_id:
                self._previous_circuit_name: str | None | object = _NAME_UNSET
            # Entity exists, get current circuit name for comparison
            elif hasattr(self, "circuit_id"):
                circuit = snapshot.circuits.get(getattr(self, "circuit_id", ""))
                self._previous_circuit_name = circuit.name if circuit else None
            else:
                self._previous_circuit_name = None
        else:
            self._previous_circuit_name = _NAME_UNSET

        # Use standard coordinator pattern - entities will update automatically
        # when coordinator data changes

    @abstractmethod
    def _generate_unique_id(self, snapshot: SpanPanelSnapshot, description: T) -> str:
        """Generate unique ID for the sensor.

        Subclasses must implement this to define their unique ID strategy.

        Args:
            snapshot: The panel snapshot data
            description: The sensor description

        Returns:
            Unique ID string

        """

    def _object_id_parts(
        self, snapshot: SpanPanelSnapshot, description: T
    ) -> tuple[str, str] | None:
        """Return the identifier and canonical suffix this entity's id is built from.

        `None` -- the default, and the answer for every entity that is not a
        circuit -- leaves `_span_object_id_base` unset, so Home Assistant
        composes the id from the display name exactly as it always has.

        A circuit entity answers with the naming-flag half (`Circuit 15`,
        `Kitchen Outlets`, `Unmapped Tab 32`) and the suffix
        `naming.circuit_object_id_base` keys on. The two travel together because
        neither is any use alone, and because a circuit entity's
        `description.key` has been overwritten with the circuit id by then, so
        there is no suffix a default could compute.
        """
        return None

    def _generate_panel_name(self, snapshot: SpanPanelSnapshot, description: T) -> str | None:
        """Generate the displayed name for the sensor, in either naming mode.

        Circuit entities override this to prefix the panel's own circuit name,
        which is what makes a circuit renamed in the SPAN app show through. Every
        other sensor's name is its description's label -- and a description that
        declares a `translation_key` instead is normally not asked at all, since
        its name comes from `translations/en.json`.

        Args:
            snapshot: The panel snapshot data
            description: The sensor description

        Returns:
            Panel name string

        """
        return _description_label(description)

    def _release_synced_registry_name(
        self,
        snapshot: SpanPanelSnapshot,
        description: T,
        entity_registry: er.EntityRegistry,
        existing_entity_id: str,
    ) -> None:
        """Give the registry's `name` back to the user, where an older release took it.

        Circuit-numbers mode used to deliver the panel's name by writing the
        registry's `name`. That field is the *user's* override, and Home Assistant
        reads it ahead of `suggested_object_id` when generating an entity id -- so
        occupying it made "Recreate entity IDs" propose a friendly-name id for a
        circuit-numbered entity, converting the whole panel if accepted.

        The name now travels as `original_name` instead, so this only has to let
        go of what the old scheme wrote. Only a name this integration would have
        written is cleared; anything else is the user's and is left exactly where
        it is, which is the same test the write used to gate on.

        Every label the description has carried counts, not just its current one:
        a label reworded between releases left installs holding the older string,
        and a write we no longer recognise is a write we would never hand back.
        """
        circuit = snapshot.circuits.get(getattr(self, "circuit_id", ""))
        if not (circuit and circuit.name):
            return
        # Only the circuit descriptions declare a reworded label, and only a
        # circuit entity reaches this line at all -- the guard above returns for
        # anything with no circuit behind it.
        legacy_names = (
            description.legacy_names
            if isinstance(description, SpanPanelCircuitsSensorEntityDescription)
            else ()
        )
        release_registry_name_written_by_older_release(
            entity_registry,
            existing_entity_id,
            circuit.name,
            (_description_label(description), *legacy_names),
        )

    def _sync_circuit_name(self) -> None:
        """Follow a circuit renamed on the panel, by reloading so the name is rebuilt.

        One path for both modes. The name is carried by `original_name`, which is
        written when the entity is added, so a reload is what refreshes it --
        circuit-numbers mode used to write the registry's `name` in place instead,
        which was quicker but handed that field the last word over entity id
        generation. See `_release_synced_registry_name`.
        """
        if not (hasattr(self, "circuit_id") and hasattr(self.coordinator.data, "circuits")):
            return

        circuit = self.coordinator.data.circuits.get(getattr(self, "circuit_id", ""))
        if not circuit:
            return

        current_circuit_name = circuit.name

        # A name in the registry is one the user set: theirs outranks the panel's,
        # and reloading would not change what is displayed anyway.
        user_has_override = False
        if self.entity_id:
            entity_registry = er.async_get(self.hass)
            entity_entry = entity_registry.async_get(self.entity_id)
            if entity_entry and entity_entry.name:
                user_has_override = True

        if user_has_override:
            self._previous_circuit_name = current_circuit_name
        elif self._previous_circuit_name is _NAME_UNSET:
            _LOGGER.info(
                "First update: syncing sensor name to panel name '%s', requesting reload",
                current_circuit_name,
            )
            self._previous_circuit_name = current_circuit_name
            self.coordinator.request_reload()
        elif current_circuit_name != self._previous_circuit_name:
            _LOGGER.info(
                "Auto-sync detected circuit name change from '%s' to '%s' for sensor, requesting integration reload",
                self._previous_circuit_name,
                current_circuit_name,
            )
            self._previous_circuit_name = current_circuit_name
            self.coordinator.request_reload()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._sync_circuit_name()
        self._update_native_value()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return entity availability.

        Keep entities available during a panel_offline condition so sensors can show
        their grace period state (last_valid_state) or None when grace period expires.

        The unresolved-field probe runs first: the grace-period branch below
        returns True unconditionally, so probing after it would let every
        offline sensor keep reporting a field the adapter cannot resolve.

        The transport probe runs for the same reason and answers the harder
        case: a grace period is a bet that the reading will be true again
        shortly, and a transport that has stopped for good makes that bet
        unpayable. `_handle_offline_state` gives POWER sensors 0.0 while it
        runs, which is a reading nobody took.
        """
        if self._reads_an_unresolved_field:
            return False
        if not self._transport_available:
            return False
        if self.coordinator.panel_offline:
            return True
        return super().available

    @property
    def _expects_numeric(self) -> bool:
        """Return True if HA expects this sensor to have a numeric value.

        HA raises ValueError when a sensor with a numeric device_class,
        state_class, or native_unit_of_measurement reports a string state
        like STATE_UNKNOWN.  These sensors must use None instead.
        """
        if getattr(self.entity_description, "state_class", None) is not None:
            return True
        if getattr(self.entity_description, "native_unit_of_measurement", None) is not None:
            return True
        dc = getattr(self.entity_description, "device_class", None)
        if dc is not None and dc != SensorDeviceClass.ENUM:
            return True
        return False

    def _unknown_value(self) -> StateType:
        """Return the appropriate 'unknown' value for this sensor."""
        return None if self._expects_numeric else STATE_UNKNOWN

    def _update_native_value(self) -> None:
        """Update the native value of the sensor."""
        if self.coordinator.panel_offline:
            self._handle_offline_state()
            return

        self._handle_online_state()

    def _handle_offline_state(self) -> None:
        """Handle sensor state when panel is offline."""
        _LOGGER.debug(
            "STATUS_SENSOR_DEBUG: Panel is offline for %s",
            self.entity_id or self._attr_unique_id,
        )

        device_class = getattr(self.entity_description, "device_class", None)
        if device_class == SensorDeviceClass.POWER:
            self._attr_native_value = 0.0
        elif device_class == SensorDeviceClass.ENERGY:
            self._attr_native_value = None
        else:
            self._attr_native_value = self._unknown_value()

    def _handle_online_state(self) -> None:
        """Handle sensor state when panel is online."""
        value_function: Callable[[D], float | int | str | None] | None = getattr(
            self.entity_description, "value_fn", None
        )
        if value_function is None:
            _LOGGER.debug(
                "STATUS_SENSOR_DEBUG: No value_function for %s",
                self.entity_id or self._attr_unique_id,
            )
            self._attr_native_value = self._unknown_value()
            return

        try:
            data_source: D = self.get_data_source(self.coordinator.data)
            self._log_debug_info(data_source)
            raw_value: float | int | str | None = value_function(data_source)
            self._process_raw_value(raw_value)
        except (AttributeError, KeyError, IndexError) as err:
            _LOGGER.debug(
                "Value lookup failed for %s (%s): %s",
                self.entity_id or self._attr_unique_id,
                getattr(self.entity_description, "key", "?"),
                err,
            )
            self._attr_native_value = self._unknown_value()
        except Exception as err:  # noqa: BLE001  # pragma: no cover - defensive
            # Avoid noisy stack traces from value functions; fall back to unknown
            _LOGGER.warning(
                "Value function failed for %s (%s); reporting unknown",
                self.entity_id or self._attr_unique_id,
                err,
            )
            self._attr_native_value = self._unknown_value()

    def _log_debug_info(self, data_source: D) -> None:
        """Log debug information for circuit sensors."""
        # Only do debug logging if we have valid data and the panel is online
        if (
            not self.coordinator.panel_offline
            and hasattr(self, "id")
            and hasattr(data_source, "instant_power_w")
        ):
            circuit_id = getattr(self, "id", STATE_UNKNOWN)
            instant_power = getattr(data_source, "instant_power_w", None)
            description_key = getattr(self.entity_description, "key", STATE_UNKNOWN)
            _LOGGER.debug(
                "CIRCUIT_POWER_DEBUG: Circuit %s, sensor %s, instant_power=%s, data_source type=%s",
                circuit_id,
                description_key,
                instant_power,
                type(data_source).__name__,
            )

    def _process_raw_value(self, raw_value: float | str | None) -> None:
        """Process the raw value from the value function."""
        if raw_value is None:
            self._attr_native_value = self._unknown_value()
        elif isinstance(raw_value, float | int):
            self._attr_native_value = float(raw_value)
        else:
            str_value = str(raw_value)
            # For enum sensors, ensure the value is in the options list before
            # setting it — HA raises ValueError if the state is not in options.
            # Values are normalized to lowercase to satisfy HA's translation key
            # requirement ([a-z0-9-_]+); HA uses the state value directly as the
            # translation key lookup.
            #
            # Options are declared statically on each description, from the states
            # `en.json` renders. They used to be discovered here instead, which could
            # not work: options would only ever list states the panel had already
            # reached, so a state it had not yet visited was absent from its own
            # "Possible states" — and every panel advertised a different set
            # depending on what it had lived through.
            #
            # The append survives as a last resort so an undeclared value degrades to
            # a shown state rather than a ValueError. It is a warning rather than a
            # debug line because reaching it means the panel published outside the
            # enum its own catalog declares, which is a producer defect and not
            # something a consumer should absorb quietly. It also renders untranslated,
            # as a raw key.
            if self._attr_device_class is SensorDeviceClass.ENUM:
                str_value = str_value.lower()
                if not hasattr(self, "_attr_options") or self._attr_options is None:
                    self._attr_options = []
                if str_value not in self._attr_options:
                    self._attr_options.append(str_value)
                    _LOGGER.warning(
                        "%s reported '%s', which is not one of its declared states %s. "
                        "Showing it untranslated; the panel is publishing outside the "
                        "enum its catalog declares.",
                        self.entity_id or self._attr_unique_id,
                        str_value,
                        sorted(o for o in self._attr_options if o != str_value),
                    )
            self._attr_native_value = str_value

    def get_data_source(self, snapshot: SpanPanelSnapshot) -> D:
        """Get the data source for the sensor."""
        raise NotImplementedError("Subclasses must implement this method")


class SpanEnergySensorBase[T: SensorEntityDescription, D](SpanSensorBase[T, D], RestoreSensor, ABC):
    """Base class for energy sensors that includes grace period tracking.

    This class extends SpanSensorBase with:
    - Grace period tracking for offline scenarios
    - State restoration across HA restarts via RestoreSensor mixin
    - Automatic persistence of last_valid_state and last_valid_changed

    High-churn diagnostic attributes are listed in ``extra_state_attributes`` for
    the UI but omitted from recorder history via ``_unrecorded_attributes`` so the
    database is not flooded with unique attribute blobs on every energy update.
    """

    _unrecorded_attributes = _ENERGY_SENSOR_UNRECORDED_ATTRIBUTES

    def __init__(
        self,
        data_coordinator: SpanPanelCoordinator,
        description: T,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize the energy sensor with grace period tracking."""
        super().__init__(data_coordinator, description, snapshot)
        self._last_valid_state: float | None = None
        self._last_valid_changed: datetime | None = None
        self._grace_period_minutes = data_coordinator.config_entry.options.get(
            ENERGY_REPORTING_GRACE_PERIOD, 15
        )
        # Track if we've restored data (used for logging)
        self._restored_from_storage: bool = False

        # Energy dip compensation state
        self._energy_offset: float = 0.0
        self._last_panel_reading: float | None = None
        self._last_dip_delta: float | None = None
        # A dip that has been compensated but not yet settled — see
        # `process_energy_dip`. Either being non-None means part of
        # `_energy_offset` is provisional and may still be taken back: the
        # first is waiting on evidence, the second has it and is waiting out
        # the window in which that evidence could still be an artefact.
        self._pending_dip: PendingDip | None = None
        self._recently_confirmed_dip: PendingDip | None = None
        self._is_total_increasing: bool = (
            getattr(description, "state_class", None) == SensorStateClass.TOTAL_INCREASING
        )
        self._dip_compensation_enabled: bool = data_coordinator.config_entry.options.get(
            ENABLE_ENERGY_DIP_COMPENSATION, False
        )

    @property
    def energy_offset(self) -> float:
        """Return the cumulative dip compensation offset."""
        return self._energy_offset

    def _process_raw_value(self, raw_value: float | str | None) -> None:
        """Process the raw value with energy dip compensation for TOTAL_INCREASING sensors."""
        if (
            self._dip_compensation_enabled
            and self._is_total_increasing
            and isinstance(raw_value, float | int)
        ):
            raw_float = float(raw_value)
            outcome = process_energy_dip(
                raw_float,
                self._last_panel_reading,
                self._energy_offset,
                self._pending_dip,
                self._recently_confirmed_dip,
            )
            self._apply_dip_event(outcome)
            self._energy_offset = outcome.offset
            self._pending_dip = outcome.pending
            self._recently_confirmed_dip = outcome.recently_confirmed
            self._last_panel_reading = raw_float
            super()._process_raw_value(outcome.compensated)
        else:
            super()._process_raw_value(raw_value)

    def _apply_dip_event(self, outcome: DipOutcome) -> None:
        """Record the diagnostic, and tell the coordinator once a dip is final.

        The notification waits for the dip to *settle* rather than firing when
        it is first seen, or even when it is first corroborated. Reporting on
        sight is what made `SpanPanel/span#259` so loud — a notification naming
        essentially every circuit on the panel, for an event that had not
        happened. Reporting on corroboration would be quieter but still
        premature, because the reading after it can take the offset back; a
        persistent notification cannot be taken back, so it is held until
        nothing can undo what it describes. A dip that gets retracted produces
        no notification at all, because nothing happened.

        `last_dip_delta` is set when the dip is booked, since it describes what
        the counter did and the compensation is applied from that moment, and
        cleared on retraction, since by then the counter turns out not to have
        done it.
        """
        if outcome.event is DipEvent.BOOKED:
            self._last_dip_delta = outcome.delta
        elif outcome.event is DipEvent.RETRACTED:
            self._last_dip_delta = None
            _LOGGER.debug(
                "Energy dip retracted for %s: %s Wh given back, offset back to %s",
                self.entity_id or self._attr_unique_id,
                outcome.delta,
                outcome.offset,
            )
        elif outcome.event is DipEvent.CONFIRMED:
            _LOGGER.debug(
                "Energy dip corroborated for %s: %s Wh, reported if it settles",
                self.entity_id or self._attr_unique_id,
                outcome.delta,
            )

        # Checked separately from `event`: a dip can settle on the same reading
        # that books or retracts another one.
        if outcome.settled is not None:
            self.coordinator.report_energy_dip(
                self.entity_id or self._attr_unique_id or "unknown",
                outcome.settled,
                outcome.offset,
            )

    async def async_added_to_hass(self) -> None:
        """Restore grace period state when entity is added to Home Assistant.

        This method is called when the entity is added to HA, which happens
        during startup or when the integration is reloaded. We use this
        opportunity to restore the grace period tracking state from storage.

        Dip compensation state is restored BEFORE calling super() because
        super() registers the coordinator listener, and the SPAN coordinator
        may immediately push a snapshot via async_set_updated_data() which
        calls async_update_listeners() synchronously. If that push fires before
        the offset is restored, _process_raw_value() runs with _energy_offset=0,
        reports the raw panel counter to HA, and HA statistics treats the value
        drop as a counter reset — permanently inflating the energy sum.
        """
        # Pre-fetch stored extra data so dip state can be applied before the
        # coordinator listener is registered inside super().
        last_extra_data = await self.async_get_last_extra_data()
        restored = (
            SpanEnergyExtraStoredData.from_dict(last_extra_data.as_dict())
            if last_extra_data is not None
            else None
        )

        # Restore dip compensation state before super() registers the listener.
        if restored and self._dip_compensation_enabled and self._is_total_increasing:
            if restored.energy_offset is not None:
                self._energy_offset = restored.energy_offset
            if restored.last_panel_reading is not None:
                self._last_panel_reading = restored.last_panel_reading
            if restored.last_dip_delta is not None:
                self._last_dip_delta = restored.last_dip_delta
            if restored.pending_dip_baseline is not None and restored.pending_dip_delta is not None:
                self._pending_dip = PendingDip(
                    baseline=restored.pending_dip_baseline,
                    delta=restored.pending_dip_delta,
                )
            if (
                restored.confirmed_dip_baseline is not None
                and restored.confirmed_dip_delta is not None
                and restored.confirmed_dip_ticks_left is not None
            ):
                self._recently_confirmed_dip = PendingDip(
                    baseline=restored.confirmed_dip_baseline,
                    delta=restored.confirmed_dip_delta,
                    confirmed_ticks_left=restored.confirmed_dip_ticks_left,
                )
            _LOGGER.debug(
                "Restored energy dip compensation for %s: offset=%s, last_reading=%s, last_dip=%s",
                self.entity_id or self._attr_unique_id,
                self._energy_offset,
                self._last_panel_reading,
                self._last_dip_delta,
            )

        # Register the coordinator listener (and base RestoreSensor setup).
        await super().async_added_to_hass()

        # Complete grace period restoration from the already-fetched extra data.
        if restored:
            if restored.last_valid_state is not None:
                self._last_valid_state = restored.last_valid_state

            if restored.last_valid_changed is not None:
                try:
                    parsed = datetime.fromisoformat(restored.last_valid_changed)
                    # Ensure UTC-aware: old storage may have naive timestamps
                    self._last_valid_changed = (
                        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
                    )
                    self._restored_from_storage = True
                    _LOGGER.debug(
                        "Restored grace period state for %s: "
                        "last_valid_state=%s, last_valid_changed=%s",
                        self.entity_id or self._attr_unique_id,
                        self._last_valid_state,
                        self._last_valid_changed,
                    )
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Failed to parse restored last_valid_changed for %s: %s",
                        self.entity_id or self._attr_unique_id,
                        e,
                    )

        # Seed grace period tracking from the last stored HA state when extra data
        # is missing (e.g., after first install or early offline event).
        await self._initialize_grace_period_from_last_state()

    async def _initialize_grace_period_from_last_state(self) -> None:
        """Seed grace tracking from HA's last stored state when extra data is missing."""

        if self._last_valid_state is not None:
            return

        try:
            last_state = await self.async_get_last_state()
        except Exception as err:  # noqa: BLE001  # pragma: no cover - defensive
            _LOGGER.debug(
                "Grace period restore: failed for %s: %s",
                self.entity_id or self._attr_unique_id,
                err,
            )
            return

        value, changed = initialize_from_last_state(last_state)
        if value is not None:
            self._last_valid_state = value
            self._last_valid_changed = changed
            self._restored_from_storage = True
            _LOGGER.debug(
                "Grace period initialized for %s: value=%s, changed=%s",
                self.entity_id or self._attr_unique_id,
                value,
                changed,
            )

    @property
    def extra_restore_state_data(self) -> SensorExtraStoredData:
        """Return sensor-specific state data to be restored.

        This data is automatically saved by Home Assistant when the
        integration is unloaded or HA shuts down, and restored when
        the entity is added back to HA.
        """
        return SpanEnergyExtraStoredData(
            native_value=(
                float(self._attr_native_value)
                if isinstance(self._attr_native_value, int | float)
                else None
            ),
            native_unit_of_measurement=self.native_unit_of_measurement,
            last_valid_state=self._last_valid_state,
            last_valid_changed=(
                self._last_valid_changed.isoformat() if self._last_valid_changed else None
            ),
            energy_offset=self._energy_offset or None,
            last_panel_reading=self._last_panel_reading,
            last_dip_delta=self._last_dip_delta,
            pending_dip_baseline=self._pending_dip.baseline if self._pending_dip else None,
            pending_dip_delta=self._pending_dip.delta if self._pending_dip else None,
            confirmed_dip_baseline=(
                self._recently_confirmed_dip.baseline if self._recently_confirmed_dip else None
            ),
            confirmed_dip_delta=(
                self._recently_confirmed_dip.delta if self._recently_confirmed_dip else None
            ),
            confirmed_dip_ticks_left=(
                self._recently_confirmed_dip.confirmed_ticks_left
                if self._recently_confirmed_dip
                else None
            ),
        )

    def _update_native_value(self) -> None:
        """Update the native value with grace period logic for energy sensors."""
        if self.coordinator.panel_offline:
            # Use grace period logic when offline
            self._handle_offline_grace_period()
            return

        # Panel is online - use normal update logic from parent class
        super()._update_native_value()

        self._track_valid_state(self._attr_native_value)

    def _track_valid_state(self, value: StateType | date | Decimal | None) -> None:
        """Update last valid state tracking when a numeric value is available."""
        if value is not None and isinstance(value, int | float | Decimal):
            self._last_valid_state = float(value)
            self._last_valid_changed = datetime.now(tz=UTC)

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator with grace period tracking."""
        self._sync_circuit_name()

        # Update grace period from options in case it changed
        self._grace_period_minutes = self.coordinator.config_entry.options.get(
            ENERGY_REPORTING_GRACE_PERIOD, 15
        )

        # Update dip compensation flag from options in case it changed
        self._dip_compensation_enabled = self.coordinator.config_entry.options.get(
            ENABLE_ENERGY_DIP_COMPENSATION, False
        )

        # Use the overridden _update_native_value method which handles grace period
        self._update_native_value()

        # Call the parent's parent class coordinator update to avoid the intermediate parent's logic
        super(SpanSensorBase, self)._handle_coordinator_update()

    def _handle_offline_grace_period(self) -> None:
        """Handle grace period logic when panel is offline."""
        result = handle_offline_grace_period(
            self._last_valid_state,
            self._last_valid_changed,
            self._attr_native_value,
            coerce_grace_period_minutes(self._grace_period_minutes),
        )
        self._attr_native_value = result[0]
        self._last_valid_state = result[1]
        self._last_valid_changed = result[2]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes including grace period info."""
        attributes = {}

        # Always show grace period information if we have valid tracking data
        if self._last_valid_changed is not None:
            if self._last_valid_state is not None:
                attributes["last_valid_state"] = str(self._last_valid_state)
            attributes["last_valid_changed"] = self._last_valid_changed.isoformat()

            # Calculate grace period remaining (coerce + sync back to instance)
            grace_minutes = coerce_grace_period_minutes(self._grace_period_minutes)
            self._grace_period_minutes = grace_minutes
            if grace_minutes > 0:
                time_since_last_valid = datetime.now(tz=UTC) - self._last_valid_changed
                grace_period_duration = timedelta(minutes=grace_minutes)
                remaining_seconds = (grace_period_duration - time_since_last_valid).total_seconds()
                remaining_minutes = max(0, int(remaining_seconds / 60))
                attributes["grace_period_remaining"] = str(remaining_minutes)

                # Indicate if we're currently using grace period
                panel_offline = self.coordinator.panel_offline
                if panel_offline and remaining_seconds > 0:
                    attributes["using_grace_period"] = "True"

        # Energy dip compensation attributes
        dip_attrs = build_dip_attributes(
            self._energy_offset,
            self._last_dip_delta,
            self._is_total_increasing,
            self._dip_compensation_enabled,
        )
        attributes.update(dip_attrs)

        return attributes or None
