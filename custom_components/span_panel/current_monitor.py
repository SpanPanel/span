"""Current monitoring for SPAN Panel circuits and mains legs.

Detects spike (instantaneous) and continuous overload conditions by comparing
current readings against the rating each point is judged against, with configurable
thresholds. A circuit is judged against its panel rating, or both breakers of a
package the user copied from the SPAN app; the monitor asks the
`PackageRatingManager` for that rating and stores none.
Dispatches notifications via event bus, notify services, and persistent
notifications.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Final, Literal, TypedDict

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from span_panel_api import SpanPanelSnapshot, shared_meter_groups

from .alert_dispatcher import dispatch_alert
from .const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE,
    DEFAULT_NOTIFICATION_PRIORITY,
    DEFAULT_NOTIFICATION_TITLE_TEMPLATE,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    DOMAIN,
)
from .device_id_migration import rekeyed
from .entity_resolver import resolve_circuit_id
from .entry_reloads import async_update_entry_options_applied
from .helpers import build_circuit_unique_id, build_panel_unique_id, construct_circuit_label
from .meter_capacity import meter_capacity_a
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    NOTIFICATION_MESSAGE_TEMPLATE,
    NOTIFICATION_PRIORITY,
    NOTIFICATION_TITLE_TEMPLATE,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)
from .package_ratings import (
    circuit_package_ratings_form,
    package_ratings_payload,
    panel_describes_breakers,
    service_rating_a,
)
from .threshold_evaluator import (
    MonitoringSettings,
    check_continuous,
    check_spike,
    is_monitoring_disabled,
    resolve_thresholds,
)
from .util import circuit_display_name

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .package_rating_manager import PackageRatingManager
    from .package_ratings import (
        JudgedState,
        MonitoringRating,
        PackageRatings,
        PackageRatingsForm,
    )

_LOGGER = logging.getLogger(__name__)

# Maps mains leg identifiers to SpanPanelSnapshot attribute names
_MAINS_CURRENT_ATTRS: dict[str, str] = {
    "upstream_l1": "upstream_l1_current_a",
    "upstream_l2": "upstream_l2_current_a",
    "downstream_l1": "downstream_l1_current_a",
    "downstream_l2": "downstream_l2_current_a",
}

_STORAGE_VERSION = 1
_STORAGE_KEY_PREFIX = "span_panel_current_monitor"

_LEGACY_ENABLE_OPTION: Final = "enable_current_monitoring"
"""The option a release before the monitoring store switched monitoring on by.

Read only by `async_adopt_legacy_enable_option`, which moves it into the store.
"""

StoredMonitoring = TypedDict(
    "StoredMonitoring",
    {
        "enabled": bool,
        "circuit_overrides": dict[str, MonitoringSettings],
        "mains_overrides": dict[str, MonitoringSettings],
        "global": MonitoringSettings,
    },
    total=False,
)
"""One panel's monitoring settings, as `CurrentMonitor` saves them.

The functional form, because `global` is a keyword. Not total: a store
`async_save_disabled` wrote before monitoring was ever on holds `enabled`
alone, and `global` is saved only once a global setting is. A compile-time
claim only; `_read_stored` is what the disk is held to.
"""


@dataclass
class MonitoredPointState:
    """Tracking state for a single monitored point (circuit or mains leg).

    `last_current_a` is None until the point's first sample: a current nobody
    read is not 0 A. Only the monitoring status reads it; threshold checks are
    handed each sample directly.
    """

    last_current_a: float | None = None
    over_threshold_since: datetime | None = None
    last_spike_alert: datetime | None = None
    last_continuous_alert: datetime | None = None


type MonitoringBasis = Literal["circuit", "group"]
"""What a monitored point's rating is: one circuit's judged-against rating, or a shared meter's capacity."""


class PointMonitoringStatus(TypedDict):
    """What monitoring reports of one point, as the Monitoring tab reads it: the mains' entry.

    The figures are a judgement, so a point nobody is monitoring reports no
    current, utilization or window, while its alert times, being history, stay.
    """

    name: str
    last_current_a: float | None
    breaker_rating_a: float | None
    monitoring_rating_a: float | None
    monitoring_rating_user_set: bool
    package_ratings_a: list[int] | None
    package_ratings_form: PackageRatingsForm | None
    package_ratings_state: JudgedState | None
    utilization_pct: float | None
    continuous_threshold_pct: int
    spike_threshold_pct: int
    window_duration_m: int
    cooldown_duration_m: int
    has_override: bool
    monitoring_enabled: bool
    over_threshold_since: str | None
    last_spike_alert: str | None
    last_continuous_alert: str | None


class CircuitMonitoringStatus(PointMonitoringStatus):
    """What monitoring reports of one circuit: its point's entry, and which point it is."""

    basis: MonitoringBasis
    point_id: str
    """The point this entry's judgement is: a shared meter's members carry its key."""


class MonitoringStatus(TypedDict):
    """Every point monitoring tracks, each by the entity the Monitoring tab shows it under."""

    circuits: dict[str, CircuitMonitoringStatus]
    mains: dict[str, PointMonitoringStatus]


@dataclass(frozen=True, slots=True)
class MonitoredCircuit:
    """One point the monitor judges: a circuit, or the circuits that share one meter.

    Circuits that share a meter all report that meter's current, so judging
    each against its own breaker would overstate every one of them. The group
    is judged once, against its capacity (`meter_capacity_a`), under the id of
    its first member: that key is stable while the membership is, which is
    enough for alerts and overrides, neither of which is an entity.

    `name` is the panel's name for the point, each member's label joined with " + ":
    the alert event's `alert_name`, which automations read, so a rename in Home
    Assistant never changes it. The alert's text names the point as Home
    Assistant shows it instead (`CurrentMonitor._display_name`).

    `rating_a` is the rating judged against and `panel_rating_a` the breaker rating the
    panel published, each None where a circuit, or the capacity of a group, is unknown.
    For a group, both are capacities by the same rule, `rating_a` over the ratings its
    members are judged against and `panel_rating_a` over the ratings the panel
    published, and so equal: only a panel that describes its breakers names shared
    meters, and none of its circuits holds a package (`panel_describes_breakers`).
    `package_ratings_a` is the package a user copied for a circuit and
    `package_ratings_state` what that copy is, each None where there is none and for a
    group. Only a copy that applies is what `rating_a` is the capacity of: one kept on
    averaged legs leaves the circuit judged against the panel's rating, and one waiting
    on a value leaves it unrated. `rating_user_set` is whether the circuit is judged
    against a package the user copied, and so never true of a group.
    """

    point_id: str
    members: tuple[str, ...]
    name: str
    current_a: float | None
    rating_a: float | None
    panel_rating_a: float | None
    rating_user_set: bool
    package_ratings_a: PackageRatings | None
    package_ratings_state: JudgedState | None
    basis: MonitoringBasis


def monitored_circuits(
    snapshot: SpanPanelSnapshot, ratings: Callable[[str], MonitoringRating]
) -> dict[str, MonitoredCircuit]:
    """Every point the monitor judges, by the id of each circuit it covers.

    `ratings` answers the rating each circuit is judged against, which is what a point is
    judged against; the snapshot's own `breaker_rating_a` is the panel's.
    """
    points: dict[str, MonitoredCircuit] = {}
    for key, members in shared_meter_groups(snapshot.circuits).items():
        resolved = {member: ratings(member) for member in members}
        spaces = {member: snapshot.circuits[member].tabs for member in members}
        point = MonitoredCircuit(
            point_id=key,
            members=members,
            name=" + ".join(
                construct_circuit_label(snapshot.circuits[member], member) for member in members
            ),
            current_a=snapshot.circuits[key].current_a,
            rating_a=meter_capacity_a((spaces[m], r.judged_a) for m, r in resolved.items()),
            panel_rating_a=meter_capacity_a((spaces[m], r.panel_a) for m, r in resolved.items()),
            rating_user_set=any(r.user_set for r in resolved.values()),
            package_ratings_a=None,
            package_ratings_state=None,
            basis="group",
        )
        points.update(dict.fromkeys(members, point))
    for circuit_id, circuit in snapshot.circuits.items():
        # A meter outside the panel has no breaker to judge it against.
        if circuit_id not in points and not circuit.measures_outside_panel:
            rating = ratings(circuit_id)
            points[circuit_id] = MonitoredCircuit(
                point_id=circuit_id,
                members=(circuit_id,),
                # Its label, as a shared meter's members are named above: the
                # panel's name, or its breaker positions, never its wire id.
                name=construct_circuit_label(circuit, circuit_id),
                current_a=circuit.current_a,
                rating_a=rating.judged_a,
                panel_rating_a=rating.panel_a,
                rating_user_set=rating.user_set,
                package_ratings_a=rating.package_ratings_a,
                package_ratings_state=rating.state,
                basis="circuit",
            )
    return points


class CurrentMonitor:
    """Monitors current draw against each circuit's rating for overload detection.

    The ratings come from the panel's `PackageRatingManager`, which the
    coordinator reconciles before any listener sees a snapshot. The monitor only
    resolves them; it holds none, so nothing here can lose a package the user copied.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        ratings: PackageRatingManager,
        snapshot: SpanPanelSnapshot,
    ) -> None:
        """Initialize the CurrentMonitor, holding the coordinator's snapshot without judging it.

        Only `process_snapshot` judges, so building a monitor raises no alert and
        opens no over-threshold window: the first judgement is the next update's.
        Holding the snapshot from the start means every reader has one: the status
        lists the panel's circuits, and a threshold set on a shared meter's member
        is held under the group's key, before that update arrives.
        """
        self._hass = hass
        self._entry = entry
        self._ratings = ratings
        self._circuit_states: dict[str, MonitoredPointState] = {}
        self._mains_states: dict[str, MonitoredPointState] = {}
        self._circuit_overrides: dict[str, MonitoringSettings] = {}
        self._mains_overrides: dict[str, MonitoringSettings] = {}
        self._global_settings: MonitoringSettings = {}
        self._last_snapshot = snapshot
        self._store = _store_for(hass, entry)

    # --- Public API ---

    def process_snapshot(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for all circuits and mains legs."""
        self._last_snapshot = snapshot
        if self._fold_member_overrides(snapshot):
            self._hass.async_create_task(self.async_save_overrides())
        self._evaluate_circuits(snapshot)
        self._evaluate_mains(snapshot)

    def get_circuit_state(self, circuit_id: str) -> MonitoredPointState | None:
        """Return tracking state for a circuit, or None if not monitored."""
        return self._circuit_states.get(circuit_id)

    def get_mains_state(self, leg: str) -> MonitoredPointState | None:
        """Return tracking state for a mains leg, or None if not monitored."""
        return self._mains_states.get(leg)

    def set_circuit_override(self, circuit_id: str, overrides: MonitoringSettings) -> None:
        """Set per-circuit threshold overrides; a shared meter's member sets its group's."""
        key = self._override_key(circuit_id)
        existing = self._circuit_overrides.get(key, {})
        existing.update(overrides)
        if self._is_redundant_override(existing):
            self._circuit_overrides.pop(key, None)
        else:
            self._circuit_overrides[key] = existing
        if is_monitoring_disabled(existing):
            self._forget_judgement(self._circuit_states.get(key))
        self._hass.async_create_task(self.async_save_overrides())

    def clear_circuit_override(self, circuit_id: str) -> None:
        """Remove per-circuit threshold overrides; a shared meter's member clears its group's."""
        key = self._override_key(circuit_id)
        self._circuit_overrides.pop(key, None)
        self._circuit_states.pop(key, None)
        self._hass.async_create_task(self.async_save_overrides())

    @staticmethod
    def _forget_judgement(state: MonitoredPointState | None) -> None:
        """Drop what monitoring last saw of a point whose monitoring was just turned off.

        The sample and the window it opened belong to monitoring that has stopped:
        kept, a window opened long ago would make the first sample after
        monitoring resumes alert at once. The alert times stay, since they are
        history and hold the cooldown.
        """
        if state is not None:
            state.last_current_a = None
            state.over_threshold_since = None

    def _points(self, snapshot: SpanPanelSnapshot) -> dict[str, MonitoredCircuit]:
        """Every point judged in this snapshot, rated as the manager resolves each circuit."""
        return monitored_circuits(snapshot, lambda cid: self._ratings.resolve(cid, snapshot))

    def _override_key(self, circuit_id: str) -> str:
        """Return the key a circuit's override and state are held under.

        The monitored point's id: the circuit's own, or for a member of a shared
        meter the group's first member, which is the key every reader uses. The
        circuit's own id for one the snapshot does not hold.
        """
        point = self._points(self._last_snapshot).get(circuit_id)
        return point.point_id if point is not None else circuit_id

    def _fold_member_overrides(self, snapshot: SpanPanelSnapshot) -> bool:
        """Move overrides held under a shared meter's other members onto the group's key.

        An override can sit under a member's own id, where no reader looks, in
        two ways. The store can hold one there, written before the meter was
        known to be shared or re-keyed by a device-id migration onto ids in
        another order; `async_start` folds those as it loads. And one can be set
        while the held snapshot does not yet group the member, because the panel
        has not yet published its `meter/shared-with-device-ids` or a peer that
        sorts first has not arrived. Nothing reloads the entry when either
        arrives, so every judged snapshot is folded too. The group's own key
        wins where it already holds one; otherwise the first member, in group
        order, that holds one does. The rest are dropped. Returns whether
        anything moved, so the caller persists the result.
        """
        moved = False
        for key, members in shared_meter_groups(snapshot.circuits).items():
            stored = [m for m in members if m != key and m in self._circuit_overrides]
            if not stored:
                continue
            if key not in self._circuit_overrides:
                self._circuit_overrides[key] = self._circuit_overrides[stored[0]]
            for member in stored:
                del self._circuit_overrides[member]
            moved = True
        return moved

    def set_mains_override(self, leg: str, overrides: MonitoringSettings) -> None:
        """Set per-mains-leg threshold overrides."""
        existing = self._mains_overrides.get(leg, {})
        existing.update(overrides)
        if self._is_redundant_override(existing):
            self._mains_overrides.pop(leg, None)
        else:
            self._mains_overrides[leg] = existing
        if is_monitoring_disabled(existing):
            self._forget_judgement(self._mains_states.get(leg))
        self._hass.async_create_task(self.async_save_overrides())

    def _is_redundant_override(self, override: MonitoringSettings) -> bool:
        """Check if an override matches global defaults (and can be removed).

        An override is redundant if monitoring is enabled (or not set, defaulting
        to True) and all threshold values match the global settings.
        """
        if override.get("monitoring_enabled") is False:
            return False
        g = self.get_global_settings()
        threshold_keys = (
            CONTINUOUS_THRESHOLD_PCT,
            SPIKE_THRESHOLD_PCT,
            WINDOW_DURATION_M,
            COOLDOWN_DURATION_M,
        )
        for key in threshold_keys:
            if key in override and override[key] != g[key]:
                return False
        # All present keys match globals (or aren't set), and monitoring is enabled
        return True

    def clear_mains_override(self, leg: str) -> None:
        """Remove per-mains-leg threshold overrides."""
        self._mains_overrides.pop(leg, None)
        self._mains_states.pop(leg, None)
        self._hass.async_create_task(self.async_save_overrides())

    def get_global_settings(self) -> MonitoringSettings:
        """Get the effective global monitoring settings.

        Returns stored global settings if available, otherwise falls back
        to config entry options for backward compatibility during migration.
        """
        opts: Mapping[str, Any]
        if self._global_settings:
            opts = self._global_settings
        else:
            opts = self._entry.options
        return {
            CONTINUOUS_THRESHOLD_PCT: opts.get(
                CONTINUOUS_THRESHOLD_PCT, DEFAULT_CONTINUOUS_THRESHOLD_PCT
            ),
            SPIKE_THRESHOLD_PCT: opts.get(SPIKE_THRESHOLD_PCT, DEFAULT_SPIKE_THRESHOLD_PCT),
            WINDOW_DURATION_M: opts.get(WINDOW_DURATION_M, DEFAULT_WINDOW_DURATION_M),
            COOLDOWN_DURATION_M: opts.get(COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M),
            NOTIFY_TARGETS: opts.get(NOTIFY_TARGETS, ""),
            NOTIFICATION_TITLE_TEMPLATE: opts.get(
                NOTIFICATION_TITLE_TEMPLATE, DEFAULT_NOTIFICATION_TITLE_TEMPLATE
            ),
            NOTIFICATION_MESSAGE_TEMPLATE: opts.get(
                NOTIFICATION_MESSAGE_TEMPLATE, DEFAULT_NOTIFICATION_MESSAGE_TEMPLATE
            ),
            NOTIFICATION_PRIORITY: opts.get(NOTIFICATION_PRIORITY, DEFAULT_NOTIFICATION_PRIORITY),
        }

    def set_global_settings(self, settings: MonitoringSettings) -> None:
        """Update global monitoring settings in storage."""
        valid_keys = {
            CONTINUOUS_THRESHOLD_PCT,
            SPIKE_THRESHOLD_PCT,
            WINDOW_DURATION_M,
            COOLDOWN_DURATION_M,
            NOTIFY_TARGETS,
            NOTIFICATION_TITLE_TEMPLATE,
            NOTIFICATION_MESSAGE_TEMPLATE,
            NOTIFICATION_PRIORITY,
        }
        for key, value in settings.items():
            if key in valid_keys:
                self._global_settings[key] = value

        self._hass.async_create_task(self.async_save_overrides())

    def _resolve_circuit_entity_id(self, circuit_id: str) -> str:
        """Return the entity a circuit is shown under: its monitoring status key, and its alert's `{entity_id}`.

        Its Current sensor, else its Power sensor, matching the frontend lookup
        order (circuit.entities.current ?? .power): a circuit that never publishes
        a current has no Current sensor, nor does one whose current first arrived
        after setup until the next reload. The circuit's id where Home Assistant
        holds neither, as for a circuit the panel added since the entry was set up.
        """
        serial = self._last_snapshot.serial_number
        entity_reg = er.async_get(self._hass)
        # Try current sensor first (matches frontend preference)
        current_uid = build_circuit_unique_id(serial, circuit_id, "current")
        entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, current_uid)
        if entity_id is not None:
            return entity_id
        # Fall back to power sensor
        power_uid = build_circuit_unique_id(serial, circuit_id, "instantPowerW")
        entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, power_uid)
        return entity_id if entity_id is not None else circuit_id

    def resolve_entity_to_circuit_id(
        self, entity_id: str, snapshot: SpanPanelSnapshot, *, service: str
    ) -> str:
        """Resolve one of the circuit's entities to its internal circuit_id.

        Accepts any of this integration's entities for the circuit, such as its
        current sensor (sensor.span_panel_kitchen_current), or a raw circuit_id
        for backwards compatibility, an id from before a firmware update gave
        the circuit a new one included (`entity_resolver.resolve_circuit_id`,
        which logs it against `service`). The circuit is the one of the
        snapshot's circuits that the entity's unique_id names; an id is opaque,
        so it is found among them rather than by its shape.

        The caller passes the coordinator's live snapshot, the one every service
        resolves its target against, rather than this monitor reading its own,
        which is the last it judged or, before the first, the one it was built
        holding.

        The ids this monitor holds overrides for are searched too, so a circuit
        that has left the panel can still have its override cleared through its
        orphaned entity.
        """
        return resolve_circuit_id(
            self._hass, entity_id, snapshot, service=service, also=self._circuit_overrides
        )

    def resolve_entity_to_mains_leg(self, entity_id: str) -> str:
        """Resolve a current sensor entity_id to its internal mains leg name.

        Accepts either an entity_id (sensor.span_panel_upstream_l1_current)
        or a raw leg name (upstream_l1) for backwards compatibility.
        """
        # Check if it's already a known leg name
        if entity_id in _MAINS_CURRENT_ATTRS:
            return entity_id
        entity_reg = er.async_get(self._hass)
        entry = entity_reg.async_get(entity_id)
        if entry is not None and entry.unique_id:
            # unique_id format: span_{serial}_{leg}_current
            # e.g., span_sp3-242424-001_upstream_l1_current
            for leg in _MAINS_CURRENT_ATTRS:
                if f"_{leg}_current" in entry.unique_id:
                    return leg
        return entity_id

    def _resolve_mains_entity_id(self, leg: str) -> str:
        """Return a mains leg's Current sensor, which its alert's `{entity_id}` carries.

        Upstream L1's is also the key the monitoring status shows the mains
        under. The leg's name where Home Assistant holds no such sensor, as for a
        leg whose current first arrived after setup.
        """
        serial = self._last_snapshot.serial_number
        # The sensor key matches the leg name + "_current" (e.g., "upstream_l1_current")
        unique_id = build_panel_unique_id(serial, f"{leg}_current")
        entity_reg = er.async_get(self._hass)
        entity_id = entity_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        return entity_id if entity_id is not None else leg

    def get_monitoring_status(self) -> MonitoringStatus:
        """Return current monitoring state for all tracked points."""
        snapshot = self._last_snapshot
        main_rating = service_rating_a(snapshot)

        circuits: dict[str, CircuitMonitoringStatus] = {}
        # Include all circuits from the snapshot, not just those with active state.
        # Circuits with relays off may have no state yet but should still appear.
        all_circuit_ids = set(self._circuit_states.keys()) | set(snapshot.circuits)
        # A meter outside the panel has no breaker to monitor against.
        all_circuit_ids -= {
            cid for cid, circuit in snapshot.circuits.items() if circuit.measures_outside_panel
        }
        points = self._points(snapshot)
        # Asked once for every circuit's form: no circuit takes a copy on a panel
        # that describes its multi-load breakers.
        describes_breakers = panel_describes_breakers(snapshot)

        for cid in all_circuit_ids:
            circuit = snapshot.circuits.get(cid)
            # A member of a shared meter shows its group's judgement: the
            # group's state, rating and thresholds, all under the group's key.
            point = points.get(cid)
            point_id = point.point_id if point else cid
            state = self._circuit_states.get(point_id)
            rating = float(point.rating_a) if point and point.rating_a else None
            breaker_rating = float(point.panel_rating_a) if point and point.panel_rating_a else None
            override = self._circuit_overrides.get(point_id, {})
            # A point nobody is monitoring has no judgement to report, whatever it last
            # sampled; its alert times stay, being history.
            judged = not is_monitoring_disabled(override)
            last_current = state.last_current_a if state and judged else None
            utilization = (
                round(last_current / rating * 100, 1)
                if rating and last_current is not None
                else None
            )
            cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
                override, self.get_global_settings()
            )
            entity_id = self._resolve_circuit_entity_id(cid)
            has_override = bool(override)
            # The form a copy of the circuit's package takes, by the rule the topology
            # says it by. None on a shared meter's entry, which is the group's
            # judgement and takes no copy, and for a circuit no longer on the panel.
            form = (
                circuit_package_ratings_form(circuit, describes_breakers=describes_breakers)
                if circuit is not None and point is not None and point.basis == "circuit"
                else None
            )

            circuits[entity_id] = {
                "name": construct_circuit_label(circuit, cid) if circuit else cid,
                "basis": point.basis if point else "circuit",
                # The point this entry's judgement is: members of a shared meter
                # carry its key, so a consumer counts each point once.
                "point_id": point_id,
                "last_current_a": last_current,
                "breaker_rating_a": breaker_rating,
                "monitoring_rating_a": rating,
                "monitoring_rating_user_set": point.rating_user_set if point else False,
                "package_ratings_a": package_ratings_payload(
                    point.package_ratings_a if point else None
                ),
                "package_ratings_form": form,
                "package_ratings_state": point.package_ratings_state if point else None,
                "utilization_pct": utilization,
                "continuous_threshold_pct": cont_pct,
                "spike_threshold_pct": spike_pct,
                "window_duration_m": window_m,
                "cooldown_duration_m": cooldown_m,
                "has_override": has_override,
                "monitoring_enabled": judged,
                "over_threshold_since": state.over_threshold_since.isoformat()
                if state and judged and state.over_threshold_since
                else None,
                "last_spike_alert": state.last_spike_alert.isoformat()
                if state and state.last_spike_alert
                else None,
                "last_continuous_alert": state.last_continuous_alert.isoformat()
                if state and state.last_continuous_alert
                else None,
            }

        # Present mains as a single entry using the higher of the two upstream legs.
        # The main breaker is a single 240V breaker; internally we still evaluate
        # per-leg, but the UI shows one combined "Mains Breaker" point. Only legs
        # that have been sampled count; with neither, there is no current to show.
        mains: dict[str, PointMonitoringStatus] = {}
        upstream = (self._mains_states.get("upstream_l1"), self._mains_states.get("upstream_l2"))
        sampled = [
            leg.last_current_a
            for leg in upstream
            if leg is not None and leg.last_current_a is not None
        ]
        override = self._mains_overrides.get("upstream_l1", {})
        # As for a circuit: the mains' figures are a judgement, and none is made while it is off.
        judged = not is_monitoring_disabled(override)
        peak_current = max(sampled) if sampled and judged else None
        utilization = (
            round(peak_current / main_rating * 100, 1)
            if main_rating and peak_current is not None
            else None
        )

        # Use upstream_l1 thresholds as the representative (they're the same unless overridden)
        cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
            override, self.get_global_settings()
        )
        has_override = bool(override)

        # Resolve to the current_power entity for the mains entry key
        mains_entity_id = self._resolve_mains_entity_id("upstream_l1")
        mains[mains_entity_id] = {
            "name": "Mains Breaker",
            "last_current_a": peak_current,
            "breaker_rating_a": main_rating,
            # The mains takes no package ratings, so every entry reads the same way.
            "monitoring_rating_a": main_rating,
            "monitoring_rating_user_set": False,
            "package_ratings_a": None,
            "package_ratings_form": None,
            "package_ratings_state": None,
            "utilization_pct": utilization,
            "continuous_threshold_pct": cont_pct,
            "spike_threshold_pct": spike_pct,
            "window_duration_m": window_m,
            "cooldown_duration_m": cooldown_m,
            "has_override": has_override,
            "monitoring_enabled": judged,
            "over_threshold_since": None,
            "last_spike_alert": None,
            "last_continuous_alert": None,
        }

        return {"circuits": circuits, "mains": mains}

    async def async_start(self) -> None:
        """Start the monitor: load persisted overrides, folded onto the snapshot it holds.

        Folded here, before any reader runs, because every reader finds a shared
        meter's override under the key the held snapshot gives its group. Left
        under another member until the first judgement, a stored override reads
        as absent, survives a clear, and loses its other settings to a threshold
        set on that member.
        """
        await self.async_load_overrides()
        if self._fold_member_overrides(self._last_snapshot):
            await self.async_save_overrides()
        _LOGGER.info("Current monitor started")

    def async_stop(self) -> None:
        """Stop the monitor — clear all tracking state."""
        self._circuit_states.clear()
        self._mains_states.clear()
        _LOGGER.info("Current monitor stopped")

    async def async_save_disabled(self) -> None:
        """Mark monitoring as disabled in storage, preserving settings."""
        data = _read_stored(await self._store.async_load(), self._entry) or StoredMonitoring()
        data["enabled"] = False
        await self._store.async_save(data)

    async def async_save_overrides(self) -> None:
        """Persist circuit overrides, mains overrides, and global settings to storage."""
        data = StoredMonitoring(
            enabled=True,
            circuit_overrides=self._circuit_overrides,
            mains_overrides=self._mains_overrides,
        )
        if self._global_settings:
            data["global"] = self._global_settings
        await self._store.async_save(data)

    async def async_load_overrides(self) -> None:
        """Load circuit overrides, mains overrides, and global settings from storage."""
        data = _read_stored(await self._store.async_load(), self._entry)
        if data is None:
            return
        self._circuit_overrides = data.get("circuit_overrides", {})
        self._mains_overrides = data.get("mains_overrides", {})
        self._global_settings = data.get("global", {})

    @staticmethod
    async def async_is_enabled(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Check if monitoring was previously enabled by reading storage."""
        data = _read_stored(await _store_for(hass, entry).async_load(), entry)
        return data is not None and data.get("enabled", False)

    # --- Threshold resolution (delegated to threshold_evaluator) ---

    def _resolve_circuit_thresholds(self, circuit_id: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a circuit, or its shared meter."""
        return resolve_thresholds(
            self._circuit_overrides.get(self._override_key(circuit_id), {}),
            self.get_global_settings(),
        )

    def _resolve_mains_thresholds(self, leg: str) -> tuple[int, int, int, int]:
        """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a mains leg."""
        return resolve_thresholds(self._mains_overrides.get(leg, {}), self.get_global_settings())

    # --- Circuit evaluation ---

    def _evaluate_circuits(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for every monitored point: each circuit, and each shared meter once."""
        points = {point.point_id: point for point in self._points(snapshot).values()}
        for circuit_id, point in points.items():
            if point.current_a is None or point.rating_a is None:
                continue
            if is_monitoring_disabled(self._circuit_overrides.get(circuit_id, {})):
                continue

            state = self._circuit_states.setdefault(circuit_id, MonitoredPointState())
            current = abs(point.current_a)
            rating = point.rating_a
            state.last_current_a = current

            cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
                self._circuit_overrides.get(circuit_id, {}), self.get_global_settings()
            )

            alert = check_spike(state, current, rating, spike_pct, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=point.name,
                    display_name=self._display_name(point, snapshot),
                    alert_id=circuit_id,
                    alert_source="circuit",
                    current_a=alert.current_a,
                    breaker_rating_a=point.panel_rating_a,
                    monitoring_rating_a=alert.monitoring_rating_a,
                    monitoring_rating_user_set=point.rating_user_set,
                    package_ratings_a=package_ratings_payload(point.package_ratings_a),
                    package_ratings_state=point.package_ratings_state,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                    entity_id=self._resolve_circuit_entity_id(circuit_id),
                )

            alert = check_continuous(state, current, rating, cont_pct, window_m, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=point.name,
                    display_name=self._display_name(point, snapshot),
                    alert_id=circuit_id,
                    alert_source="circuit",
                    current_a=alert.current_a,
                    breaker_rating_a=point.panel_rating_a,
                    monitoring_rating_a=alert.monitoring_rating_a,
                    monitoring_rating_user_set=point.rating_user_set,
                    package_ratings_a=package_ratings_payload(point.package_ratings_a),
                    package_ratings_state=point.package_ratings_state,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                    window_duration_s=alert.window_duration_s,
                    over_threshold_since=alert.over_threshold_since,
                    entity_id=self._resolve_circuit_entity_id(circuit_id),
                )

    def _display_name(self, point: MonitoredCircuit, snapshot: SpanPanelSnapshot) -> str:
        """Name a point as Home Assistant shows it, for the text of the alert it raises.

        Each member's device as Home Assistant shows it (`util.circuit_display_name`),
        joined as `MonitoredCircuit.name` joins the panel's names. Asked when an
        alert fires rather than held, so a device renamed since setup is named
        by its new name in the next alert.
        """
        device_registry = dr.async_get(self._hass)
        return " + ".join(
            circuit_display_name(
                device_registry,
                snapshot.serial_number,
                snapshot.circuits[member],
                member,
                config_entry_id=self._entry.entry_id,
            )
            for member in point.members
        )

    # --- Mains evaluation ---

    def _evaluate_mains(self, snapshot: SpanPanelSnapshot) -> None:
        """Evaluate thresholds for all mains legs."""
        rating = service_rating_a(snapshot)
        if rating is None:
            return

        for leg, attr in _MAINS_CURRENT_ATTRS.items():
            current_val = getattr(snapshot, attr, None)
            if current_val is None:
                continue
            if is_monitoring_disabled(self._mains_overrides.get(leg, {})):
                continue

            state = self._mains_states.setdefault(leg, MonitoredPointState())
            current = abs(current_val)
            state.last_current_a = current

            cont_pct, spike_pct, window_m, cooldown_m = resolve_thresholds(
                self._mains_overrides.get(leg, {}), self.get_global_settings()
            )

            # The mains has no device of its own, so its text and its event both
            # name the leg by its own name.
            leg_name = f"Mains {leg.replace('_', ' ').title()}"

            alert = check_spike(state, current, rating, spike_pct, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=leg_name,
                    display_name=leg_name,
                    alert_id=leg,
                    alert_source="mains",
                    current_a=alert.current_a,
                    # The mains takes no package ratings: it is judged against its panel rating.
                    breaker_rating_a=rating,
                    monitoring_rating_a=alert.monitoring_rating_a,
                    monitoring_rating_user_set=False,
                    package_ratings_a=None,
                    package_ratings_state=None,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                    entity_id=self._resolve_mains_entity_id(leg),
                )

            alert = check_continuous(state, current, rating, cont_pct, window_m, cooldown_m)
            if alert is not None:
                dispatch_alert(
                    self._hass,
                    self.get_global_settings(),
                    alert_type=alert.alert_type,
                    alert_name=leg_name,
                    display_name=leg_name,
                    alert_id=leg,
                    alert_source="mains",
                    current_a=alert.current_a,
                    # The mains takes no package ratings: it is judged against its panel rating.
                    breaker_rating_a=rating,
                    monitoring_rating_a=alert.monitoring_rating_a,
                    monitoring_rating_user_set=False,
                    package_ratings_a=None,
                    package_ratings_state=None,
                    threshold_pct=alert.threshold_pct,
                    utilization_pct=alert.utilization_pct,
                    panel_serial=snapshot.serial_number,
                    window_duration_s=alert.window_duration_s,
                    over_threshold_since=alert.over_threshold_since,
                    entity_id=self._resolve_mains_entity_id(leg),
                )


def _store_for(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredMonitoring]:
    """Return the store holding one panel's monitoring settings."""
    return Store(hass, _STORAGE_VERSION, f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}")


async def async_adopt_legacy_enable_option(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move an entry's legacy `enable_current_monitoring` option into the monitoring store, once.

    Monitoring is switched on and off in the store alone, which turning it off
    writes; an option left in the entry turned it on again at every setup, so an
    entry holding it could not stay off across a restart. Setup calls this
    before it reads the store. Where the store already holds a flag, that flag
    is the user's last word and the option carries nothing; otherwise an option
    that is on is recorded as on, and one that is off needs no record, since a
    store holding no flag is off. Either way the option leaves the entry, through
    a write that asks for no reload, because this setup has already applied it.
    """
    if _LEGACY_ENABLE_OPTION not in entry.options:
        return
    store = _store_for(hass, entry)
    data = _read_stored(await store.async_load(), entry) or StoredMonitoring()
    if "enabled" not in data and entry.options[_LEGACY_ENABLE_OPTION]:
        data["enabled"] = True
        await store.async_save(data)
    async_update_entry_options_applied(
        hass,
        entry,
        {key: value for key, value in entry.options.items() if key != _LEGACY_ENABLE_OPTION},
    )


async def async_forget_monitoring(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the monitoring settings when the config entry is removed.

    Keyed by the entry's id, so no entry would ever load them again: left
    behind, the file is one nothing owns. The overrides in it are keyed by
    circuit ids, which a later entry for the same panel would carry too, and
    whose user removed the panel to be rid of them.
    """
    await _store_for(hass, entry).async_remove()


def _read_stored(stored: object, entry: ConfigEntry) -> StoredMonitoring | None:
    """Read the monitoring store off disk, keeping what has the shape it is saved in.

    None for nothing stored, or for anything but an object. `StoredMonitoring`
    is a compile-time claim, and the disk violates it freely: a hand edit, a
    partly restored backup. Each part is read on its own, and one of the wrong
    shape is dropped with a WARNING rather than failing the setup that reads
    it or a snapshot that judges by it later: `enabled` only as a boolean,
    each override and the global settings only as an object, and in them only
    an integer, boolean or string value under a string key, which is all a
    setting holds. A dropped override leaves its point on the global settings.
    """
    if not isinstance(stored, dict):
        return None
    data = StoredMonitoring()
    enabled = stored.get("enabled")
    if isinstance(enabled, bool):
        data["enabled"] = enabled
    elif enabled is not None:
        _warn_dropped(entry, "enabled", enabled)
    for part in ("circuit_overrides", "mains_overrides"):
        overrides = stored.get(part)
        if overrides is None:
            continue
        if not isinstance(overrides, dict):
            _warn_dropped(entry, part, overrides)
            continue
        kept: dict[str, MonitoringSettings] = {}
        for point_id, override in overrides.items():
            settings = _read_settings(override)
            if isinstance(point_id, str) and settings is not None:
                kept[point_id] = settings
            else:
                _warn_dropped(entry, f"{part}.{point_id}", override)
        data[part] = kept
    global_settings = stored.get("global")
    if global_settings is not None:
        settings = _read_settings(global_settings)
        if settings is not None:
            data["global"] = settings
        else:
            _warn_dropped(entry, "global", global_settings)
    return data


def _read_settings(value: object) -> MonitoringSettings | None:
    """Read one override or the global settings: an object of setting values, or None.

    A value of another type inside one is dropped alone, so the rest of the
    override still applies; a setting holds only an integer, a boolean or a
    string, and a boolean is an `int`.
    """
    if not isinstance(value, dict):
        return None
    return {
        key: setting
        for key, setting in value.items()
        if isinstance(key, str) and isinstance(setting, (int, str))
    }


def _warn_dropped(entry: ConfigEntry, part: str, value: object) -> None:
    """Log, at WARNING, a part of the monitoring store dropped for its shape."""
    _LOGGER.warning(
        "Ignoring %s in the monitoring settings of %s: found %s, which is not what is saved there",
        part,
        entry.entry_id,
        type(value).__name__,
    )


async def async_rekey_circuit_ids(
    hass: HomeAssistant, entry: ConfigEntry, mapping: Mapping[str, str]
) -> int:
    """Move the stored circuit overrides from old circuit ids to new ones, on disk.

    Run by the device-id migration before any monitor loads the store. A shared
    meter's override is held under its first member, and its key moves to that
    member's new id; the new ids keep the members' order, and should a group's
    first member differ, `_fold_member_overrides` moves the override onto it as
    the monitor loads the store (`async_start`), as it does for any member's. A
    value already under the new id wins (`device_id_migration.rekeyed`). Every
    other part of the store is written back as the monitor reads it
    (`_read_stored`). Written only when something moved. Returns how many
    overrides moved.
    """
    store = _store_for(hass, entry)
    data = _read_stored(await store.async_load(), entry)
    if data is None:
        return 0
    rekeyed_overrides, moved = rekeyed(data.get("circuit_overrides", {}), mapping)
    if moved:
        data["circuit_overrides"] = rekeyed_overrides
        await store.async_save(data)
    return moved
