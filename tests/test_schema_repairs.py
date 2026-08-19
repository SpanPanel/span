from __future__ import annotations

import logging

from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.const import DOMAIN, EVENT_SCHEMA_ISSUE
from custom_components.span_panel.schema_repairs import (
    async_clear_schema_issues,
    async_sync_schema_issues,
)
from custom_components.span_panel.schema_validation import SchemaFindings, UnitMismatch

_PATH = "circuit.instant_power_w"
_UNIT_PATH = "panel.l1_voltage"

# Both Repairs claim something the user owns is broken, so nothing is raised for a
# field path no enabled entity reads. Every call below that expects an issue has
# to name the entities the finding took down.
_AFFECTED = {_PATH: ["sensor.a"]}
_UNIT_AFFECTED = {_UNIT_PATH: ["sensor.voltage"]}


@pytest.fixture
def entry(hass) -> MockConfigEntry:
    """Return a config entry added to hass. No conftest fixture exists for this."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="abc123")
    mock.add_to_hass(hass)
    return mock


def _issue_id(entry: MockConfigEntry, path: str = _PATH) -> str:
    return f"unresolved_{entry.entry_id}_{path}"


def _unit_issue_id(entry: MockConfigEntry, path: str = _PATH) -> str:
    return f"unit_mismatch_{entry.entry_id}_{path}"


async def test_unresolved_path_raises_one_issue(hass, entry) -> None:
    findings = SchemaFindings(frozenset({_PATH}), (), frozenset())
    async_sync_schema_issues(hass, entry, findings, _AFFECTED)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry))


async def test_issue_cleared_when_condition_resolves(hass, entry) -> None:
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({_PATH}), (), frozenset()), _AFFECTED
    )
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (), frozenset()), _AFFECTED)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is None


async def test_dismissal_survives_reconciliation(hass, entry) -> None:
    """Re-raise idempotently rather than delete-then-recreate.

    Deleting is the one thing that resets a dismissal, which would turn an
    accepted notice into a permanent nag.
    """
    findings = SchemaFindings(frozenset({_PATH}), (), frozenset())
    async_sync_schema_issues(hass, entry, findings, _AFFECTED)

    issue_id = _issue_id(entry)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    registry = ir.async_get(hass)
    dismissed = registry.async_get_issue(DOMAIN, issue_id).dismissed_version
    assert dismissed is not None

    for _ in range(3):
        async_sync_schema_issues(hass, entry, findings, _AFFECTED)

    assert registry.async_get_issue(DOMAIN, issue_id).dismissed_version == dismissed


async def test_dismissal_survives_a_changing_affected_entity_payload(hass, entry) -> None:
    """The update branch replaces the placeholders and keeps the dismissal.

    Stronger than the identical-payload case above: `async_get_or_create` skips
    the write entirely when nothing changed, so a delete-then-recreate bug could
    hide there. Here the placeholders genuinely differ between passes, forcing
    the update branch to run.
    """
    findings = SchemaFindings(frozenset({_PATH}), (), frozenset())
    async_sync_schema_issues(hass, entry, findings, {_PATH: ["sensor.a"]})

    issue_id = _issue_id(entry)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    registry = ir.async_get(hass)
    dismissed = registry.async_get_issue(DOMAIN, issue_id).dismissed_version

    async_sync_schema_issues(hass, entry, findings, {_PATH: ["sensor.a", "sensor.b"]})

    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue.dismissed_version == dismissed
    assert issue.translation_placeholders["count"] == "2"


async def test_distinct_paths_get_distinct_issues(hass, entry) -> None:
    """Dismissing one finding must not swallow a later, different one."""
    affected = {"a.one": ["sensor.one"], "b.two": ["sensor.two"]}
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"a.one"}), (), frozenset()), affected
    )
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"a.one", "b.two"}), (), frozenset()), affected
    )
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry, "b.two"))


async def test_a_dismissed_finding_does_not_swallow_a_later_one(hass, entry) -> None:
    """The reason one issue per (class, path) is not cosmetic.

    Dismissing an aggregate would silence every finding that joined it later,
    because the update branch preserves `dismissed_version`.
    """
    affected = {"a.one": ["sensor.one"], "b.two": ["sensor.two"]}
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"a.one"}), (), frozenset()), affected
    )
    ir.async_ignore_issue(hass, DOMAIN, _issue_id(entry, "a.one"), True)

    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"a.one", "b.two"}), (), frozenset()), affected
    )

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry, "a.one")).dismissed_version
    assert registry.async_get_issue(DOMAIN, _issue_id(entry, "b.two")).dismissed_version is None


async def test_one_entry_does_not_clear_another(hass) -> None:
    """Two panels must not delete each other's issues on every reconcile."""
    sick = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sick")
    sick.add_to_hass(hass)
    well = MockConfigEntry(domain=DOMAIN, data={}, unique_id="well")
    well.add_to_hass(hass)

    async_sync_schema_issues(
        hass, sick, SchemaFindings(frozenset({_PATH}), (), frozenset()), _AFFECTED
    )
    async_sync_schema_issues(hass, well, SchemaFindings(frozenset(), (), frozenset()), _AFFECTED)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(sick))


async def test_circuit_rename_and_commissioning_raise_no_issue(hass, entry) -> None:
    """Tier-1 and Tier-2 changes are handled elsewhere and must stay silent.

    Silent because of what they are, not because nothing reads them: the map
    names a live entity for the field, and it still raises nothing.
    """
    async_sync_schema_issues(
        hass,
        entry,
        SchemaFindings(frozenset(), (), frozenset({"pv.model"})),
        {"pv.model": ["sensor.pv_model"]},
    )
    registry = ir.async_get(hass)
    assert not [k for k in registry.issues if k[0] == DOMAIN]


async def test_unit_mismatch_raises_its_own_issue(hass, entry) -> None:
    """The second of the two user-facing defects: a reading may be wrong."""
    mismatch = UnitMismatch(_UNIT_PATH, "V", "kV")
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset(), (mismatch,), frozenset()), _UNIT_AFFECTED
    )

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, _unit_issue_id(entry, _UNIT_PATH))
    assert issue is not None
    assert issue.translation_key == "schema_unit_mismatch"
    assert issue.translation_placeholders == {
        "field_path": _UNIT_PATH,
        "ha_unit": "V",
        "schema_unit": "kV",
        "count": "1",
        "examples": "sensor.voltage",
    }


async def test_unit_mismatch_issue_is_cleared_on_its_own(hass, entry) -> None:
    """Reconciliation must scope both classes, not just the unresolved one."""
    mismatch = UnitMismatch(_UNIT_PATH, "V", "kV")
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset(), (mismatch,), frozenset()), _UNIT_AFFECTED
    )
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset(), (), frozenset()), _UNIT_AFFECTED
    )

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _unit_issue_id(entry, _UNIT_PATH)) is None


async def test_issues_are_not_persistent(hass, entry) -> None:
    """Derived from live state, so they must be re-asserted at startup.

    A non-persistent issue reloads as a tombstone carrying only the dismissal,
    which is exactly what lets re-assertion happen without resurrecting one.
    """
    mismatch = UnitMismatch(_UNIT_PATH, "V", "kV")
    async_sync_schema_issues(
        hass,
        entry,
        SchemaFindings(frozenset({_PATH}), (mismatch,), frozenset()),
        _AFFECTED | _UNIT_AFFECTED,
    )

    registry = ir.async_get(hass)
    for issue_id in (_issue_id(entry), _unit_issue_id(entry, _UNIT_PATH)):
        issue = registry.async_get_issue(DOMAIN, issue_id)
        assert issue.is_persistent is False
        assert issue.is_fixable is False
        assert issue.severity is ir.IssueSeverity.WARNING


async def test_affected_entities_are_bounded_and_counted(hass, entry) -> None:
    """One missing `circuit.instant_power_w` affects every circuit.

    The payload carries the full count but only a few examples, so a 40-circuit
    panel does not render a wall of entity ids.
    """
    affected = [f"sensor.circuit_{n}_power" for n in range(40)]
    async_sync_schema_issues(
        hass,
        entry,
        SchemaFindings(frozenset({_PATH}), (), frozenset()),
        {_PATH: affected},
    )

    registry = ir.async_get(hass)
    placeholders = registry.async_get_issue(DOMAIN, _issue_id(entry)).translation_placeholders
    assert placeholders["count"] == "40"
    assert placeholders["examples"].count(",") < 5
    assert "sensor.circuit_0_power" in placeholders["examples"]


# --- Findings nobody owns -------------------------------------------------
#
# `vendor_cloud` is `entity_registry_enabled_default=False`, so it is registered
# and never added to hass. A fresh install against the flat simulator raised
# "`panel.vendor_cloud` ... 0 entity/entities are affected (for example: none)"
# beside two genuine notices, which is how a category of Repair gets ignored.


async def test_a_finding_no_enabled_entity_reads_raises_no_issue(hass, entry) -> None:
    """The disabled-by-default case: nothing the user owns is affected."""
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"panel.vendor_cloud"}), (), frozenset()), {}
    )

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry, "panel.vendor_cloud")) is None
    assert not [k for k in registry.issues if k[0] == DOMAIN]


async def test_a_unit_mismatch_no_enabled_entity_reads_raises_no_issue(hass, entry) -> None:
    """The same rule for the second class: no reading of the user's is wrong."""
    mismatch = UnitMismatch(_UNIT_PATH, "V", "kV")
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (mismatch,), frozenset()), {})

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _unit_issue_id(entry, _UNIT_PATH)) is None
    assert not [k for k in registry.issues if k[0] == DOMAIN]


async def test_suppression_only_silences_the_path_nobody_reads(hass, entry) -> None:
    """The real install: two genuine notices, one suppressed, in one pass."""
    async_sync_schema_issues(
        hass,
        entry,
        SchemaFindings(frozenset({_PATH, "panel.vendor_cloud"}), (), frozenset()),
        _AFFECTED,
    )

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry))
    assert registry.async_get_issue(DOMAIN, _issue_id(entry, "panel.vendor_cloud")) is None


async def test_a_suppressed_finding_is_logged(hass, entry, caplog) -> None:
    """Suppressed is not discarded: the field path stays reachable in the log."""
    with caplog.at_level(logging.DEBUG, logger="custom_components.span_panel.schema_repairs"):
        async_sync_schema_issues(
            hass, entry, SchemaFindings(frozenset({"panel.vendor_cloud"}), (), frozenset()), {}
        )

    assert "panel.vendor_cloud" in caplog.text
    assert "no enabled entity reads" in caplog.text


async def test_an_issue_is_deleted_when_its_last_affected_entity_goes(hass, entry) -> None:
    """The transition the reconcile pass has to cover.

    A path raised while entities read it, then disabled or removed, must have its
    issue deleted rather than left orphaned at "0 affected".
    """
    findings = SchemaFindings(frozenset({_PATH}), (), frozenset())
    async_sync_schema_issues(hass, entry, findings, _AFFECTED)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry))

    async_sync_schema_issues(hass, entry, findings, {})

    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is None


async def test_a_unit_mismatch_issue_is_deleted_when_its_entities_go(hass, entry) -> None:
    """The same transition for the second class."""
    findings = SchemaFindings(frozenset(), (UnitMismatch(_UNIT_PATH, "V", "kV"),), frozenset())
    async_sync_schema_issues(hass, entry, findings, _UNIT_AFFECTED)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _unit_issue_id(entry, _UNIT_PATH))

    async_sync_schema_issues(hass, entry, findings, {})

    assert registry.async_get_issue(DOMAIN, _unit_issue_id(entry, _UNIT_PATH)) is None


async def test_a_dismissal_survives_the_entity_leaving_and_returning(hass, entry) -> None:
    """Suppression deletes, and a delete is the one thing that clears a dismissal.

    That is the accepted cost of not nagging about a finding nobody owns: the
    notice is genuinely new when an entity starts reading the field again.
    """
    findings = SchemaFindings(frozenset({_PATH}), (), frozenset())
    async_sync_schema_issues(hass, entry, findings, _AFFECTED)
    ir.async_ignore_issue(hass, DOMAIN, _issue_id(entry), True)

    async_sync_schema_issues(hass, entry, findings, {})
    async_sync_schema_issues(hass, entry, findings, _AFFECTED)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _issue_id(entry))
    assert issue is not None
    assert issue.dismissed_version is None


async def test_findings_fire_an_event(hass, entry) -> None:
    """Matches the `span_panel_current_alert` pattern so automations can react."""
    events = []
    hass.bus.async_listen(EVENT_SCHEMA_ISSUE, events.append)

    mismatch = UnitMismatch(_UNIT_PATH, "V", "kV")
    async_sync_schema_issues(
        hass,
        entry,
        SchemaFindings(frozenset({_PATH}), (mismatch,), frozenset()),
        _AFFECTED | _UNIT_AFFECTED,
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "entry_id": entry.entry_id,
        "unresolved": [_PATH],
        "unit_mismatches": [_UNIT_PATH],
    }


async def test_the_event_carries_only_what_the_user_was_told(hass, entry) -> None:
    """A suppressed finding is not user-facing, so it is not in the event either.

    Otherwise an automation would react to a defect that took nothing down, and
    an all-suppressed pass — which fires nothing at all — would disagree with a
    partly-suppressed one.
    """
    events = []
    hass.bus.async_listen(EVENT_SCHEMA_ISSUE, events.append)

    async_sync_schema_issues(
        hass,
        entry,
        SchemaFindings(frozenset({_PATH, "panel.vendor_cloud"}), (), frozenset()),
        _AFFECTED,
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["unresolved"] == [_PATH]


async def test_an_all_suppressed_pass_fires_no_event(hass, entry) -> None:
    events = []
    hass.bus.async_listen(EVENT_SCHEMA_ISSUE, events.append)

    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"panel.vendor_cloud"}), (), frozenset()), {}
    )
    await hass.async_block_till_done()

    assert events == []


async def test_a_healthy_pass_fires_no_event(hass, entry) -> None:
    events = []
    hass.bus.async_listen(EVENT_SCHEMA_ISSUE, events.append)

    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset(), (), frozenset({"pv.model"})), {}
    )
    await hass.async_block_till_done()

    assert events == []


async def test_clearing_removes_only_this_entry(hass) -> None:
    """`async_remove_entry` must not take a second panel's issues with it."""
    removed = MockConfigEntry(domain=DOMAIN, data={}, unique_id="removed")
    removed.add_to_hass(hass)
    kept = MockConfigEntry(domain=DOMAIN, data={}, unique_id="kept")
    kept.add_to_hass(hass)

    findings = SchemaFindings(frozenset({_PATH}), (), frozenset())
    async_sync_schema_issues(hass, removed, findings, _AFFECTED)
    async_sync_schema_issues(hass, kept, findings, _AFFECTED)

    async_clear_schema_issues(hass, removed)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(removed)) is None
    assert registry.async_get_issue(DOMAIN, _issue_id(kept))


async def test_reconciliation_leaves_other_domain_issues_alone(hass, entry) -> None:
    """The upgrade repair shares our domain and must survive a reconcile pass."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"panel_upgraded_to_ebus_v1_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="panel_upgraded_to_ebus_v1",
    )

    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (), frozenset()), {})
    async_clear_schema_issues(hass, entry)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"panel_upgraded_to_ebus_v1_{entry.entry_id}")


async def test_remove_entry_clears_this_entry_issues(hass, entry) -> None:
    """Core does not delete our issues when the entry is removed."""
    from custom_components.span_panel import async_remove_entry

    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({_PATH}), (), frozenset()), _AFFECTED
    )
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry))

    await async_remove_entry(hass, entry)

    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is None


# --- The affected-entity map, built from real entities ---------------------
#
# The map is populated by the entities themselves rather than reverse-engineered
# from entity descriptions. Reverse-engineering was wrong three ways at once:
# panel-data sensors build their unique_id from `get_panel_entity_suffix`, whose
# `PANEL_ENTITY_SUFFIX_MAPPING` deliberately disagrees with the general mapping
# (`instantGridPowerW` -> "current_power", not "grid_power"); binary sensors use
# the raw camelCase key ("doorState"), which a lowercasing suffix helper can
# never match; and an `endswith("power")` test claims every power entity on the
# panel. Self-registration cannot drift from the builders because it never
# consults them.

_STYLE_PATHS = {
    # circuit style — `get_user_friendly_suffix`, and the over-match case
    "circuit.instant_power_w",
    # panel-data style — `get_panel_entity_suffix`, which disagrees
    "panel.instant_grid_power_w",
    "panel.power_flow_battery",
    "panel.power_flow_pv",
    "panel.power_flow_site",
    # binary-sensor style — the raw camelCase description key
    "panel.door_state",
}


async def _entities_by_declared_path(hass):
    """Build the real entities for a healthy panel, grouped by declared field.

    Real platform setup, real entity classes, real unique_id builders — the
    three id styles only differ because the builders differ, so anything less
    faithful would not exercise the bug this replaced.
    """
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.span_panel import SpanPanelRuntimeData, ensure_device_registered
    from custom_components.span_panel.binary_sensor import (
        async_setup_entry as binary_setup,
    )
    from custom_components.span_panel.coordinator import SpanPanelCoordinator
    from custom_components.span_panel.field_paths import FieldPathDeclarationMixin
    from custom_components.span_panel.sensor import async_setup_entry as sensor_setup

    from .factories import (
        SpanBatterySnapshotFactory,
        SpanCircuitSnapshotFactory,
        SpanPanelSnapshotFactory,
    )

    # Two circuits and the three power flows, so the over-match case has real
    # panel power sensors to be wrongly claimed by.
    snapshot = SpanPanelSnapshotFactory.create(
        circuits={
            "1": SpanCircuitSnapshotFactory.create(circuit_id="1", name="Kitchen"),
            "2": SpanCircuitSnapshotFactory.create(circuit_id="2", name="Garage"),
        },
        battery=SpanBatterySnapshotFactory.create(soe_percentage=85.0, connected=True),
        power_flow_battery=-250.0,
        power_flow_pv=1250.0,
        power_flow_site=3000.0,
    )
    config_entry = MockConfigEntry(
        domain=DOMAIN, data={}, title="SPAN Panel", unique_id=snapshot.serial_number
    )
    config_entry.add_to_hass(hass)
    client = MagicMock()
    client.stop_streaming = AsyncMock()
    client.close = AsyncMock()
    coordinator = SpanPanelCoordinator(hass, client, config_entry)
    coordinator.data = snapshot
    # A real panel device: the BESS sub-device declares `via_device`, and HA
    # refuses to add an entity whose via_device is not a registered device id.
    config_entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=await ensure_device_registered(hass, config_entry, snapshot, "SPAN Panel"),
    )

    grouped: dict[str, dict[str, list[object]]] = {}
    for platform_domain, setup in (("sensor", sensor_setup), ("binary_sensor", binary_setup)):
        added = MagicMock()
        await setup(hass, config_entry, added)
        for entity in added.call_args.args[0]:
            description = getattr(entity, "entity_description", None)
            if not isinstance(description, FieldPathDeclarationMixin):
                continue
            if description.derived or description.field_path not in _STYLE_PATHS:
                continue
            grouped.setdefault(description.field_path, {}).setdefault(platform_domain, []).append(
                entity
            )

    return coordinator, config_entry, grouped


async def _add_to_platform(hass, config_entry, entities, platform_domain: str) -> None:
    """Add real entities to a real entity platform, as HA does at setup."""
    from pytest_homeassistant_custom_component.common import MockEntityPlatform

    platform = MockEntityPlatform(hass, domain=platform_domain, platform_name=DOMAIN)
    platform.config_entry = config_entry
    await platform.async_add_entities(entities)
    await hass.async_block_till_done()


async def _stop_scheduling(coordinator) -> None:
    """Cancel the coordinator's refresh timer and debouncer.

    `SpanPanelCoordinator.async_shutdown` releases the client but does not chain
    to the base implementation; in production the timer is unscheduled when the
    last entity listener goes away, which these tests deliberately do not do.
    """
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    await DataUpdateCoordinator.async_shutdown(coordinator)


async def test_affected_entities_span_all_three_unique_id_styles(hass) -> None:
    """A dead field must name the entities that actually died — every style.

    Three unique_id builders are in play and they do not agree, so any scheme
    that re-derives entity ids from entity descriptions gets at least two of the
    three wrong: it reports "0 affected" for a panel field whose sensor is dead,
    and over-claims for a circuit field.
    """
    coordinator, config_entry, grouped = await _entities_by_declared_path(hass)
    try:
        assert _STYLE_PATHS <= grouped.keys(), f"fixture missed {_STYLE_PATHS - grouped.keys()}"

        # The fixture really does cover three different builders: circuit suffix,
        # panel entity suffix, raw camelCase key.
        def _first(path: str, platform_domain: str):
            return grouped[path][platform_domain][0]

        assert _first("circuit.instant_power_w", "sensor").unique_id.endswith("_power")
        assert _first("panel.instant_grid_power_w", "sensor").unique_id.endswith("_current_power")
        assert _first("panel.door_state", "binary_sensor").unique_id.endswith("doorState")

        # One platform per domain, as HA does — several platforms sharing a
        # domain and platform name is not a shape the real integration produces.
        for platform_domain in ("sensor", "binary_sensor"):
            batch = [
                entity
                for by_domain in grouped.values()
                for entity in by_domain.get(platform_domain, [])
            ]
            await _add_to_platform(hass, config_entry, batch, platform_domain)

        affected = coordinator.entity_ids_by_field_path

        for path, by_domain in grouped.items():
            expected = sorted(
                entity.entity_id for entities in by_domain.values() for entity in entities
            )
            assert affected[path] == expected, path
            assert all(expected), f"{path} recorded an entity with no entity_id"

        # The over-match case: a dead circuit power field must claim only circuit
        # power entities, never the panel's own power sensors.
        circuit_power = set(affected["circuit.instant_power_w"])
        assert circuit_power
        for other in _STYLE_PATHS - {"circuit.instant_power_w"}:
            assert affected[other]
            assert set(affected[other]).isdisjoint(circuit_power), (
                f"{other} entities were claimed by circuit.instant_power_w"
            )
    finally:
        await _stop_scheduling(coordinator)


async def test_the_repair_payload_names_the_real_entities(hass) -> None:
    """End to end: the notice a user reads carries real, resolvable entity ids."""
    coordinator, config_entry, grouped = await _entities_by_declared_path(hass)
    try:
        entities = grouped["panel.instant_grid_power_w"]["sensor"]
        await _add_to_platform(hass, config_entry, entities, "sensor")

        async_sync_schema_issues(
            hass,
            config_entry,
            SchemaFindings(frozenset({"panel.instant_grid_power_w"}), (), frozenset()),
            coordinator.entity_ids_by_field_path,
        )

        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, f"unresolved_{config_entry.entry_id}_panel.instant_grid_power_w"
        )
        assert issue is not None
        placeholders = issue.translation_placeholders
        assert placeholders["count"] == str(len(entities))
        assert placeholders["examples"] == entities[0].entity_id
        assert hass.states.get(entities[0].entity_id) is not None
    finally:
        await _stop_scheduling(coordinator)


async def test_removing_an_entity_drops_it_from_the_map(hass) -> None:
    """A removed entity must stop inflating the count."""
    coordinator, config_entry, grouped = await _entities_by_declared_path(hass)
    try:
        entities = grouped["panel.instant_grid_power_w"]["sensor"]
        await _add_to_platform(hass, config_entry, entities, "sensor")
        assert coordinator.entity_ids_by_field_path["panel.instant_grid_power_w"]

        for entity in entities:
            await entity.async_remove()
        await hass.async_block_till_done()

        assert "panel.instant_grid_power_w" not in coordinator.entity_ids_by_field_path
    finally:
        await _stop_scheduling(coordinator)


async def test_derived_entities_are_not_tracked(hass) -> None:
    """A derived entity must not be blamed for one of the fields it combines.

    Derived entities compute from several fields or none, so no single field's
    loss can be said to have taken them down. They declare no `field_path` and
    no residual reads, and must therefore land in no bucket at all.
    """
    from unittest.mock import MagicMock

    from custom_components.span_panel.binary_sensor import (
        BESS_CONNECTED_SENSOR,
        async_setup_entry as binary_setup,
    )

    coordinator, config_entry, _ = await _entities_by_declared_path(hass)
    try:
        assert BESS_CONNECTED_SENSOR.derived, "fixture assumes a derived description"

        added = MagicMock()
        await binary_setup(hass, config_entry, added)
        derived = [
            entity
            for entity in added.call_args.args[0]
            if getattr(entity, "entity_description", None) is BESS_CONNECTED_SENSOR
        ]
        assert derived

        await _add_to_platform(hass, config_entry, derived, "binary_sensor")

        assert coordinator.entity_ids_by_field_path == {}
    finally:
        await _stop_scheduling(coordinator)


async def test_a_platform_with_no_description_still_registers_its_residuals(
    hass,
) -> None:
    """The circuit switch carries no entity description at all.

    It is tracked purely through `_residual_field_paths`, which is the whole
    reason that hook exists.
    """
    from unittest.mock import MagicMock

    from custom_components.span_panel.switch import async_setup_entry as switch_setup

    coordinator, config_entry, _ = await _entities_by_declared_path(hass)
    try:
        added = MagicMock()
        await switch_setup(hass, config_entry, added)
        switches = list(added.call_args.args[0])
        assert switches
        assert not hasattr(switches[0], "entity_description")

        await _add_to_platform(hass, config_entry, switches, "switch")

        expected = sorted(s.entity_id for s in switches)
        assert coordinator.entity_ids_by_field_path == {
            "circuit.relay_state": expected,
            "circuit.name": expected,
            "circuit.tabs": expected,
        }
    finally:
        await _stop_scheduling(coordinator)


# --- Residual reads -------------------------------------------------------
#
# Five field paths are read from entity code rather than from a description's
# `field_path`: the switch's relay state, the select's priority, and the name,
# tabs and relay requester a circuit entity uses for its identity and its
# attributes. Each is declared on the entity that reads it, which is where
# `field_paths.residual_field_paths()` collects them from for the producible
# gate. Nothing declared them on the entities, so a dead `circuit.relay_state`
# reported "0 entity/entities are affected" while every breaker switch on the
# panel was out.


async def test_a_dead_relay_state_names_the_breaker_switches(hass) -> None:
    """The residual set must not reproduce the "0 affected" lie."""
    from unittest.mock import MagicMock

    from custom_components.span_panel.switch import async_setup_entry as switch_setup

    coordinator, config_entry, _ = await _entities_by_declared_path(hass)
    try:
        added = MagicMock()
        await switch_setup(hass, config_entry, added)
        switches = list(added.call_args.args[0])
        assert len(switches) == 2, "fixture should build one switch per circuit"

        await _add_to_platform(hass, config_entry, switches, "switch")

        affected = coordinator.entity_ids_by_field_path
        assert affected["circuit.relay_state"] == sorted(s.entity_id for s in switches)

        async_sync_schema_issues(
            hass,
            config_entry,
            SchemaFindings(frozenset({"circuit.relay_state"}), (), frozenset()),
            affected,
        )
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, f"unresolved_{config_entry.entry_id}_circuit.relay_state"
        )
        assert issue is not None
        assert issue.translation_placeholders["count"] == "2"
        assert issue.translation_placeholders["examples"] != "none"
    finally:
        await _stop_scheduling(coordinator)


async def test_a_dead_circuit_attribute_names_the_power_sensors(hass) -> None:
    """The circuit power sensor's residual reads must name it, one by one.

    Every other residual is claimed by more than one entity class, so dropping
    it from any single class leaves the read enumerated somewhere and the panel
    still describable. `circuit.relay_requester` is claimed by this class alone:
    it is republished as a state attribute here and nowhere else, so if this
    entity stopped declaring it, the path would leave
    `field_paths.residual_field_paths()` entirely and the producible gate would
    quietly stop covering a read that is still happening.

    Stated as the Repair's own output rather than as a second copy of the
    declaration, so it fails on the observable consequence -- a dead attribute
    naming no entity -- instead of on a list disagreeing with a list.
    """
    from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor

    coordinator, config_entry, grouped = await _entities_by_declared_path(hass)
    try:
        power_sensors = [
            entity
            for entity in grouped["circuit.instant_power_w"]["sensor"]
            if isinstance(entity, SpanCircuitPowerSensor)
        ]
        assert len(power_sensors) == 2, "fixture should build one power sensor per circuit"

        await _add_to_platform(hass, config_entry, power_sensors, "sensor")

        expected = sorted(sensor.entity_id for sensor in power_sensors)
        assert coordinator.entity_ids_by_field_path == {
            # The description's own declaration.
            "circuit.instant_power_w": expected,
            # Identity, read outside any value_fn.
            "circuit.name": expected,
            "circuit.tabs": expected,
            # Republished as state attributes.
            "circuit.relay_state": expected,
            "circuit.relay_requester": expected,
            "circuit.priority": expected,
        }
    finally:
        await _stop_scheduling(coordinator)


async def test_a_dead_priority_names_the_selects(hass) -> None:
    """The select's own state comes from `circuit.priority`."""
    from unittest.mock import MagicMock

    from custom_components.span_panel.select import async_setup_entry as select_setup

    coordinator, config_entry, _ = await _entities_by_declared_path(hass)
    try:
        added = MagicMock()
        await select_setup(hass, config_entry, added)
        selects = list(added.call_args.args[0])
        assert selects

        await _add_to_platform(hass, config_entry, selects, "select")

        affected = coordinator.entity_ids_by_field_path
        assert affected["circuit.priority"] == sorted(s.entity_id for s in selects)
    finally:
        await _stop_scheduling(coordinator)
