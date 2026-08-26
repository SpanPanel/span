"""Helper functions for Span Panel integration."""

from __future__ import annotations

from hashlib import sha256
import logging

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    entity_registry as er,  # noqa: F401 — re-exported for patch compatibility
)
from span_panel_api import SpanCircuitSnapshot, SpanPanelSnapshot

from .entity_resolver import (  # noqa: F401
    build_bess_unique_id_for_entry,
    build_binary_sensor_unique_id_for_entry,
    build_evse_unique_id_for_entry,
    build_mid_unique_id_for_entry,
    build_select_unique_id_for_entry,
    build_switch_unique_id_for_entry,
    construct_circuit_unique_id_for_entry,
    construct_panel_unique_id_for_entry,
    construct_synthetic_unique_id_for_entry,
    construct_unmapped_friendly_name,
    get_device_identifier_for_entry,
    resolve_evse_display_suffix,
)
from .id_builder import (  # noqa: F401
    ALL_SUFFIX_MAPPINGS,
    CIRCUIT_SUFFIX_MAPPING,
    PANEL_ENTITY_SUFFIX_MAPPING,
    PANEL_SUFFIX_MAPPING,
    build_bess_unique_id,
    build_binary_sensor_unique_id,
    build_circuit_unique_id,
    build_evse_unique_id,
    build_mid_unique_id,
    build_panel_unique_id,
    build_select_unique_id,
    build_switch_unique_id,
    construct_binary_sensor_unique_id,
    construct_circuit_unique_id,
    construct_panel_unique_id,
    construct_select_unique_id,
    construct_switch_unique_id,
    construct_synthetic_unique_id,
    get_panel_entity_suffix,
    get_suffix_from_sensor_key,
    get_user_friendly_suffix,
    is_panel_level_sensor_key,
)

__all__ = [
    "ALL_SUFFIX_MAPPINGS",
    "CIRCUIT_SUFFIX_MAPPING",
    "PANEL_ENTITY_SUFFIX_MAPPING",
    "PANEL_SUFFIX_MAPPING",
    "adopted_capability_tokens",
    "async_create_span_notification",
    "build_bess_unique_id",
    "build_mid_unique_id",
    "build_bess_unique_id_for_entry",
    "build_mid_unique_id_for_entry",
    "build_binary_sensor_unique_id",
    "build_binary_sensor_unique_id_for_entry",
    "build_circuit_unique_id",
    "build_evse_unique_id",
    "build_evse_unique_id_for_entry",
    "build_panel_unique_id",
    "build_select_unique_id",
    "build_select_unique_id_for_entry",
    "build_switch_unique_id",
    "build_switch_unique_id_for_entry",
    "construct_binary_sensor_unique_id",
    "construct_circuit_identifier_from_tabs",
    "construct_circuit_label",
    "construct_circuit_unique_id",
    "construct_circuit_unique_id_for_entry",
    "construct_panel_unique_id",
    "construct_panel_unique_id_for_entry",
    "construct_select_unique_id",
    "construct_switch_unique_id",
    "construct_synthetic_unique_id",
    "construct_synthetic_unique_id_for_entry",
    "construct_tabs_attribute",
    "construct_unmapped_friendly_name",
    "construct_voltage_attribute",
    "detect_capabilities",
    "er",
    "get_device_identifier_for_entry",
    "get_panel_entity_suffix",
    "get_suffix_from_sensor_key",
    "get_user_friendly_suffix",
    "has_bess",
    "has_evse",
    "has_mid",
    "has_power_flows",
    "has_pv",
    "has_shed_forecast",
    "is_panel_level_sensor_key",
    "resolve_evse_display_suffix",
]

_LOGGER = logging.getLogger(__name__)


async def async_create_span_notification(
    hass: HomeAssistant,
    message: str,
    title: str,
    notification_id: str,
    level: str = "warning",
) -> None:
    """Create a persistent notification for SPAN Panel issues.

    Args:
        hass: Home Assistant instance
        message: Notification message content
        title: Notification title
        notification_id: Unique identifier for the notification
        level: Severity level (info, warning, error)

    """
    _LOGGER.log(
        getattr(logging, level.upper(), logging.WARNING),
        "SPAN Panel %s: %s - %s",
        level,
        title,
        message,
    )

    async_create(
        hass,
        message=message,
        title=title,
        notification_id=notification_id,
    )


def construct_circuit_identifier_from_tabs(tabs: list[int], circuit_id: str = "") -> str:
    """Build a human-readable circuit identifier from tab positions.

    Used as a fallback when a circuit has no panel-assigned name.

    Args:
        tabs: Every panel position the breaker occupies
        circuit_id: Fallback identifier when tabs are unavailable

    Returns:
        String like "Circuit 30 32" for a two-pole breaker or "Circuit 15" for a
        single-pole one, naming every position however many there are

    """
    if tabs:
        return "Circuit " + " ".join(str(tab) for tab in sorted(tabs))
    return f"Circuit {circuit_id}"


def construct_circuit_label(circuit: SpanCircuitSnapshot | None, circuit_id: str) -> str:
    """Name a circuit the way its owner knows it, for a message addressed to them.

    The panel's own name where there is one, and the tab identifier otherwise --
    which is what the entity itself was named from, so an error names the thing
    the user is looking at rather than a wire UUID. `None` is accepted because a
    circuit can drop out of a snapshot between a command and its failure, and an
    error message is the worst possible place to raise a second error.
    """
    if circuit is not None and circuit.name:
        return circuit.name
    tabs = circuit.tabs if circuit is not None else []
    return construct_circuit_identifier_from_tabs(tabs, circuit_id)


def construct_tabs_attribute(circuit: SpanCircuitSnapshot) -> str | None:
    """Construct tabs attribute string from circuit data.

    Names every position the breaker occupies, however many that is. v1.0
    publishes them literally in ``info/spaces``; the flat schema published one
    space plus a ``dipole`` flag and its adapter recovers the pair from that. So
    this sees at most two positions on flat, and on v1.0 exactly what the panel
    reported.

    **Accepting more than two is defensive, not a fix for observed hardware.**
    SPAN has stated that its panels "are split-phase and publish only 1- or
    2-pole breakers", and no circuit on any panel captured so far occupies more
    than two positions. The ``1:4:1`` range on ``breaker/poles`` is the generic
    eBus catalog, which covers load centres that are not SPAN. What this
    replaces is a hard failure: three positions used to drop the attribute
    entirely and log that the hardware was "not valid for US electrical
    system", which is a poor way to meet input we merely have not seen.

    Args:
        circuit: SpanCircuitSnapshot object with tabs information

    Returns:
        Tabs attribute string like "tabs [30:32]", or None if no tabs
        information is available

    Examples:
        Single tab: "tabs [28]"
        Two tabs: "tabs [30:32]"
        Three tabs: "tabs [17:19:21]"
        No tabs: None

    """
    if not circuit.tabs:
        return None

    return f"tabs [{':'.join(str(tab) for tab in sorted(circuit.tabs))}]"


def construct_voltage_attribute(circuit: SpanCircuitSnapshot) -> int | None:
    """Return the nominal voltage for a circuit, inferred from its pole count.

    **Nominal, not measured, and there is nothing better to read.** The eBus
    circuit ``meter`` capability publishes current, active power and energy
    only; voltage is a panel-level quantity, published as the enclosure's
    ``meter/voltage-a`` / ``voltage-b``. No per-circuit voltage exists on the
    wire.

    **It is derived from the pole count, not from the positions.** Those are
    different claims and only the second would be unsound: the specification
    defines ``spaces`` as identifying every occupied slot "without assuming a
    numbering convention", so reading a leg out of a position *number* is
    exactly what the property exists to make unnecessary. The count comes from
    ``breaker/poles``, published outright. Given the count, SPAN supplies the
    rest -- it has stated that its panels "are split-phase and publish only 1-
    or 2-pole breakers" -- and on a split-phase service a two-pole breaker is
    line-to-line across both legs. So 240 rests on a vendor statement about
    service type, not on a layout convention.

    **Which is also why it stops at two poles.** Three or more is not a
    split-phase circuit at all -- 208V line-to-line on a three-phase wye
    service, 240V on a high-leg delta -- and nothing published distinguishes
    them. Deriving it from ``P / I`` does not rescue it either: that yields
    ``V * pf`` through a 0.1A quantiser, which on real circuits lands within 1%
    once in 27 and reads 0V for any circuit drawing standby current at zero
    real power. None means we do not know, and callers omit the attribute
    rather than publish a guess.

    Args:
        circuit: SpanCircuitSnapshot object with tabs information

    Returns:
        120 for a single-pole circuit, 240 for a two-pole one, or None when
        there is no tab information or the pole count does not determine it

    """
    if not circuit.tabs:
        return None

    if len(circuit.tabs) == 1:
        return 120
    if len(circuit.tabs) == 2:
        return 240
    return None


def has_bess(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether a BESS (battery energy storage system) is commissioned.

    Only soe_percentage is a reliable signal — the power-flows node publishes
    battery=0.0 even on panels without a commissioned BESS.
    """
    return snapshot.battery.soe_percentage is not None


def has_pv(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether PV (solar) is commissioned."""
    return snapshot.power_flow_pv is not None or any(
        c.device_type == "pv" for c in snapshot.circuits.values()
    )


def has_power_flows(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether the power-flows node is publishing data."""
    return snapshot.power_flow_site is not None


def has_mid(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether a Microgrid Interconnect Device is published.

    Unambiguous, unlike `has_bess`, which has to infer presence from
    `soe_percentage is not None` because the battery field is always there. The library
    makes `mid` optional precisely so presence needs no sentinel.

    Always false on flat firmware, which publishes no MID at all.

    DUAL-SCHEMA: this integration must serve flat and parent/child panels side by side until
    every panel has hot-loaded v1.0. Grep this token to find every place that branches on
    which schema a panel is publishing; when the flat path is finally retired, these are
    the conditionals that become unconditional and the flat branches that get deleted.
    Nothing here may *assume* parent/child before then.
    """
    return snapshot.mid is not None


def has_shed_forecast(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether the panel publishes an `energy.ebus.capability.shed-forecast` node.

    Presence of the *capability*, from presence of any of the five fields it
    fills. The library models each one as `None` when unpublished, and a panel
    with no such node fills none of them, so any non-`None` field is the node —
    there is no telemetry value that could be mistaken for it. That is why this
    reads all five rather than only the two that back sensors: a firmware
    publishing the node with a partial property set still has the capability,
    and the per-sensor gate in `create_shed_forecast_sensors` is what decides
    which entities that firmware can actually support.

    Always false on flat firmware, which publishes no such node at all.

    DUAL-SCHEMA: gated on what the snapshot carries rather than on a schema
    version, so a panel that hot-loads parent/child mid-life gains the
    capability, reloads, and the sensors appear.
    """
    return any(
        value is not None
        for value in (
            snapshot.shed_time_to_priority_shed_min,
            snapshot.shed_total_time_remaining_min,
            snapshot.shed_full_charge_time_to_priority_shed_min,
            snapshot.shed_full_charge_total_time_remaining_min,
            snapshot.shed_forecast_confidence,
        )
    )


def has_bess_telemetry(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether the BESS publishes anything about itself beyond its state of charge.

    Presence of the BESS's own `meter` and `status` capability nodes, from
    presence of the fields they fill. A BESS may be commissioned and publish
    neither: `has_bess` reads `soc/soc`, which is a different node, and every flat
    panel's BESS has no such properties at all.

    Separate from `has_bess` rather than folded into it, because the two answer
    different questions and the wrong one is silently wrong. `has_bess` decides
    whether the sub-device exists; this decides whether two of its sensors can be
    created. Merging them would either delete the metadata sensors from a BESS
    with no meter node or invent two permanently-unknown ones on it.

    DUAL-SCHEMA: gated on what the snapshot carries rather than on a schema
    version, so a BESS that gains these nodes on a firmware upgrade reaches
    `detect_capabilities`, the coordinator reloads, and the sensors appear.
    """
    return snapshot.battery.power_w is not None or snapshot.battery.communication_state is not None


def has_pcs(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether the panel runs a Power Control System.

    The one capability gate here that cannot be a value test, and the library is
    where that is enforced: `SpanPanelSnapshot.pcs` is `None` exactly when the
    enclosure declares no `pcs` node, per the capability's own rule that
    "absence of the `pcs` node means the device does not run (or participate in)
    a Power Control System".

    A value test would be wrong rather than merely awkward. Every property this
    capability publishes is legally zero — the reference capture is a PCS that
    exists and is switched off, reporting `0.0` on every limit — so reading the
    values would delete the entities of every panel whose PCS is unconfigured,
    which is the state most panels are in and the state a user most wants to
    see.

    Always false on flat firmware, which publishes no such node at all.

    DUAL-SCHEMA: gated on what the snapshot carries rather than on a schema
    version, so a panel that gains the node reaches `detect_capabilities`, the
    coordinator reloads, and the entities appear.
    """
    return snapshot.pcs is not None


def has_evse(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether an EVSE (EV charger) is commissioned.

    A circuit typed `evse` counts even before the charger appears in
    `snapshot.evse`: the panel has commissioned it and the device usually
    arrives on a later snapshot. Creation still iterates `snapshot.evse`, so
    the wider signal adds no entities -- it makes the coordinator ask for a
    reload at the moment the panel first admits the charger exists.
    """
    return len(snapshot.evse) > 0 or any(
        circuit.device_type == "evse" for circuit in snapshot.circuits.values()
    )


def has_der_link_health(snapshot: SpanPanelSnapshot) -> bool:
    """Detect whether the panel reports the link to any circuit-fed DER.

    Presence of the *record*, from presence of the field it fills. The library
    models `connected` as `None` for a DER no circuit claims, and the enum a
    circuit does publish is `OK,LOST,DEGRADED` with no UNKNOWN member — so an
    absent property is the only way the panel can say it does not know, and
    `None` is the only reading that can mean it.

    A value gate would be wrong here in a way it is not for the PCS: the
    question is not what the link is doing but whether the panel says anything
    about it, and `distribution-enclosure.md` makes silence the normal state for
    a circuit that feeds an ordinary load rather than a DER.

    Coarse on purpose. This decides whether a *reload* is worth requesting, not
    which entities exist — the per-DER gate in `binary_sensor.async_setup_entry`
    does that, because two chargers can be fed by two circuits of which only one
    publishes the record.

    Always false on flat firmware, which publishes this only for the BESS, and
    reaches `battery.connected` rather than either field here.

    DUAL-SCHEMA: gated on what the snapshot carries rather than on a schema
    version, so a panel that starts publishing the record reaches
    `detect_capabilities`, the coordinator reloads, and the sensors appear.
    """
    return snapshot.pv.connected is not None or any(
        evse.connected is not None for evse in snapshot.evse.values()
    )


def _digest(identity: str) -> str:
    """Return a short, stable digest of one wire identity.

    Not a security boundary and not trying to be one — this is the same rule
    `diagnostics.AdoptedDeviceRow` states and applies to `parent`: a device id can
    embed a serial, because producers derive a DER's id preferring a serial over a
    default slug, which is why this repository holds PV's `info/serial-number`
    unvalued. The tokens below are the only capability names that carry wire
    identity, and the capability set is *logged at INFO* by
    `_check_capability_change` whenever it expands. A serial does not belong in
    a line that ends up pasted into an issue.

    Everything the reload trigger needs survives the digest: it compares tokens for
    equality and never reads one, so a stable digest is the identity as far as
    that comparison is concerned. What is lost is only a maintainer's ability to
    read *which* device appeared out of the log line, and the diagnostics download
    already answers that with the device type and counts.
    """
    return sha256(identity.encode("utf-8")).hexdigest()[:12]


def adopted_capability_tokens(snapshot: SpanPanelSnapshot) -> frozenset[str]:
    """One token per adopted device and per vendor extension property.

    Vendor extensibility is the one part of the snapshot whose vocabulary this
    integration cannot enumerate in advance, so it cannot be reduced to a named
    flag the way `bess` or `pcs` are. It reaches the reload trigger as its
    identities, digested — see `_digest` for why the raw ids must not appear here.

    **One token each rather than one hash of the set.** The trigger fires on set
    *expansion* (`current - known`), and a hash of a shrinking set is as "new" as
    a hash of a growing one — so a single token would reload the integration when
    a vendor device left the tree, and flap it on a device that came and went.
    Per-identity tokens make the expansion-only rule hold for free: a device that
    arrives adds a token, a device that leaves removes one, and only the first is
    a reload.

    The extension token digests its **instance key** and keeps its subject kind
    and wire path in the clear. The key is an identity — an EVSE's node id, a
    circuit's uuid — and can carry the same serial a device id can; `battery` and
    `meter/cell-temperature` are vendor vocabulary that names no install.

    Keyed on the wire identity rather than on the value: a reading changing is
    every refresh, and the question here is only whether an entity that could not
    be created at setup now can.
    """
    return frozenset(
        [f"adopted:{_digest(device.device_id)}" for device in snapshot.adopted_devices]
        + [
            f"extension:{row.subject.kind}:"
            f"{'' if row.subject.instance_key is None else _digest(row.subject.instance_key)}:"
            f"{row.path}"
            for row in snapshot.extension_properties
        ]
    )


def detect_capabilities(snapshot: SpanPanelSnapshot) -> frozenset[str]:
    """Derive the set of optional capabilities present in the snapshot.

    Used by the coordinator to detect when new hardware (BESS, PV, EVSE, MID) or a
    new published capability (shed-forecast) appears, and trigger a reload so new
    sensors are created. A capability is not hardware, but it reaches this the same
    way — the panel starts publishing a node it did not publish before — and the
    consequence is identical: entities that could not be created at setup now can.

    Adopted devices and vendor extension properties are in the set for exactly
    that reason. `adoption.py` opens by calling a device type nobody modelled an
    *expected* event rather than a hypothetical one, and nothing adds those
    entities dynamically — so with them excluded, a vendor device that appeared an
    hour after setup produced no device, no entity and no reload, and the user saw
    nothing at all until they restarted Home Assistant. See
    `adopted_capability_tokens` for why they are carried as identities.
    """
    caps: set[str] = set(adopted_capability_tokens(snapshot))
    if has_bess(snapshot):
        caps.add("bess")
    if has_pv(snapshot):
        caps.add("pv")
    if has_power_flows(snapshot):
        caps.add("power_flows")
    if has_evse(snapshot):
        caps.add("evse")
    if has_mid(snapshot):
        caps.add("mid")
    if has_shed_forecast(snapshot):
        caps.add("shed_forecast")
    if has_bess_telemetry(snapshot):
        caps.add("bess_telemetry")
    if has_pcs(snapshot):
        caps.add("pcs")
    if has_der_link_health(snapshot):
        caps.add("der_link_health")
    return frozenset(caps)
