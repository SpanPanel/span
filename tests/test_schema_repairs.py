from __future__ import annotations

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
    async_sync_schema_issues(hass, entry, findings, {})

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry))


async def test_issue_cleared_when_condition_resolves(hass, entry) -> None:
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset({_PATH}), (), frozenset()), {})
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (), frozenset()), {})

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is None


async def test_dismissal_survives_reconciliation(hass, entry) -> None:
    """Re-raise idempotently rather than delete-then-recreate.

    Deleting is the one thing that resets a dismissal, which would turn an
    accepted notice into a permanent nag.
    """
    findings = SchemaFindings(frozenset({_PATH}), (), frozenset())
    async_sync_schema_issues(hass, entry, findings, {})

    issue_id = _issue_id(entry)
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    registry = ir.async_get(hass)
    dismissed = registry.async_get_issue(DOMAIN, issue_id).dismissed_version
    assert dismissed is not None

    for _ in range(3):
        async_sync_schema_issues(hass, entry, findings, {})

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
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset({"a.one"}), (), frozenset()), {})
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"a.one", "b.two"}), (), frozenset()), {}
    )
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry, "b.two"))


async def test_a_dismissed_finding_does_not_swallow_a_later_one(hass, entry) -> None:
    """The reason one issue per (class, path) is not cosmetic.

    Dismissing an aggregate would silence every finding that joined it later,
    because the update branch preserves `dismissed_version`.
    """
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset({"a.one"}), (), frozenset()), {})
    ir.async_ignore_issue(hass, DOMAIN, _issue_id(entry, "a.one"), True)

    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({"a.one", "b.two"}), (), frozenset()), {}
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

    async_sync_schema_issues(hass, sick, SchemaFindings(frozenset({_PATH}), (), frozenset()), {})
    async_sync_schema_issues(hass, well, SchemaFindings(frozenset(), (), frozenset()), {})

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(sick))


async def test_circuit_rename_and_commissioning_raise_no_issue(hass, entry) -> None:
    """Tier-1 and Tier-2 changes are handled elsewhere and must stay silent."""
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset(), (), frozenset({"pv.model"})), {}
    )
    registry = ir.async_get(hass)
    assert not [k for k in registry.issues if k[0] == DOMAIN]


async def test_unit_mismatch_raises_its_own_issue(hass, entry) -> None:
    """The second of the two user-facing defects: a reading may be wrong."""
    mismatch = UnitMismatch("panel.l1_voltage", "V", "kV")
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (mismatch,), frozenset()), {})

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, _unit_issue_id(entry, "panel.l1_voltage"))
    assert issue is not None
    assert issue.translation_key == "schema_unit_mismatch"
    assert issue.translation_placeholders == {
        "field_path": "panel.l1_voltage",
        "ha_unit": "V",
        "schema_unit": "kV",
    }


async def test_unit_mismatch_issue_is_cleared_on_its_own(hass, entry) -> None:
    """Reconciliation must scope both classes, not just the unresolved one."""
    mismatch = UnitMismatch("panel.l1_voltage", "V", "kV")
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (mismatch,), frozenset()), {})
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset(), (), frozenset()), {})

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _unit_issue_id(entry, "panel.l1_voltage")) is None


async def test_issues_are_not_persistent(hass, entry) -> None:
    """Derived from live state, so they must be re-asserted at startup.

    A non-persistent issue reloads as a tombstone carrying only the dismissal,
    which is exactly what lets re-assertion happen without resurrecting one.
    """
    mismatch = UnitMismatch("panel.l1_voltage", "V", "kV")
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({_PATH}), (mismatch,), frozenset()), {}
    )

    registry = ir.async_get(hass)
    for issue_id in (_issue_id(entry), _unit_issue_id(entry, "panel.l1_voltage")):
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


async def test_no_affected_entities_still_reads_sensibly(hass, entry) -> None:
    """An empty example list must not render as an empty string in the notice."""
    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset({_PATH}), (), frozenset()), {})

    registry = ir.async_get(hass)
    placeholders = registry.async_get_issue(DOMAIN, _issue_id(entry)).translation_placeholders
    assert placeholders["count"] == "0"
    assert placeholders["examples"]


async def test_findings_fire_an_event(hass, entry) -> None:
    """Matches the `span_panel_current_alert` pattern so automations can react."""
    events = []
    hass.bus.async_listen(EVENT_SCHEMA_ISSUE, events.append)

    mismatch = UnitMismatch("panel.l1_voltage", "V", "kV")
    async_sync_schema_issues(
        hass, entry, SchemaFindings(frozenset({_PATH}), (mismatch,), frozenset()), {}
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "entry_id": entry.entry_id,
        "unresolved": [_PATH],
        "unit_mismatches": ["panel.l1_voltage"],
    }


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
    async_sync_schema_issues(hass, removed, findings, {})
    async_sync_schema_issues(hass, kept, findings, {})

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

    async_sync_schema_issues(hass, entry, SchemaFindings(frozenset({_PATH}), (), frozenset()), {})
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
        panel_device_id=await ensure_device_registered(
            hass, config_entry, snapshot, "SPAN Panel"
        ),
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
            grouped.setdefault(description.field_path, {}).setdefault(
                platform_domain, []
            ).append(entity)

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
        assert _STYLE_PATHS <= grouped.keys(), (
            f"fixture missed {_STYLE_PATHS - grouped.keys()}"
        )

        # The fixture really does cover three different builders: circuit suffix,
        # panel entity suffix, raw camelCase key.
        def _first(path: str, platform_domain: str):
            return grouped[path][platform_domain][0]

        assert _first("circuit.instant_power_w", "sensor").unique_id.endswith("_power")
        assert _first("panel.instant_grid_power_w", "sensor").unique_id.endswith(
            "_current_power"
        )
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
