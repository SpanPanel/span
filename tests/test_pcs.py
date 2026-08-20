"""The Power Control System, surfaced as three entities and fifteen attributes.

Thirteen of the attributes are on the effective-limit sensor and two are on
every circuit's power sensor.

`energy.ebus.capability.pcs` 0.3 is the largest capability the enclosure
publishes — sixteen properties on the panel, two on every circuit — and nothing
read a byte of it. The capability itself says what to surface: it reconciles
every active import constraint to one enforced current limit, and "what `pcs`
publishes is the **result**: the effective `import-limit` and the
`binding-constraint`". So the result is the entities, and the arbitration behind
it is their attributes.

**The capture is a PCS that is switched off, and every test here is written
around that.** Every limit is `0.0`, every enablement `UNCONFIGURED`, every
boolean `false`. Uniform data makes an assertion cheap to satisfy for the wrong
reason — an entity wired to the neighbouring property reports the identical
value, and an entity hardcoding a zero agrees with the wire by accident. So no
state or attribute here is asserted against the captured values. Presence is
checked against the capture; every *reading* is proved by republishing a value
that differs from the captured one and from every sibling's, and the sensor's
whole attribute dictionary is compared at once, so a wrong wiring shows up as a
value under the wrong name rather than as a value that happens to match.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.binary_sensor import (
    PCS_ACTIVE_SENSOR,
    SpanPanelBinarySensor,
    SpanPanelBinarySensorEntityDescription,
)
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    DerivedReason,
    Producibility,
)
from custom_components.span_panel.helpers import detect_capabilities, has_pcs
from custom_components.span_panel.sensor import create_circuit_sensors, create_pcs_sensors
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
from custom_components.span_panel.sensor_definitions import (
    PCS_BINDING_CONSTRAINT_OPTIONS,
    PCS_CONSTRAINT_FAMILIES,
    PCS_SENSORS,
)
from custom_components.span_panel.sensor_panel import SpanPcsSensor
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_HOST, UnitOfElectricCurrent
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.typing import StateType

from .adapter_fixtures import SCHEMA_ONE_PANEL, schema_one_snapshot, schema_one_tree
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

from pytest_homeassistant_custom_component.common import MockConfigEntry

NODE = "pcs"

IMPORT_LIMIT_KEY = "pcs_import_limit"
BINDING_CONSTRAINT_KEY = "pcs_binding_constraint"

# A circuit the capture reports as PCS-managed, and one it reports as not.
_SOURCES = ("feed", "operator", "off-grid", "requested")
"""The catalog's four amps-native constraint classes, in the order it names them."""

MANAGED_CIRCUIT = "0ab966b95f92a6a51ec548485aa85f54"
UNMANAGED_CIRCUIT = "573066aaddd7b75114c4563ce3af18c4"

# One republished value per panel property, every one different from the
# captured value *and* from every sibling's. That distinctness is the whole
# apparatus of this module: against a capture of zeros, `false` and
# `UNCONFIGURED`, an attribute asserted to equal what was published is satisfied
# by eleven wrong wirings as easily as by the right one.
#
# Two of the sixteen cannot be made unique, and saying why matters more than
# hiding it. The enablement enum has four members and one of them is what the
# capture already publishes, so four families can differ from the capture and
# from each other in at most three ways. Every `-active` flag is worse: all four
# are `false` in the capture, so all four must be republished `true` to be
# testing anything at all.
#
# Telling those apart is what `test_republishing_any_property_moves_only_what_reads_it`
# is for. It flips one property at a time on top of this state and requires
# exactly one observable to move, which catches a cross-wiring the dictionary
# comparison below cannot see.
_CONFIGURED: dict[str, str] = {
    "enabled": "true",
    "active": "true",
    "import-limit": "37.5",
    "binding-constraint": "DOE",
    "feed-import-limit": "100.0",
    "feed-import-limit-enablement": "ENABLED",
    "feed-import-limit-active": "true",
    "operator-import-limit": "62.5",
    "operator-import-limit-enablement": "DISABLED",
    "operator-import-limit-active": "true",
    "off-grid-import-limit": "25.0",
    "off-grid-import-limit-enablement": "UNSPECIFIED",
    "off-grid-import-limit-active": "true",
    "requested-import-limit": "80.0",
    "requested-import-limit-enablement": "ENABLED",
    "requested-import-limit-active": "true",
}

# What `pcs_import_limit` must publish beside its state for `_CONFIGURED`. The
# effective limit and the binding constraint are absent: those are the two
# entities, and repeating an entity's state as its own attribute would be a
# second copy to keep in step.
_CONFIGURED_ATTRIBUTES: dict[str, float | str | bool] = {
    "pcs_enabled": True,
    "feed_import_limit": 100.0,
    "feed_import_limit_enablement": "ENABLED",
    "feed_import_limit_active": True,
    "operator_import_limit": 62.5,
    "operator_import_limit_enablement": "DISABLED",
    "operator_import_limit_active": True,
    "off_grid_import_limit": 25.0,
    "off_grid_import_limit_enablement": "UNSPECIFIED",
    "off_grid_import_limit_active": True,
    "requested_import_limit": 80.0,
    "requested_import_limit_enablement": "ENABLED",
    "requested_import_limit_active": True,
}


@pytest.fixture(autouse=True)
def _mock_entity_registry() -> Any:
    """Patch entity registry lookups used during sensor construction."""
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    with patch(
        "custom_components.span_panel.sensor_base.er.async_get",
        return_value=registry,
    ):
        yield registry


def _coordinator(snapshot: SpanPanelSnapshot) -> MagicMock:
    """A coordinator-like mock carrying one snapshot."""
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.hass = MagicMock()
    coordinator.panel_offline = False
    coordinator.unresolved_paths = frozenset()
    coordinator.config_entry = MockConfigEntry(
        domain="span_panel",
        data={CONF_HOST: "192.168.1.50"},
        options={},
        title="SPAN Panel",
        unique_id=snapshot.serial_number,
    )
    coordinator.config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
    )
    return coordinator


def _published(property_id: str, device_id: str = SCHEMA_ONE_PANEL) -> str:
    """What the capture publishes on one PCS topic, or fail saying it does not."""
    value = schema_one_tree()[device_id].get(f"{NODE}/{property_id}")
    assert value is not None, f"{device_id} publishes no {NODE}/{property_id} in the capture"
    return value


def _republishing(device_id: str = SCHEMA_ONE_PANEL, **properties: str) -> SpanPanelSnapshot:
    """A snapshot from the capture with some PCS topics rewritten."""
    tree = schema_one_tree()
    for property_id, value in properties.items():
        tree[device_id][f"{NODE}/{property_id.replace('_', '-')}"] = value
    return schema_one_snapshot(tree)


def _configured() -> SpanPanelSnapshot:
    """A snapshot of a PCS that is switched on, every property distinct.

    The capture cannot be used for a reading test — see the module docstring —
    so this is the state the reading tests run against.
    """
    tree = schema_one_tree()
    for property_id, value in _CONFIGURED.items():
        tree[SCHEMA_ONE_PANEL][f"{NODE}/{property_id}"] = value
    return schema_one_snapshot(tree)


def _without(*property_ids: str, device_id: str = SCHEMA_ONE_PANEL) -> SpanPanelSnapshot:
    """A snapshot from a panel that stopped publishing (and declaring) properties."""
    tree = schema_one_tree()
    description = json.loads(tree[device_id]["$description"])
    for property_id in property_ids:
        del tree[device_id][f"{NODE}/{property_id}"]
        del description["nodes"][NODE]["properties"][property_id]
    tree[device_id]["$description"] = json.dumps(description)
    return schema_one_snapshot(tree)


def _without_node(device_id: str = SCHEMA_ONE_PANEL) -> SpanPanelSnapshot:
    """A snapshot from a capture with no `pcs` node on one device at all."""
    tree = schema_one_tree()
    for topic in [t for t in tree[device_id] if t.startswith(f"{NODE}/")]:
        del tree[device_id][topic]
    description = json.loads(tree[device_id]["$description"])
    del description["nodes"][NODE]
    tree[device_id]["$description"] = json.dumps(description)
    return schema_one_snapshot(tree)


def _sensors(snapshot: SpanPanelSnapshot) -> dict[str, SpanPcsSensor]:
    """Whatever the platform creates for this snapshot, keyed by description key."""
    created = create_pcs_sensors(_coordinator(snapshot), snapshot)
    return {sensor.entity_description.key: sensor for sensor in created}


def _state(snapshot: SpanPanelSnapshot, key: str) -> StateType | date | datetime | Decimal:
    """The state one PCS sensor reports for a snapshot.

    Typed as `SensorEntity.native_value` is rather than narrowed to what these
    two sensors happen to report: narrowing here would be the test asserting its
    own expectation twice, once in the annotation and once in the body.
    """
    sensor = _sensors(snapshot)[key]
    sensor._update_native_value()
    return sensor.native_value


def _attributes(snapshot: SpanPanelSnapshot, key: str) -> dict[str, Any]:
    return _sensors(snapshot)[key].extra_state_attributes or {}


def _binary(
    snapshot: SpanPanelSnapshot,
) -> SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription]:
    """The `pcs_active` binary sensor, updated from the snapshot."""
    sensor: SpanPanelBinarySensor[SpanPanelBinarySensorEntityDescription] = SpanPanelBinarySensor(
        _coordinator(snapshot), PCS_ACTIVE_SENSOR
    )
    sensor.async_write_ha_state = MagicMock()
    sensor._handle_coordinator_update()
    return sensor


def _circuit_power_sensor(snapshot: SpanPanelSnapshot, circuit_id: str) -> SpanCircuitPowerSensor:
    """The power sensor for one circuit, which is where PCS participation lands."""
    coordinator = _coordinator(snapshot)
    created = create_circuit_sensors(coordinator, snapshot, coordinator.config_entry)
    for sensor in created:
        if isinstance(sensor, SpanCircuitPowerSensor) and sensor.circuit_id == circuit_id:
            return sensor
    raise AssertionError(f"no power sensor created for circuit {circuit_id}")


def _circuit_attributes(snapshot: SpanPanelSnapshot, circuit_id: str) -> dict[str, Any]:
    return _circuit_power_sensor(snapshot, circuit_id).extra_state_attributes or {}


# ---------------------------------------------------------------------------
# The premise: what the capture carries, and why it cannot prove a reading
# ---------------------------------------------------------------------------


def test_the_capture_publishes_the_whole_system_surface() -> None:
    """Guard the premise for every test below. Sixteen properties, each declared
    and published; a capture that dropped one would make its absence test
    vacuous rather than failing."""
    declared = json.loads(schema_one_tree()[SCHEMA_ONE_PANEL]["$description"])["nodes"][NODE]

    assert set(declared["properties"]) == set(_CONFIGURED)
    for property_id in _CONFIGURED:
        assert _published(property_id)


def test_the_capture_is_a_pcs_that_is_switched_off() -> None:
    """The fact this module is written around, asserted rather than assumed.

    Every limit zero, every enablement `UNCONFIGURED`, every boolean false. That
    is why no reading below is proved by comparing an entity against the
    capture: fifteen wrong wirings report the same value as the right one. Were
    the capture ever retaken with a configured PCS, this fails first and says
    so, rather than the reading tests silently becoming redundant.
    """
    assert _published("enabled") == "false"
    assert _published("active") == "false"
    assert _published("binding-constraint") == "NONE"
    assert {float(_published(f"{source}-import-limit")) for source in _SOURCES} == {0.0}
    assert {_published(f"{source}-import-limit-enablement") for source in _SOURCES} == {
        "UNCONFIGURED"
    }
    assert float(_published("import-limit")) == 0.0


def test_the_republished_values_are_all_different_from_each_other() -> None:
    """Guard the apparatus itself.

    Every reading test below rests on `_CONFIGURED` giving each property a value
    no sibling shares — that is what turns "the attribute equals what was
    published" into a statement about which property it came from. Two entries
    accidentally made equal would silently weaken every one of them.
    """
    numeric = [value for value in _CONFIGURED.values() if value.replace(".", "").isdigit()]
    assert len(set(numeric)) == len(numeric)

    # As distinct as the enum allows: four families, four members, one of which
    # is the value the capture already publishes.
    declared = json.loads(schema_one_tree()[SCHEMA_ONE_PANEL]["$description"])["nodes"][NODE][
        "properties"
    ]["feed-import-limit-enablement"]["format"]
    enablements = {_CONFIGURED[f"{source}-import-limit-enablement"] for source in _SOURCES}
    assert len(enablements) == len(declared.split(",")) - 1

    for property_id, value in _CONFIGURED.items():
        assert value != _published(property_id), f"{property_id} is not being changed"


# ---------------------------------------------------------------------------
# The effective limit, and the arbitration it carries as attributes
# ---------------------------------------------------------------------------


def test_the_import_limit_sensor_reports_the_effective_limit() -> None:
    """The headline reading, on a PCS that is switched on."""
    assert _state(_configured(), IMPORT_LIMIT_KEY) == float(_CONFIGURED["import-limit"])


def test_the_effective_limit_is_not_any_of_its_inputs() -> None:
    """The capability calls `import-limit` the arbitration *result*. A sensor
    wired to the FSR would be plausible and wrong, so the four inputs are
    republished to one shared value the result does not share."""
    snapshot = _republishing(
        **{
            "import_limit": "12.5",
            "feed_import_limit": "99.0",
            "operator_import_limit": "99.0",
            "off_grid_import_limit": "99.0",
            "requested_import_limit": "99.0",
        }
    )

    assert _state(snapshot, IMPORT_LIMIT_KEY) == 12.5


def test_republishing_the_effective_limit_moves_the_sensor() -> None:
    """The mutation proof, twice over: two values, neither the captured zero."""
    assert _state(_republishing(import_limit="15.0"), IMPORT_LIMIT_KEY) == 15.0
    assert _state(_republishing(import_limit="16.5"), IMPORT_LIMIT_KEY) == 16.5


def test_zero_amps_is_a_reading_and_not_an_absence() -> None:
    """The captured state, and a real one: the PCS is permitting no import at
    all. An entity that treated it as missing would go blank exactly when the
    panel is most restrictive."""
    assert _state(schema_one_snapshot(), IMPORT_LIMIT_KEY) == 0.0


def test_the_arbitration_rides_as_attributes_on_the_limit() -> None:
    """The whole attribute dictionary at once, against a PCS where every value
    is distinct.

    Compared as a dictionary rather than key by key, deliberately. Twelve of
    these are the same shape and the capture makes them identical, so the
    failure worth catching is a value landing under the wrong name — which a
    per-key assertion on a matching value cannot see and this does.
    """
    assert _attributes(_configured(), IMPORT_LIMIT_KEY) == _CONFIGURED_ATTRIBUTES


def test_the_attributes_are_the_inputs_and_not_the_result() -> None:
    """`import-limit` and `binding-constraint` are the two entities. Repeating
    either as an attribute of the other would be a second copy to keep in
    step."""
    attributes = _attributes(_configured(), IMPORT_LIMIT_KEY)

    assert "import_limit" not in attributes
    assert "binding_constraint" not in attributes


@pytest.mark.parametrize("family", PCS_CONSTRAINT_FAMILIES, ids=lambda f: f.attribute)
def test_each_constraint_family_publishes_its_own_three_attributes(family: Any) -> None:
    """Every family contributes a limit, an enablement and an active flag, and
    the names extend the limit's. Asserted per family so a copied line that left
    one family reading another's fields fails naming the family."""
    snapshot = _configured()
    pcs = snapshot.pcs
    assert pcs is not None
    attributes = _attributes(snapshot, IMPORT_LIMIT_KEY)

    assert attributes[family.attribute] == family.limit_fn(pcs)
    assert attributes[f"{family.attribute}_enablement"] == family.enablement_fn(pcs)
    assert attributes[f"{family.attribute}_active"] == family.active_fn(pcs)


@pytest.mark.parametrize("property_id", sorted(_CONFIGURED))
def test_republishing_any_property_moves_only_what_reads_it(property_id: str) -> None:
    """The strongest statement this module makes, and the one the uniform
    capture demands.

    One property is republished on top of the fully-configured PCS, and the
    entity states plus the whole attribute dictionary are compared against the
    unmodified configured baseline. Exactly one thing may move. An attribute
    wired to a neighbouring property moves when it should not, which no
    assertion against the captured zeros could ever detect — every sibling
    already holds the value a wrong wiring would report.
    """
    tree = schema_one_tree()
    for name, value in _CONFIGURED.items():
        tree[SCHEMA_ONE_PANEL][f"{NODE}/{name}"] = value

    def observe(snapshot: SpanPanelSnapshot) -> dict[str, object]:
        observed: dict[str, object] = {
            f"state:{IMPORT_LIMIT_KEY}": _state(snapshot, IMPORT_LIMIT_KEY),
            f"state:{BINDING_CONSTRAINT_KEY}": _state(snapshot, BINDING_CONSTRAINT_KEY),
            "state:pcs_active": _binary(snapshot).is_on,
        }
        observed.update(_attributes(snapshot, IMPORT_LIMIT_KEY))
        return observed

    baseline = observe(schema_one_snapshot(tree))

    # A second value for this property, again unlike anything else published.
    republished = "false" if _CONFIGURED[property_id] == "true" else "true"
    if property_id == "binding-constraint":
        republished = "OPERATOR"
    elif property_id.endswith("-enablement"):
        republished = "DISABLED" if _CONFIGURED[property_id] != "DISABLED" else "ENABLED"
    elif property_id.endswith("import-limit"):
        republished = "7.25"

    tree[SCHEMA_ONE_PANEL][f"{NODE}/{property_id}"] = republished
    after = observe(schema_one_snapshot(tree))

    moved = {name for name, value in after.items() if baseline[name] != value}
    assert len(moved) == 1, f"republishing {property_id} moved {sorted(moved)}"


@pytest.mark.parametrize("property_id", sorted(_CONFIGURED))
def test_an_unpublished_property_is_omitted_rather_than_shown_empty(property_id: str) -> None:
    """Three of the four constraint classes are `MAY`, so an absent family is
    conformant firmware. An attribute present and holding `None` would read as a
    reading the panel failed to produce, which is a different claim."""
    attributes = _attributes(_without(property_id), IMPORT_LIMIT_KEY)

    assert None not in attributes.values()


def test_a_panel_publishing_no_constraint_families_still_shows_its_limit() -> None:
    """The minimum conformant PCS: the two results, and none of the working."""
    snapshot = _without(
        *[
            f"{source}-import-limit{suffix}"
            for source in _SOURCES
            for suffix in ("", "-enablement", "-active")
        ],
        "enabled",
    )

    assert _state(snapshot, IMPORT_LIMIT_KEY) == 0.0
    assert _attributes(snapshot, IMPORT_LIMIT_KEY) == {}


# ---------------------------------------------------------------------------
# The binding constraint
# ---------------------------------------------------------------------------


def test_the_binding_constraint_is_the_published_enum_lowercased() -> None:
    """Lowercase because Home Assistant looks the state up as a translation key,
    which its own contract restricts to `[a-z0-9-_]+`."""
    assert (
        _state(_configured(), BINDING_CONSTRAINT_KEY) == _CONFIGURED["binding-constraint"].lower()
    )


@pytest.mark.parametrize("republished", ["FSR", "DOE", "VOLTAGE", "OFF_GRID", "OPERATOR"])
def test_republishing_the_binding_constraint_moves_the_sensor(republished: str) -> None:
    """Members of the enum the panel's own `$description` declares, none of them
    the captured `NONE`, so a sensor pinned to the capture cannot report any."""
    assert _state(_republishing(binding_constraint=republished), BINDING_CONSTRAINT_KEY) == (
        republished.lower()
    )


def test_the_declared_options_are_the_enum_the_panel_declares() -> None:
    """The sensor's "Possible states" against the wire's `format`, so a firmware
    that widens the enum is caught here rather than by a runtime append.

    This is the assertion against the catalog: the fixture's `$format` is a
    verbatim copy of the eight members `capabilities/pcs.md` lists, so checking
    the options against it checks them against the catalog without a third
    hand-written copy in this file.
    """
    declared = json.loads(schema_one_tree()[SCHEMA_ONE_PANEL]["$description"])["nodes"][NODE][
        "properties"
    ]["binding-constraint"]["format"]

    assert set(PCS_BINDING_CONSTRAINT_OPTIONS) == {value.lower() for value in declared.split(",")}


def test_none_is_a_binding_constraint_and_not_an_absence() -> None:
    """The captured value. `NONE` means nothing is constraining import, which is
    a state the catalog defines; reporting it as unknown would lose that."""
    assert _state(schema_one_snapshot(), BINDING_CONSTRAINT_KEY) == "none"


def test_a_binding_constraint_that_stops_arriving_goes_unknown() -> None:
    """Absence after setup is a different event from absence at setup: an entity
    a user already has cannot be deleted, so it degrades instead."""
    sensor = _sensors(schema_one_snapshot())[BINDING_CONSTRAINT_KEY]

    sensor.coordinator.data = _without("binding-constraint")
    sensor._update_native_value()

    assert sensor.native_value == "unknown"


# ---------------------------------------------------------------------------
# The activity binary sensor
# ---------------------------------------------------------------------------


def test_the_activity_sensor_follows_the_published_flag() -> None:
    """Both directions, because the capture only shows one of them."""
    assert _binary(_republishing(active="true")).is_on is True
    assert _binary(_republishing(active="false")).is_on is False


def test_activity_is_not_enablement() -> None:
    """A configured PCS spends most of its life enabled and inactive, which is
    exactly the state a sensor reading the wrong flag would misreport. Both are
    `false` in the capture, so crossing them there is invisible."""
    snapshot = _republishing(enabled="true", active="false")

    assert _binary(snapshot).is_on is False
    assert _attributes(snapshot, IMPORT_LIMIT_KEY)["pcs_enabled"] is True


def test_the_activity_sensor_goes_unknown_when_the_flag_stops_arriving() -> None:
    """`None` reaches Home Assistant as unknown, not as unavailable: the panel
    is reachable and the entity is fine, the fact simply is not being stated."""
    assert _binary(_without("active")).is_on is None


def test_the_activity_sensor_is_a_running_diagnostic() -> None:
    """A panel throttling the user's supply is a state worth seeing, but it
    belongs beside the other panel-state sensors rather than the power
    readings."""
    assert PCS_ACTIVE_SENSOR.device_class is BinarySensorDeviceClass.RUNNING
    assert PCS_ACTIVE_SENSOR.entity_category is EntityCategory.DIAGNOSTIC
    assert PCS_ACTIVE_SENSOR.entity_registry_enabled_default is True


# ---------------------------------------------------------------------------
# Absence: no node, no PCS, a flat panel
# ---------------------------------------------------------------------------


def test_the_capture_creates_all_three_entities() -> None:
    created = _sensors(schema_one_snapshot())

    assert set(created) == {IMPORT_LIMIT_KEY, BINDING_CONSTRAINT_KEY}
    assert has_pcs(schema_one_snapshot()) is True


def test_a_panel_with_no_pcs_node_gets_no_entities() -> None:
    """The presence gate, from the tree end."""
    snapshot = _without_node()

    assert has_pcs(snapshot) is False
    assert _sensors(snapshot) == {}


def test_a_flat_panel_gets_no_entities() -> None:
    """The same absence by the other route: no flat panel declares the
    capability, so the factory's default snapshot carries no PCS at all."""
    snapshot = SpanPanelSnapshotFactory.create()

    assert has_pcs(snapshot) is False
    assert _sensors(snapshot) == {}


def test_a_switched_off_pcs_still_gets_its_entities() -> None:
    """The reason the gate is the node and not a value, stated as a test.

    The capture publishes `0.0` on every limit and `false` on every flag. A
    creation rule that read those as absence would delete the entities of every
    panel whose PCS is merely unconfigured — which is the state most panels are
    in, and the state a user most wants reported.
    """
    snapshot = schema_one_snapshot()

    assert snapshot.pcs is not None
    assert snapshot.pcs.import_limit_a == 0.0
    assert set(_sensors(snapshot)) == {IMPORT_LIMIT_KEY, BINDING_CONSTRAINT_KEY}
    assert _binary(snapshot).is_on is False


def test_a_declared_node_that_publishes_nothing_still_gets_its_entities() -> None:
    """Mid-discovery, and the same rule: the panel has announced the capability
    and not yet retained its topics, so the entities exist and read unknown."""
    tree = schema_one_tree()
    for property_id in _CONFIGURED:
        del tree[SCHEMA_ONE_PANEL][f"{NODE}/{property_id}"]
    snapshot = schema_one_snapshot(tree)

    assert set(_sensors(snapshot)) == {IMPORT_LIMIT_KEY, BINDING_CONSTRAINT_KEY}
    assert _state(snapshot, IMPORT_LIMIT_KEY) is None
    assert _attributes(snapshot, IMPORT_LIMIT_KEY) == {}


def test_a_reading_that_stops_arriving_goes_unknown_rather_than_stale() -> None:
    """The last value persisting would be worse than unknown: a panel reporting
    a 40 A limit forever is indistinguishable from one still enforcing it."""
    sensor = _sensors(_configured())[IMPORT_LIMIT_KEY]

    sensor.coordinator.data = _without("import-limit")
    sensor._update_native_value()

    assert sensor.native_value is None


def test_the_pcs_appearing_is_a_capability_change() -> None:
    """Which is how a panel that gains the node mid-life gets the entities: the
    coordinator reloads on a new capability."""
    assert "pcs" not in detect_capabilities(SpanPanelSnapshotFactory.create())
    assert "pcs" in detect_capabilities(schema_one_snapshot())
    assert "pcs" not in detect_capabilities(_without_node())


# ---------------------------------------------------------------------------
# Circuit participation
# ---------------------------------------------------------------------------


def test_the_capture_publishes_participation_on_two_disagreeing_circuits() -> None:
    """Guard the premise for the circuit tests: an attribute wired to a constant
    would satisfy either circuit alone."""
    assert _published("managed", MANAGED_CIRCUIT) == "true"
    assert _published("managed", UNMANAGED_CIRCUIT) == "false"
    assert _published("priority", MANAGED_CIRCUIT) != _published("priority", UNMANAGED_CIRCUIT)


def test_a_circuit_sensor_carries_its_pcs_participation() -> None:
    """Read against the capture rather than against literals, on both circuits."""
    snapshot = schema_one_snapshot()

    managed = _circuit_attributes(snapshot, MANAGED_CIRCUIT)
    unmanaged = _circuit_attributes(snapshot, UNMANAGED_CIRCUIT)

    assert managed["pcs_managed"] is True
    assert unmanaged["pcs_managed"] is False
    assert managed["pcs_priority"] == int(_published("priority", MANAGED_CIRCUIT))
    assert unmanaged["pcs_priority"] == int(_published("priority", UNMANAGED_CIRCUIT))


def test_republishing_participation_moves_the_circuit_attributes() -> None:
    """The attribute-mutation proof. The republished priority is outside the
    range any circuit uses in the capture, so an attribute wired to another
    circuit — or to the load-shed priority beside it — cannot report it."""
    snapshot = _republishing(device_id=MANAGED_CIRCUIT, managed="false", priority="42")

    attributes = _circuit_attributes(snapshot, MANAGED_CIRCUIT)

    assert attributes["pcs_managed"] is False
    assert attributes["pcs_priority"] == 42
    # The other circuit is untouched, so a shared read would show here.
    assert _circuit_attributes(snapshot, UNMANAGED_CIRCUIT)["pcs_priority"] == int(
        _published("priority", UNMANAGED_CIRCUIT)
    )


def test_pcs_priority_is_not_the_shed_priority_beside_it() -> None:
    """Two policies on one relay, and two attributes on one sensor. The catalog
    keeps them apart because they answer different questions, and they do not
    even share a value space."""
    attributes = _circuit_attributes(schema_one_snapshot(), MANAGED_CIRCUIT)

    assert isinstance(attributes["pcs_priority"], int)
    assert isinstance(attributes["shed_priority"], str)


@pytest.mark.parametrize("property_id", ["managed", "priority"])
def test_a_circuit_that_does_not_publish_participation_omits_the_attribute(
    property_id: str,
) -> None:
    """Both properties are `MAY`. `False` and `0` would each be a claim the
    panel never made, and an attribute holding `None` reads as a failed
    reading."""
    snapshot = _without(property_id, device_id=MANAGED_CIRCUIT)

    assert f"pcs_{property_id}" not in _circuit_attributes(snapshot, MANAGED_CIRCUIT)


def test_a_circuit_outside_any_pcs_shows_neither_attribute() -> None:
    snapshot = _without_node(MANAGED_CIRCUIT)
    attributes = _circuit_attributes(snapshot, MANAGED_CIRCUIT)

    assert "pcs_managed" not in attributes
    assert "pcs_priority" not in attributes
    # The circuit's own readings are unaffected.
    assert "shed_priority" in attributes


def test_a_flat_circuit_shows_neither_attribute() -> None:
    """No flat circuit declares a `pcs` node, so the attributes simply do not
    appear rather than appearing empty."""
    circuit = SpanCircuitSnapshotFactory.create(circuit_id="1", name="Kitchen")
    snapshot = SpanPanelSnapshotFactory.create(circuits={circuit.circuit_id: circuit})

    assert circuit.pcs_managed is None
    attributes = _circuit_attributes(snapshot, circuit.circuit_id)

    assert "pcs_managed" not in attributes
    assert "pcs_priority" not in attributes


# ---------------------------------------------------------------------------
# Shape of the entities, and the conformance annotations
# ---------------------------------------------------------------------------


def test_the_import_limit_is_an_ampere_measurement_enabled_by_default() -> None:
    """A PCS throttling import is a fact about the user's electricity supply,
    not about the integration's health, so it is not filed as a diagnostic."""
    description = next(d for d in PCS_SENSORS if d.key == IMPORT_LIMIT_KEY)

    assert description.device_class is SensorDeviceClass.CURRENT
    assert description.state_class is SensorStateClass.MEASUREMENT
    assert description.native_unit_of_measurement == UnitOfElectricCurrent.AMPERE
    assert description.entity_registry_enabled_default is True
    assert description.entity_category is not EntityCategory.DIAGNOSTIC


def test_the_binding_constraint_is_an_enum_diagnostic_enabled_by_default() -> None:
    """It explains a number already on screen: short, rarely changing, and
    useless filed where nobody finds it."""
    description = next(d for d in PCS_SENSORS if d.key == BINDING_CONSTRAINT_KEY)

    assert description.device_class is SensorDeviceClass.ENUM
    assert description.entity_category is EntityCategory.DIAGNOSTIC
    assert description.entity_registry_enabled_default is True


def test_the_declared_unit_matches_what_the_panel_declares() -> None:
    """Home Assistant's unit against the tree's, for the one PCS path schema_1
    carries metadata for. A disagreement here is what the unit-mismatch Repair
    reports at runtime."""
    from .adapter_fixtures import schema_one_metadata

    description = next(d for d in PCS_SENSORS if d.key == IMPORT_LIMIT_KEY)

    assert schema_one_metadata()["pcs.import_limit_a"].unit == (
        description.native_unit_of_measurement
    )


def test_the_entities_live_on_the_main_panel_device() -> None:
    """The PCS is the enclosure's own capability, not a sub-device."""
    created = list(_sensors(schema_one_snapshot()).values())
    panel_device = _binary(schema_one_snapshot()).device_info

    assert panel_device is not None
    for sensor in created:
        assert sensor.device_info == panel_device


def test_every_pcs_entity_gets_a_distinct_unique_id() -> None:
    """They live on one device and differ only by description key, so a key
    reused from another panel sensor would silently collide."""
    created = _sensors(schema_one_snapshot())
    unique_ids = {sensor.unique_id for sensor in created.values()} | {
        _binary(schema_one_snapshot()).unique_id
    }

    assert len(unique_ids) == len(created) + 1


def test_the_three_result_paths_are_exempt_as_schema_1_only() -> None:
    """Pinned here as well as in the conformance suite because the reason is
    specific to this capability: no flat panel declares `pcs` at all, so the
    producible gate cannot be satisfied and the descriptions must stay derived.
    schema_1 maps all three, which is what makes the annotation SCHEMA_1_ONLY
    rather than NEITHER."""
    for path in ("pcs.import_limit_a", "pcs.binding_constraint", "pcs.active"):
        assert RESIDUAL_EXEMPT_PATHS[path] is Producibility.SCHEMA_1_ONLY


def test_every_attribute_read_is_enumerated_as_neither() -> None:
    """The fifteen fields nothing renders as a reading are still reads, and an
    unenumerated read is invisible to the Repair machinery — which is the exact
    hole `panel.wifi_ssid` sat in. Derived from the families rather than listed
    again, so a fifth constraint class cannot be added without one."""
    expected = {"pcs.enabled", "circuit.pcs_managed", "circuit.pcs_priority"}
    for family in PCS_CONSTRAINT_FAMILIES:
        field = f"pcs.{family.attribute}"
        expected |= {f"{field}_a", f"{field}_enablement", f"{field}_active"}

    for path in expected:
        assert RESIDUAL_EXEMPT_PATHS[path] is Producibility.NEITHER


@pytest.mark.parametrize("description", [*PCS_SENSORS, PCS_ACTIVE_SENSOR], ids=lambda d: str(d.key))
def test_each_description_names_its_field_as_well_as_its_reason(description: Any) -> None:
    """`field_path` says what the entity's value is and `derived` says why that
    path is outside the both-adapters gate. Leaving the first unset excuses the
    entity from its Repair mention and from going unavailable when the panel
    stops resolving the property."""
    assert description.derived is DerivedReason.SCHEMA_CONDITIONAL_FIELD
    assert description.field_path in RESIDUAL_EXEMPT_PATHS
