"""Sensor definitions for SPAN Panel integration.

This file contains sensor definitions for all native integration sensors:
- Panel status sensors (grid state, run config, relay state, dominant power source, vendor cloud)
- Hardware status sensors (software version)
- Panel power and energy sensors (grid, feedthrough, battery, site)
- Circuit power and energy sensors
- Unmapped circuit sensors (invisible backing data)
- Battery sensor
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.helpers.entity import EntityCategory
from span_panel_api import (
    SpanBatterySnapshot,
    SpanCircuitSnapshot,
    SpanEvseSnapshot,
    SpanMidSnapshot,
    SpanPanelSnapshot,
    SpanPcsSnapshot,
)

from .field_paths import (
    DerivedReason,
    FieldPathDeclarationMixin,
    iter_source_field_declarations,
)


@dataclass(frozen=True)
class SpanPanelCircuitsRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel circuit sensors."""

    value_fn: Callable[[SpanCircuitSnapshot], float | None]


@dataclass(frozen=True, kw_only=True)
class SpanPanelCircuitsSensorEntityDescription(
    SensorEntityDescription, SpanPanelCircuitsRequiredKeysMixin
):
    """Describes a Span Panel circuit sensor entity."""


@dataclass(frozen=True)
class SpanPanelDataRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel data sensors."""

    value_fn: Callable[[SpanPanelSnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanPanelDataSensorEntityDescription(SensorEntityDescription, SpanPanelDataRequiredKeysMixin):
    """Describes a Span Panel data sensor entity."""


@dataclass(frozen=True)
class SpanPanelStatusRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel status sensors."""

    value_fn: Callable[[SpanPanelSnapshot], str]


@dataclass(frozen=True, kw_only=True)
class SpanPanelStatusSensorEntityDescription(
    SensorEntityDescription, SpanPanelStatusRequiredKeysMixin
):
    """Describes a Span Panel status sensor entity."""


@dataclass(frozen=True)
class SpanPanelBatteryRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for Span Panel battery sensors."""

    value_fn: Callable[[SpanBatterySnapshot], float | None]


@dataclass(frozen=True, kw_only=True)
class SpanPanelBatterySensorEntityDescription(
    SensorEntityDescription, SpanPanelBatteryRequiredKeysMixin
):
    """Describes a Span Panel battery sensor entity."""


# Panel data status sensor definitions
PANEL_DATA_STATUS_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="dsm_state",
        derived=DerivedReason.NO_SOURCE_FIELD,
        translation_key="dsm_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["dsm_off_grid", "dsm_on_grid", "unknown"],
        value_fn=lambda s: s.dsm_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="dsm_grid_state",
        derived=DerivedReason.NO_SOURCE_FIELD,
        translation_key="dsm_grid_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["dsm_off_grid", "dsm_on_grid", "unknown"],
        value_fn=lambda s: s.dsm_state,  # deprecated alias — reads dsm_state
    ),
    SpanPanelDataSensorEntityDescription(
        key="current_run_config",
        derived=DerivedReason.NO_SOURCE_FIELD,
        translation_key="current_run_config",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["panel_backup", "panel_off_grid", "panel_on_grid", "unknown"],
        value_fn=lambda s: s.current_run_config,
    ),
    SpanPanelDataSensorEntityDescription(
        key="main_relay_state",
        field_path="panel.main_relay_state",
        translation_key="main_relay_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["closed", "open", "unknown"],
        value_fn=lambda s: s.main_relay_state,
    ),
    SpanPanelDataSensorEntityDescription(
        key="grid_forming_entity",
        field_path="panel.dominant_power_source",
        derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
        translation_key="grid_forming_entity",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["battery", "generator", "grid", "none", "pv", "unknown"],
        value_fn=lambda s: s.dominant_power_source or "unknown",
    ),
    SpanPanelDataSensorEntityDescription(
        key="vendor_cloud",
        field_path="panel.vendor_cloud",
        translation_key="vendor_cloud",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["connected", "unconnected", "unknown"],
        value_fn=lambda s: s.vendor_cloud or "unknown",
    ),
)

# Hardware status sensor definitions
STATUS_SENSORS: tuple[SpanPanelStatusSensorEntityDescription,] = (
    SpanPanelStatusSensorEntityDescription(
        key="software_version",
        field_path="panel.firmware_version",
        translation_key="software_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.firmware_version,
    ),
)

# Unmapped circuit sensor definitions (invisible backing data)
# Keys are inline string literals preserving the v1 camelCase values for unique_id stability
UNMAPPED_SENSORS: tuple[
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
] = (
    SpanPanelCircuitsSensorEntityDescription(
        key="instantPowerW",
        field_path="circuit.instant_power_w",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda c: c.instant_power_w,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=False,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="producedEnergyWh",
        field_path="circuit.produced_energy_wh",
        name="Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda c: c.produced_energy_wh,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=False,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="consumedEnergyWh",
        field_path="circuit.consumed_energy_wh",
        name="Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda c: c.consumed_energy_wh,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=False,
    ),
)

# Battery sensor definition (conditionally created when battery data available)
BATTERY_SENSOR: SpanPanelBatterySensorEntityDescription = SpanPanelBatterySensorEntityDescription(
    key="storage_battery_percentage",
    field_path="battery.soe_percentage",
    translation_key="battery_level",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.BATTERY,
    value_fn=lambda b: b.soe_percentage,
)

# ---------------------------------------------------------------------------
# Panel diagnostic sensors (promoted from attributes)
# ---------------------------------------------------------------------------

# L1/L2 voltage sensors (v2 only, conditionally created)
L1_VOLTAGE_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="l1_voltage",
    field_path="panel.l1_voltage",
    translation_key="l1_voltage",
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    suggested_display_precision=1,
    value_fn=lambda s: s.l1_voltage,
)

L2_VOLTAGE_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="l2_voltage",
    field_path="panel.l2_voltage",
    translation_key="l2_voltage",
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    suggested_display_precision=1,
    value_fn=lambda s: s.l2_voltage,
)

# Upstream/downstream lug current sensors (v2 only, conditionally created)
UPSTREAM_L1_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="upstream_l1_current",
        field_path="panel.upstream_l1_current_a",
        translation_key="upstream_l1_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.upstream_l1_current_a,
    )
)

UPSTREAM_L2_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="upstream_l2_current",
        field_path="panel.upstream_l2_current_a",
        translation_key="upstream_l2_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.upstream_l2_current_a,
    )
)

DOWNSTREAM_L1_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="downstream_l1_current",
    field_path="panel.downstream_l1_current_a",
    # Disabled by default from 2.1.0: the eBus maintainer's r202633 conformance
    # note says this cannot be relied on -- reports the UPSTREAM service conductors, not a downstream measurement.
    # Disabled rather than removed because it shipped in 2.0.8, so an existing
    # install has it with history and entities must not vanish. This changes
    # nothing for those installs -- the default applies at first registration
    # only -- it stops NEW installs adopting it. See the delta document.
    entity_registry_enabled_default=False,
    translation_key="downstream_l1_current",
    device_class=SensorDeviceClass.CURRENT,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    entity_category=EntityCategory.DIAGNOSTIC,
    suggested_display_precision=2,
    value_fn=lambda s: s.downstream_l1_current_a,
)

DOWNSTREAM_L2_CURRENT_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="downstream_l2_current",
    field_path="panel.downstream_l2_current_a",
    # Disabled by default from 2.1.0: the eBus maintainer's r202633 conformance
    # note says this cannot be relied on -- reports the UPSTREAM service conductors, not a downstream measurement.
    # Disabled rather than removed because it shipped in 2.0.8, so an existing
    # install has it with history and entities must not vanish. This changes
    # nothing for those installs -- the default applies at first registration
    # only -- it stops NEW installs adopting it. See the delta document.
    entity_registry_enabled_default=False,
    translation_key="downstream_l2_current",
    device_class=SensorDeviceClass.CURRENT,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    entity_category=EntityCategory.DIAGNOSTIC,
    suggested_display_precision=2,
    value_fn=lambda s: s.downstream_l2_current_a,
)

# Main breaker rating sensor (v2 only, conditionally created)
MAIN_BREAKER_RATING_SENSOR: SpanPanelDataSensorEntityDescription = (
    SpanPanelDataSensorEntityDescription(
        key="main_breaker_rating",
        field_path="panel.main_breaker_rating_a",
        translation_key="main_breaker_rating",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.main_breaker_rating_a,
    )
)

# ---------------------------------------------------------------------------
# Shed forecast (v1.0 `shed-forecast`, conditionally created)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanShedForecastRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for the backup-planning forecast sensors.

    Carries a second reader beside `value_fn`, which the other panel mixins do
    not need. The capability publishes each live estimate with a
    hypothetical-full-charge twin, and the twin refines the number on screen
    rather than standing on its own — so it belongs to the sensor as an
    attribute, and which twin belongs to which sensor is a fact about the
    pairing rather than about the entity class.

    Declared here so that pairing is data. Reading it off `description.key`
    inside the entity would put a string comparison between the two halves of
    something the catalog states outright, which is how a rename silently
    swaps two plausible-looking durations.
    """

    value_fn: Callable[[SpanPanelSnapshot], int | None]

    full_charge_attribute: str
    """Attribute name the hypothetical twin is published under."""

    full_charge_fn: Callable[[SpanPanelSnapshot], int | None]
    """Reads the twin. `None` when the panel does not publish it."""


@dataclass(frozen=True, kw_only=True)
class SpanShedForecastSensorEntityDescription(
    SensorEntityDescription, SpanShedForecastRequiredKeysMixin
):
    """Describes one of the two backup-planning forecast sensors."""


SHED_FORECAST_SENSORS: tuple[
    SpanShedForecastSensorEntityDescription,
    SpanShedForecastSensorEntityDescription,
] = (
    SpanShedForecastSensorEntityDescription(
        key="time_to_priority_shed",
        field_path="panel.shed_time_to_priority_shed_min",
        derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
        translation_key="time_to_priority_shed",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda s: s.shed_time_to_priority_shed_min,
        full_charge_attribute="full_charge_time_to_priority_shed",
        full_charge_fn=lambda s: s.shed_full_charge_time_to_priority_shed_min,
    ),
    SpanShedForecastSensorEntityDescription(
        key="shed_total_time_remaining",
        field_path="panel.shed_total_time_remaining_min",
        derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
        translation_key="shed_total_time_remaining",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda s: s.shed_total_time_remaining_min,
        full_charge_attribute="full_charge_total_time_remaining",
        full_charge_fn=lambda s: s.shed_full_charge_total_time_remaining_min,
    ),
)
"""The two live estimates from `energy.ebus.capability.shed-forecast` 0.1.

**Enabled by default, and not diagnostic.** These are the numbers a user plans a
backup around — how long before the panel starts shedding circuits, how long
before the battery is spent — so they belong beside the power and energy
sensors rather than under the diagnostics fold. That is the whole argument for
surfacing them ahead of the rest of the unread v1.0 surface.

**`derived` rather than a `field_path` declaration, by the producible rule.**
The gate requires a path *both* adapters produce, and no flat panel publishes
this capability at all; `SCHEMA_CONDITIONAL_FIELD` is what that situation is
called. The paths are enumerated in `RESIDUAL_EXEMPT_PATHS` instead, annotated
`SCHEMA_1_ONLY`, and the conformance suite checks that annotation against what
the adapters actually emit — so if flat firmware ever grew the capability, the
build would fail and demand promotion rather than leaving the read ungated.

The two hypothetical-full-charge figures ride as attributes rather than as
sensors of their own. They answer "what would this installation give me from a
full battery", which moves when the hardware does and not as the battery
drains; a separate entity would put a near-constant on a graph beside the
countdown it qualifies.
"""


# ---------------------------------------------------------------------------
# Power Control System (v1.0 `pcs`, conditionally created)
# ---------------------------------------------------------------------------


class PcsConstraintFamily(NamedTuple):
    """One amps-native constraint class, and how to read its three properties.

    `energy.ebus.capability.pcs` 0.3 publishes each arbitration input as an
    identical `{<source>-import-limit, -enablement, -active}` triplet, and says
    so as a rule: a vendor "MAY publish further amps-native limits using the
    same triplet". So the four families are one shape repeated, and the
    attribute builder is written once over this table rather than four times
    over twelve field names — where a copied line would put an enablement in an
    active flag and still read plausibly.

    `attribute` is the name the limit is published under, and the enablement and
    active flags extend it. The names mirror the wire property ids so a user
    reading the catalog and a user reading the attribute list see the same
    words.
    """

    attribute: str
    limit_fn: Callable[[SpanPcsSnapshot], float | None]
    enablement_fn: Callable[[SpanPcsSnapshot], str | None]
    active_fn: Callable[[SpanPcsSnapshot], bool | None]


PCS_CONSTRAINT_FAMILIES: tuple[
    PcsConstraintFamily,
    PcsConstraintFamily,
    PcsConstraintFamily,
    PcsConstraintFamily,
] = (
    PcsConstraintFamily(
        attribute="feed_import_limit",
        limit_fn=lambda p: p.feed_import_limit_a,
        enablement_fn=lambda p: p.feed_import_limit_enablement,
        active_fn=lambda p: p.feed_import_limit_active,
    ),
    PcsConstraintFamily(
        attribute="operator_import_limit",
        limit_fn=lambda p: p.operator_import_limit_a,
        enablement_fn=lambda p: p.operator_import_limit_enablement,
        active_fn=lambda p: p.operator_import_limit_active,
    ),
    PcsConstraintFamily(
        attribute="off_grid_import_limit",
        limit_fn=lambda p: p.off_grid_import_limit_a,
        enablement_fn=lambda p: p.off_grid_import_limit_enablement,
        active_fn=lambda p: p.off_grid_import_limit_active,
    ),
    PcsConstraintFamily(
        attribute="requested_import_limit",
        limit_fn=lambda p: p.requested_import_limit_a,
        enablement_fn=lambda p: p.requested_import_limit_enablement,
        active_fn=lambda p: p.requested_import_limit_active,
    ),
)
"""The four constraint classes the catalog names, in the order it names them.

The FSR first because it is the only standing one: `feed_import_limit` is the
commissioned floor that "cannot be lost", and the other three are conditional —
an operator cap set over a fleet API, an islanded cap, and a limit the owner
asked for. A reader scanning the attributes meets the permanent one first.
"""


def _no_pcs_attributes(pcs: SpanPcsSnapshot) -> dict[str, float | str | bool]:
    """Return nothing — the default for a PCS sensor with no attributes of its own."""
    return {}


def pcs_arbitration_attributes(pcs: SpanPcsSnapshot) -> dict[str, float | str | bool]:
    """Return the arbitration inputs behind the effective import limit.

    They belong to the sensor that shows the limit itself.

    `capabilities/pcs.md` is explicit that what a PCS publishes is "the
    **result**: the effective `import-limit` and the `binding-constraint`". That
    is the entity; these are the working. Twelve of them, which is why they are
    attributes: a dashboard with twelve near-constant amperages on it is not a
    dashboard, and eleven of these move only when somebody reconfigures the
    panel.

    Each is omitted when the panel does not publish it, rather than appearing as
    `None`. Three of the four classes are `MAY`, so an absent family is
    conformant firmware; an attribute present and empty would read as a reading
    the panel failed to produce.

    `pcs_enabled` rides here rather than as its own entity because it is
    subsumed: the `pcs_active` binary sensor is the fact an automation triggers
    on, and a PCS that is not enabled cannot be active.
    """
    attributes: dict[str, float | str | bool] = {}

    if pcs.enabled is not None:
        attributes["pcs_enabled"] = pcs.enabled

    for family in PCS_CONSTRAINT_FAMILIES:
        limit = family.limit_fn(pcs)
        if limit is not None:
            attributes[family.attribute] = limit
        enablement = family.enablement_fn(pcs)
        if enablement is not None:
            attributes[f"{family.attribute}_enablement"] = enablement
        active = family.active_fn(pcs)
        if active is not None:
            attributes[f"{family.attribute}_active"] = active

    return attributes


@dataclass(frozen=True, kw_only=True)
class SpanPcsRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for the Power Control System sensors.

    Keyword-only for the reason `FieldPathDeclarationMixin` is: a mixin's fields
    flatten ahead of `EntityDescription.key`, which has no default, so a
    defaulted positional field here would make every description
    unconstructable.

    `attributes_fn` is carried on the description rather than decided inside the
    entity, for the reason the shed-forecast pairing is: the alternative is a
    comparison against `description.key` in `extra_state_attributes`, which puts
    a string match between an entity and the data it publishes and silently
    stops matching after a rename.
    """

    value_fn: Callable[[SpanPcsSnapshot], float | str | None]

    attributes_fn: Callable[[SpanPcsSnapshot], dict[str, float | str | bool]] = _no_pcs_attributes
    """What this sensor publishes beside its state. Empty for most."""


@dataclass(frozen=True, kw_only=True)
class SpanPcsSensorEntityDescription(SensorEntityDescription, SpanPcsRequiredKeysMixin):
    """Describes a Power Control System sensor entity."""


PCS_BINDING_CONSTRAINT_OPTIONS: tuple[str, ...] = (
    "fsr",
    "doe",
    "voltage",
    "off_grid",
    "requested",
    "operator",
    "none",
    "unknown",
)
"""`binding-constraint`'s enum, lowercased for Home Assistant's state keys.

The catalog's eight members, in its order. Publishers MAY extend the enum
through the property's Homie `$format`, so this is the interoperable core rather
than a closed set — a vendor value arrives as a state Home Assistant does not
recognise, which is a visible gap rather than a silent re-labelling.
"""


PCS_SENSORS: tuple[SpanPcsSensorEntityDescription, SpanPcsSensorEntityDescription] = (
    SpanPcsSensorEntityDescription(
        key="pcs_import_limit",
        field_path="pcs.import_limit_a",
        derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
        translation_key="pcs_import_limit",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        value_fn=lambda p: p.import_limit_a,
        attributes_fn=pcs_arbitration_attributes,
    ),
    SpanPcsSensorEntityDescription(
        key="pcs_binding_constraint",
        field_path="pcs.binding_constraint",
        derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
        translation_key="pcs_binding_constraint",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=list(PCS_BINDING_CONSTRAINT_OPTIONS),
        value_fn=lambda p: None if p.binding_constraint is None else p.binding_constraint.lower(),
    ),
)
"""What the enclosure's Power Control System publishes as a result.

**The effective limit is the entity, and the arbitration is its attributes.**
The capability says the PCS reconciles every active import constraint — some of
them in watts on `doe`, some in volts on `voltage-response` — to one enforced
current limit, and that "what `pcs` publishes is the **result**". So the result
is what gets an entity a user can graph and alarm on. The twelve amps-native
inputs behind it are on that entity as attributes: they explain a number rather
than being numbers anyone watches, and eleven of them move only on
reconfiguration.

`pcs_binding_constraint` is the second half of that result and the reason the
first is interpretable — it names which constraint class won the `min()`, which
is the difference between "the panel is limiting me to 40 A" and "the panel is
limiting me to 40 A *because the utility sent an envelope*". Diagnostic and
enabled by default: it is short, it changes rarely, and it is useless filed
where nobody finds it.

**Enabled by default and not diagnostic, for the limit.** A PCS actively
throttling import is a fact about the user's electricity supply, not about the
integration's health.

**`derived` as well as `field_path`, by the producible rule.** No flat panel
publishes `energy.ebus.capability.pcs` at all, so the both-adapters gate cannot
be satisfied; the paths are enumerated in `RESIDUAL_EXEMPT_PATHS` as
`SCHEMA_1_ONLY`, which schema_1's metadata rows earn them and which buys
`pcs_import_limit` unit validation against the panel's own `$description`.
`field_path` still names the source, which is what gives each sensor its Repair
mention and its unavailability when the panel stops resolving the property.
"""


# ---------------------------------------------------------------------------
# Circuit diagnostic sensors (promoted from attributes)
# ---------------------------------------------------------------------------

# Per-circuit current sensor (v2 only, conditionally created)
CIRCUIT_CURRENT_SENSOR: SpanPanelCircuitsSensorEntityDescription = (
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_current",
        field_path="circuit.current_a",
        name="Current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        value_fn=lambda c: c.current_a,
    )
)

# Per-circuit breaker rating sensor (v2 only, conditionally created)
CIRCUIT_BREAKER_RATING_SENSOR: SpanPanelCircuitsSensorEntityDescription = (
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_breaker_rating",
        field_path="circuit.breaker_rating_a",
        name="Breaker Rating",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.breaker_rating_a,
    )
)

# ---------------------------------------------------------------------------
# BESS metadata sensors (on BESS sub-device)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanBessMetadataRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for BESS metadata sensors."""

    value_fn: Callable[[SpanBatterySnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanBessMetadataSensorEntityDescription(
    SensorEntityDescription, SpanBessMetadataRequiredKeysMixin
):
    """Describes a BESS metadata sensor entity."""


@dataclass(frozen=True)
class SpanMidRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for MID sensors."""

    value_fn: Callable[[SpanMidSnapshot], str | None]


@dataclass(frozen=True, kw_only=True)
class SpanMidSensorEntityDescription(SensorEntityDescription, SpanMidRequiredKeysMixin):
    """Describes a sensor on the Microgrid Interconnect Device."""


MID_SENSORS: tuple[SpanMidSensorEntityDescription, ...] = (
    SpanMidSensorEntityDescription(
        key="mid_grid_state",
        derived=DerivedReason.NO_SOURCE_FIELD,
        translation_key="mid_grid_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["up", "down", "degraded", "unknown"],
        value_fn=lambda m: (m.grid_state or "unknown").lower(),
    ),
)
"""Sensors on the MID device.

Only `grid-state` — utility-supply health, genuinely new in v1.0 with no flat
equivalent, and the one non-metadata addition the MID brings.

Islanding state and the grid-forming entity are deliberately *not* duplicated here.
Both already reach a user through entities that predate v1.0 — `dsm_state` and
`grid_forming_entity` on the panel — and those must keep their ids and their history.
Showing the same fact twice is not the benign cell of the absorb-or-surface policy;
adding a fact nobody had is.
"""


BESS_METADATA_SENSORS: tuple[
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
    SpanBessMetadataSensorEntityDescription,
] = (
    SpanBessMetadataSensorEntityDescription(
        key="vendor",
        field_path="battery.vendor_name",
        translation_key="bess_vendor",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.vendor_name,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="model",
        field_path="battery.model",
        translation_key="bess_model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.model,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="part_number",
        field_path="battery.part_number",
        translation_key="bess_part_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda b: b.part_number,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="serial_number",
        field_path="battery.serial_number",
        translation_key="bess_serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.serial_number,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="firmware_version",
        field_path="battery.software_version",
        translation_key="bess_firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: b.software_version,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="nameplate_capacity",
        field_path="battery.nameplate_capacity_kwh",
        translation_key="bess_nameplate_capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda b: b.nameplate_capacity_kwh,
    ),
    SpanBessMetadataSensorEntityDescription(
        key="soe_kwh",
        field_path="battery.soe_kwh",
        translation_key="bess_soe_kwh",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda b: b.soe_kwh,
    ),
)

BESS_TELEMETRY_SENSORS: tuple[SpanBessMetadataSensorEntityDescription,] = (
    SpanBessMetadataSensorEntityDescription(
        key="communication_state",
        field_path="battery.communication_state",
        derived=DerivedReason.SCHEMA_CONDITIONAL_FIELD,
        translation_key="bess_communication_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=["ok", "degraded", "lost", "unknown"],
        value_fn=lambda b: None if b.communication_state is None else b.communication_state.lower(),
    ),
)
"""What the BESS reports about *itself*, as opposed to what the panel reports about it.

Separate from `BESS_METADATA_SENSORS` because these are created conditionally and
those are not. Every metadata sensor exists on any commissioned BESS, filled or
empty. This one comes from a capability node a BESS may simply not have, so
absence has to mean no entity rather than a permanently unknown one, and mixing
the two rules into one tuple would mean deciding per description which applied.

Communication state is diagnostic and off by default: link health is a fault
signal, interesting when something is wrong and noise on a device card the rest
of the time.

**`bess_meter_power` was here and was withdrawn before it ever shipped.**

It read `battery.power_w`, the BESS child's own `meter/active-power`, alongside
`battery_power` reading the enclosure's `power-flows/battery` — two views of one
quantity, so that a disagreement between them would be visible rather than
hidden behind a single entity.

The eBus maintainer's r202633 conformance note (2026-08-20) established that the
two will disagree *systematically* on a real panel, for a firmware reason rather
than a panel-health one. The BESS child publishes `meter/active-power`
charge-positive where the specification requires discharge-positive, and
`build_battery`'s `_charge_positive()` negates on the assumption that the
specification holds — so `battery.power_w` inverts on that firmware. The note
calls this the one divergence a consumer cannot detect on its own: both signs
are physically plausible and the two topics are numerically identical, so
nothing in the tree contradicts itself observably.

Nothing here can catch it either. `ebus-panel-sim` 0.6.0 fixed the same
inversion in the simulator, so on this one property the simulator is now correct
where the panel is not, and every test in this repository runs against the
simulator.

Withdrawn rather than compensated, and rather than shipped documented, for the
reason the timing allows: this description had never been released, so no
install carries the entity and no history is at stake. Dropping it now is free.
Once a release creates it, the rule that an existing install's entities cannot
simply disappear makes the same decision expensive.

**Restore it when the firmware publishes the specified frame.** The correct
reading is already shipped and unaffected — `battery_power` reads
`power-flows/battery`, which the note confirms is correct as published — so
nothing is lost meanwhile except the ability to compare the two. The wire
property stays in `tests/fixtures/unread_declarations_baseline.json` with that
reason, which is what will fail when somebody surfaces it again without
revisiting this.

**`derived` as well as `field_path`, by the producible rule.** The gate wants a
path both adapters produce, and flat's BESS device class declares neither
property — so `SCHEMA_CONDITIONAL_FIELD`, with the paths enumerated in
`RESIDUAL_EXEMPT_PATHS` as `SCHEMA_1_ONLY`. `field_path` still names the source,
which is what gives the sensor its Repair mention and its unavailability when
the panel stops resolving the property.
"""

# ---------------------------------------------------------------------------
# PV metadata sensors (on main panel device)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanPVMetadataRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for PV metadata sensors."""

    value_fn: Callable[[SpanPanelSnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanPVMetadataSensorEntityDescription(
    SensorEntityDescription, SpanPVMetadataRequiredKeysMixin
):
    """Describes a PV metadata sensor entity."""


PV_METADATA_SENSORS: tuple[
    SpanPVMetadataSensorEntityDescription,
    SpanPVMetadataSensorEntityDescription,
    SpanPVMetadataSensorEntityDescription,
] = (
    SpanPVMetadataSensorEntityDescription(
        key="pv_vendor",
        field_path="pv.vendor_name",
        translation_key="pv_vendor",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.pv.vendor_name,
    ),
    SpanPVMetadataSensorEntityDescription(
        key="pv_product",
        field_path="pv.model",
        translation_key="pv_product",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.pv.model,
    ),
    SpanPVMetadataSensorEntityDescription(
        key="pv_nameplate_capacity",
        field_path="pv.nameplate_capacity_w",
        translation_key="pv_nameplate_capacity",
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.pv.nameplate_capacity_w,
    ),
)


# Panel power sensor definitions
PANEL_POWER_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="instantGridPowerW",
        field_path="panel.instant_grid_power_w",
        translation_key="instant_grid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda s: s.instant_grid_power_w,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughPowerW",
        field_path="panel.feedthrough_power_w",
        # Disabled by default from 2.1.0: the eBus maintainer's r202633 conformance
        # note says this cannot be relied on -- inverted: positive when power leaves the enclosure, where every other
        # enclosure terminal is positive inbound. Negate to compensate.
        # Disabled rather than removed because it shipped in 2.0.8, so an existing
        # install has it with history and entities must not vanish. This changes
        # nothing for those installs -- the default applies at first registration
        # only -- it stops NEW installs adopting it. See the delta document.
        entity_registry_enabled_default=False,
        translation_key="feedthrough_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda s: s.feedthrough_power_w,
    ),
)

# Battery power sensor (conditionally created when BESS is commissioned)
BATTERY_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="batteryPowerW",
    field_path="panel.power_flow_battery",
    translation_key="battery_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: (-s.power_flow_battery or 0.0) if s.power_flow_battery is not None else 0.0,
)

# PV power sensor (conditionally created when PV is commissioned)
PV_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="pvPowerW",
    field_path="panel.power_flow_pv",
    translation_key="pv_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: (-s.power_flow_pv or 0.0) if s.power_flow_pv is not None else 0.0,
)

# Grid power flow sensor (conditionally created when power-flows data is available)
GRID_POWER_FLOW_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="gridPowerFlowW",
    field_path="panel.power_flow_grid",
    translation_key="grid_power_flow",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: (-s.power_flow_grid or 0.0) if s.power_flow_grid is not None else 0.0,
)

# Site power sensor (conditionally created when power-flows data is available)
SITE_POWER_SENSOR: SpanPanelDataSensorEntityDescription = SpanPanelDataSensorEntityDescription(
    key="sitePowerW",
    field_path="panel.power_flow_site",
    translation_key="site_power",
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    device_class=SensorDeviceClass.POWER,
    value_fn=lambda s: s.power_flow_site if s.power_flow_site is not None else 0.0,
)

# Panel energy sensor definitions
PANEL_ENERGY_SENSORS: tuple[
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
    SpanPanelDataSensorEntityDescription,
] = (
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergyProducedWh",
        field_path="panel.main_meter_energy_produced_wh",
        translation_key="main_meter_produced_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.main_meter_energy_produced_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterEnergyConsumedWh",
        field_path="panel.main_meter_energy_consumed_wh",
        translation_key="main_meter_consumed_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.main_meter_energy_consumed_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyProducedWh",
        field_path="panel.feedthrough_energy_produced_wh",
        # Disabled by default from 2.1.0: the eBus maintainer's r202633 conformance
        # note says this cannot be relied on -- the difference of two unrelated counters, non-monotonic, and ~whole-panel
        # figures where the truth is zero. No transformation recovers a real value.
        # Disabled rather than removed because it shipped in 2.0.8, so an existing
        # install has it with history and entities must not vanish. This changes
        # nothing for those installs -- the default applies at first registration
        # only -- it stops NEW installs adopting it. See the delta document.
        entity_registry_enabled_default=False,
        translation_key="feedthrough_produced_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.feedthrough_energy_produced_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughEnergyConsumedWh",
        field_path="panel.feedthrough_energy_consumed_wh",
        # Disabled by default from 2.1.0: the eBus maintainer's r202633 conformance
        # note says this cannot be relied on -- the difference of two unrelated counters, non-monotonic, and ~whole-panel
        # figures where the truth is zero. No transformation recovers a real value.
        # Disabled rather than removed because it shipped in 2.0.8, so an existing
        # install has it with history and entities must not vanish. This changes
        # nothing for those installs -- the default applies at first registration
        # only -- it stops NEW installs adopting it. See the delta document.
        entity_registry_enabled_default=False,
        translation_key="feedthrough_consumed_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda s: s.feedthrough_energy_consumed_wh,
    ),
    SpanPanelDataSensorEntityDescription(
        key="mainMeterNetEnergyWh",
        derived=DerivedReason.MULTIPLE_FIELDS,
        translation_key="main_meter_net_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        entity_registry_enabled_default=False,
        value_fn=lambda s: (
            (s.main_meter_energy_consumed_wh or 0) - (s.main_meter_energy_produced_wh or 0)
        ),
    ),
    SpanPanelDataSensorEntityDescription(
        key="feedthroughNetEnergyWh",
        derived=DerivedReason.MULTIPLE_FIELDS,
        translation_key="feedthrough_net_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        entity_registry_enabled_default=False,
        value_fn=lambda s: (
            (s.feedthrough_energy_consumed_wh or 0) - (s.feedthrough_energy_produced_wh or 0)
        ),
    ),
)

# Circuit sensor definitions
CIRCUIT_SENSORS: tuple[
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
    SpanPanelCircuitsSensorEntityDescription,
] = (
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_power",
        field_path="circuit.instant_power_w",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda c: (
            (-c.instant_power_w or 0.0) if c.device_type == "pv" else c.instant_power_w
        ),
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_energy_produced",
        field_path="circuit.produced_energy_wh",
        name="Produced Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda c: c.produced_energy_wh,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_energy_consumed",
        field_path="circuit.consumed_energy_wh",
        name="Consumed Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda c: c.consumed_energy_wh,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    SpanPanelCircuitsSensorEntityDescription(
        key="circuit_energy_net",
        derived=DerivedReason.MULTIPLE_FIELDS,
        name="Net Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda c: (
            (c.produced_energy_wh or 0) - (c.consumed_energy_wh or 0)
            if c.device_type == "pv"
            else (c.consumed_energy_wh or 0) - (c.produced_energy_wh or 0)
        ),
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
)


# ---------------------------------------------------------------------------
# EVSE (EV Charger) sensor definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanEvseRequiredKeysMixin(FieldPathDeclarationMixin):
    """Required keys mixin for EVSE sensors."""

    value_fn: Callable[[SpanEvseSnapshot], float | str | None]


@dataclass(frozen=True, kw_only=True)
class SpanEvseSensorEntityDescription(SensorEntityDescription, SpanEvseRequiredKeysMixin):
    """Describes an EVSE sensor entity."""


EVSE_SENSORS: tuple[
    SpanEvseSensorEntityDescription,
    SpanEvseSensorEntityDescription,
    SpanEvseSensorEntityDescription,
    SpanEvseSensorEntityDescription,
] = (
    SpanEvseSensorEntityDescription(
        key="evse_status",
        field_path="evse.status",
        translation_key="evse_status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "available",
            "charging",
            "faulted",
            "finishing",
            "preparing",
            "reserved",
            "suspended_ev",
            "suspended_evse",
            "unavailable",
            "unknown",
        ],
        value_fn=lambda e: e.status or "unknown",
    ),
    SpanEvseSensorEntityDescription(
        key="evse_advertised_current",
        field_path="evse.advertised_current_a",
        translation_key="evse_advertised_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=1,
        value_fn=lambda e: e.advertised_current_a,
    ),
    SpanEvseSensorEntityDescription(
        key="evse_lock_state",
        field_path="evse.lock_state",
        translation_key="evse_lock_state",
        device_class=SensorDeviceClass.ENUM,
        options=["locked", "unlocked", "unknown"],
        value_fn=lambda e: e.lock_state or "unknown",
    ),
    # The charger's SKU, shaped like `bess_part_number`: build metadata, so
    # diagnostic and off by default, and a plain `field_path` because both
    # adapters map the property (`evse/part-number` on flat, `info/part-number`
    # on v1.0). It was the one unread declaration whose promotion the producible
    # gate could demand, and adding the schema_1 metadata row is what demanded it.
    SpanEvseSensorEntityDescription(
        key="evse_part_number",
        field_path="evse.part_number",
        translation_key="evse_part_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.part_number,
    ),
)


def all_sensor_descriptions() -> tuple[SensorEntityDescription, ...]:
    """Every sensor description, without deduplication.

    A tuple rather than a dict because no one key identifies a description:
    `description.key` and `field_path` are different namespaces, and neither is
    unique across the whole set — several field paths are read by two
    descriptions. Callers key by whichever suits them; see
    `sensor_descriptions_by_field_path`.
    """
    return (
        *PANEL_DATA_STATUS_SENSORS,
        *STATUS_SENSORS,
        *UNMAPPED_SENSORS,
        *MID_SENSORS,
        *BESS_METADATA_SENSORS,
        *BESS_TELEMETRY_SENSORS,
        *PCS_SENSORS,
        *PV_METADATA_SENSORS,
        *PANEL_POWER_SENSORS,
        *PANEL_ENERGY_SENSORS,
        *CIRCUIT_SENSORS,
        *EVSE_SENSORS,
        *SHED_FORECAST_SENSORS,
        BATTERY_SENSOR,
        BATTERY_POWER_SENSOR,
        PV_POWER_SENSOR,
        GRID_POWER_FLOW_SENSOR,
        SITE_POWER_SENSOR,
        L1_VOLTAGE_SENSOR,
        L2_VOLTAGE_SENSOR,
        UPSTREAM_L1_CURRENT_SENSOR,
        UPSTREAM_L2_CURRENT_SENSOR,
        DOWNSTREAM_L1_CURRENT_SENSOR,
        DOWNSTREAM_L2_CURRENT_SENSOR,
        MAIN_BREAKER_RATING_SENSOR,
        CIRCUIT_CURRENT_SENSOR,
        CIRCUIT_BREAKER_RATING_SENSOR,
    )


def sensor_descriptions_by_field_path() -> dict[str, SensorEntityDescription]:
    """Every sensor description with a source field, keyed by that field path.

    Keyed by field path because that is how the adapter keys its metadata;
    `description.key` is a different namespace and would not line up.
    Descriptions that name no field are excluded — they read several fields, or
    none, so no single path identifies them.

    A `SCHEMA_CONDITIONAL_FIELD` description does name one and is included. Its
    exemption is from the *producible* gate, and the unit its schema declares
    for the field is checkable exactly as any other's: the adapter that
    produces the row publishes a unit, and this integration's sensor declares
    one, and they can disagree.

    A few field paths are read by two descriptions (an unmapped-circuit raw key
    and its named-circuit twin), and only the first is kept. That is safe only
    while such readers agree on `native_unit_of_measurement`, which is all this
    map is consulted for; `test_readers_of_the_same_field_path_agree_on_unit`
    pins that rather than leaving it to chance.

    Lives here rather than at the call site so no consumer has to know how a
    description declares its field; `field_paths.iter_source_field_declarations`
    holds that rule.
    """
    by_field_path: dict[str, SensorEntityDescription] = {}
    for field_path, description in iter_source_field_declarations(all_sensor_descriptions()):
        by_field_path.setdefault(field_path, description)
    return by_field_path
