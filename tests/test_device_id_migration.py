"""A panel update that gives its circuits new device ids keeps everything an install holds.

Firmware r202645 calls a branch circuit `{prefix}-1-16-<n>` that r202639 called
`circuit-<n>`, and a remote CT `{prefix}-1-42-<n>` for `remote-ct-<n>`. These
tests run the integration's real setup -- coordinator, platforms, registries,
stores -- over SPAN's reference captures of the same panel before and after
(`r202639-e` and `r202645-e`, and `r202639-a` and `r202645-a`, whose remote CT
is the first captured on r202645), over a recommissioned pair (`r202639-c`,
`r202645-c`), and over trees derived from the captures where no capture shows
the case: an inverter a circuit feeds, a vendor extension on a circuit, a
partial tree, collisions an earlier release left, and a registry from before
circuits had devices. The vendored captures are never edited; a derived
tree is built in memory by `captures_replay.renamed_devices`.

Each test names, in its docstring, the change to the integration that makes it
fail.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import timedelta
import json
import logging
from typing import Final
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.const import DOMAIN, USE_CIRCUIT_NUMBERS
from custom_components.span_panel.conversion_repairs import (
    async_raise_breaker_switch_area_notice,
    breaker_switch_area_issue_id,
)
from custom_components.span_panel.device_id_migration import (
    DeviceIdPairing,
    TraitConflict,
    current_circuit_id,
    migration_pending,
    pair_device_ids,
    previous_device_ids,
    rekeyed,
)
from custom_components.span_panel.device_id_rekey import async_migrate_device_ids, holds_old_ids
from custom_components.span_panel.entry_reloads import async_request_rebuild
from custom_components.span_panel.frontend import async_get_favorites
from custom_components.span_panel.helpers import outside_meter_label
from custom_components.span_panel.id_builder import (
    build_circuit_device_identifier,
    build_circuit_unique_id,
    build_switch_unique_id,
    circuit_entity_unique_ids,
)
from custom_components.span_panel.notices import async_raise as raise_notice, is_standing
from custom_components.span_panel.util import pv_inverter_device_identifier

from .captures_replay import (
    RetainedTree,
    capture,
    description,
    devices_of_type,
    panel_device_id,
    renamed_devices,
    replay,
    snapshot,
    with_connection_point,
    with_serial,
    without_device,
    without_topics,
)
from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory
from .replayed_panel import MONITORED_OPTIONS, PANEL_NAME, Panel, replayed_panels

BEFORE: Final = "r202639-e"
AFTER: Final = "r202645-e"
"""The same panel on r202639 and on r202645: 46 circuits that pair by trait instance."""

TANDEM: Final = ("circuit-52", "circuit-53")
"""Two circuits sharing one meter; monitoring holds the pair's override under the first."""

RANGE: Final = "circuit-5"
"""`Electric range`, 20 A on space 9: one circuit on its own."""

BATH: Final = "circuit-7"
"""`Primary Bath Outlets`, whose device the user renames and puts in an area."""

HVAC: Final = "circuit-1"
"""`HVAC`, whose breaker switch carries an ignored area notice."""

CIRCUIT_TYPE: Final = "energy.ebus.device.circuit"

MIGRATION_LOGGER: Final = "custom_components.span_panel.device_id_rekey"
MAPPING_LOGGER: Final = "custom_components.span_panel.device_id_migration"

_NOTICES_KEY: Final = "span_panel.notices"
_PACKAGES_KEY: Final = "span_panel_package_ratings"
_PACKAGE_NOTICE: Final = "package_ratings_changed"
_NEW_ENTITIES_NOTICE: Final = "new_entities"


OLD_IDS_E: Final = frozenset(snapshot(capture(BEFORE).tree()).circuits)
"""Every circuit id of `r202639-e`."""

_FAMILY_OF_TRAIT: Final = {"16": "circuit", "42": "remote-ct"}


def new_ids(name: str) -> dict[str, str]:
    """Return each r202639 id's counterpart in an r202645 capture, read off the capture's own ids.

    Read from the capture rather than written out, so a capture whose ids are
    masked afresh needs no edit here. Read by splitting the id, not through the
    integration's pairing, which is what the tests hold to account.
    """
    return new_ids_of(capture(name).tree())


def new_ids_of(tree: RetainedTree) -> dict[str, str]:
    """Return each r202639 id's counterpart in an r202645 tree, read off its ids as `new_ids` reads a capture's."""
    ids: dict[str, str] = {}
    for circuit_id in snapshot(tree).circuits:
        head, trait, n = circuit_id.rsplit("-", 2)
        assert head.endswith("-1") and trait in _FAMILY_OF_TRAIT, circuit_id
        ids[f"{_FAMILY_OF_TRAIT[trait]}-{n}"] = circuit_id
    return ids


NEW_IDS_E: Final = new_ids(AFTER)


def new_id_e(old_id: str) -> str:
    """Return the id `r202645-e` gives the circuit `r202639-e` calls `old_id`."""
    return NEW_IDS_E[old_id]


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Allow the switch platform's relock and debounce timers to outlive a test."""
    return True


@pytest.fixture
def panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve."""
    with replayed_panels(hass) as make:
        yield make


# ---------------------------------------------------------------------------
# What an install holds, read back
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Registered:
    """Every row and device the entry has: by registry id, what identifies it."""

    entities: Mapping[str, tuple[str, str]]
    """Registry id to `(entity_id, unique_id)`."""

    devices: Mapping[str, frozenset[str]]
    """Registry id to the device's identifiers in this domain."""

    issues: frozenset[str]


def registered(hass: HomeAssistant, entry: MockConfigEntry) -> Registered:
    devices = dr.async_get(hass)
    return Registered(
        entities={
            row.id: (row.entity_id, row.unique_id)
            for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        },
        devices={
            device.id: frozenset(name for domain, name in device.identifiers if domain == DOMAIN)
            for device in (
                *dr.async_entries_for_config_entry(devices, entry.entry_id),
                *dr.async_child_entries_for_config_entry(devices, entry.entry_id),
            )
        },
        issues=frozenset(
            issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN
        ),
    )


def names_an_old_id(value: str, old_ids: frozenset[str]) -> bool:
    """Whether a unique id or an identifier still carries one of `old_ids` as a whole segment."""
    return any(
        f"_{old_id}_" in value or value.endswith(f"_{old_id}") or f"_{old_id}/" in value
        for old_id in old_ids
    )


def flush_delayed_saves(hass: HomeAssistant) -> None:
    """Fire the timers delayed store writes wait on, so the stores hold what was written."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))


def stores(hass_storage: Mapping[str, object], entry_id: str) -> dict[str, str]:
    """Every store this entry writes, serialised, plus the shared settings file."""
    return {
        key: json.dumps(value, sort_keys=True)
        for key, value in hass_storage.items()
        if key.endswith(entry_id) or key == "span_panel_settings"
    }


# ---------------------------------------------------------------------------
# What an install on r202639-e holds
# ---------------------------------------------------------------------------


def seed_package_removal(hass_storage: dict[str, object], entry_id: str) -> None:
    """Leave a removal the user has not dismissed, for `circuit-7`, with its notice standing.

    What a panel that describes its breakers can hold: a package copied before
    the panel said so, removed for that reason, and listed while the notice
    stands (`package_ratings.panel_describes_breakers`).
    """
    hass_storage[f"{_PACKAGES_KEY}.{entry_id}"] = {
        "version": 1,
        "minor_version": 2,
        "key": f"{_PACKAGES_KEY}.{entry_id}",
        "data": {
            "packages": {},
            "unacknowledged": {
                BATH: {
                    "circuit_id": BATH,
                    "label": "Primary Bath Outlets",
                    "reason": "panel_describes_breakers",
                    "package_ratings_a": [20, 30],
                    "panel_after_a": 20.0,
                    "service_a": None,
                }
            },
        },
    }
    hass_storage[f"{_NOTICES_KEY}.{entry_id}"] = {
        "version": 1,
        "key": f"{_NOTICES_KEY}.{entry_id}",
        "data": {
            "standing": {
                _PACKAGE_NOTICE: {
                    "title": "SPAN Panel ratings copied from the SPAN app removed",
                    "message": "- **Primary Bath Outlets**",
                }
            },
            "announced": {},
        },
    }


async def seed_settings(panel: Panel) -> str:
    """Make the user's settings an install on r202639-e can hold; return the area used.

    Through the services and registries a user reaches them by: an override on
    a tandem member, which monitoring holds under the pair's first circuit; a
    circuit switched out of monitoring; a graph horizon and a favorite; an
    ignored breaker-switch notice; a circuit device renamed and put in a room.
    """
    hass = panel.hass
    await panel.call(
        "set_circuit_threshold", circuit_id=panel.power(TANDEM[1]), spike_threshold_pct=150
    )
    await panel.call(
        "set_circuit_threshold", circuit_id=panel.power(RANGE), monitoring_enabled=False
    )
    await panel.call("set_circuit_graph_horizon", circuit_id=RANGE, horizon="1d")
    await hass.services.async_call(
        DOMAIN,
        "add_favorite",
        {"entity_id": panel.power(RANGE)},
        blocking=True,
        return_response=True,
    )

    switch_unique_id = build_switch_unique_id(panel.serial, HVAC)
    async_raise_breaker_switch_area_notice(
        hass,
        panel.entry,
        switch_entity_id=panel.entity_id("switch", switch_unique_id),
        switch_unique_id=switch_unique_id,
        area_name="Garage",
    )
    ir.async_ignore_issue(
        hass, DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, switch_unique_id), True
    )

    bathroom = ar.async_get(hass).async_create("Bathroom").id
    dr.async_get(hass).async_update_child_device(
        panel.circuit_device(BATH).id, name_by_user="Bath outlets", area_id=bathroom
    )
    await hass.async_block_till_done()
    return bathroom


async def assert_carried_over(
    panel: Panel,
    before: Registered,
    bathroom: str,
    caplog: pytest.LogCaptureFixture,
    *,
    renamed: Mapping[str, str] = NEW_IDS_E,
) -> None:
    """Everything the r202639-e install held, held now under r202645-e's ids, read back by its owner.

    `renamed` is each old id's new one, as the panel serving the update calls it.
    """
    hass = panel.hass
    after = registered(hass, panel.entry)
    old_ids = OLD_IDS_E

    # Every row and device kept its registry id and its entity id ...
    assert after.entities.keys() == before.entities.keys()
    assert {registry_id: entity_id for registry_id, (entity_id, _) in after.entities.items()} == {
        registry_id: entity_id for registry_id, (entity_id, _) in before.entities.items()
    }
    assert after.devices.keys() == before.devices.keys()
    # ... and none is keyed by an old id any more.
    assert not [uid for _, uid in after.entities.values() if names_an_old_id(uid, old_ids)]
    assert not [
        name for names in after.devices.values() for name in names if names_an_old_id(name, old_ids)
    ]
    registry = er.async_get(hass)
    for registry_id, (_, old_unique_id) in before.entities.items():
        if names_an_old_id(old_unique_id, old_ids):
            row = registry.async_get(after.entities[registry_id][0])
            assert row is not None and row.previous_unique_id == old_unique_id

    # Every enabled entity is live: the platforms built it on its re-keyed row,
    # rather than a second one the migration then had to remove.
    dead = [
        row.entity_id
        for row in er.async_entries_for_config_entry(registry, panel.entry.entry_id)
        if row.disabled_by is None and hass.states.get(row.entity_id) is None
    ]
    assert not dead

    # The renamed circuit device is the same device, its name and room kept.
    bath = panel.circuit_device(renamed[BATH])
    assert (bath.name_by_user, bath.area_id) == ("Bath outlets", bathroom)

    # Monitoring: the pair's override, and the circuit switched out.
    pair = panel.monitoring(renamed[TANDEM[1]])
    assert (pair["point_id"], pair["spike_threshold_pct"]) == (renamed[TANDEM[0]], 150)
    assert panel.monitoring(renamed[RANGE])["monitoring_enabled"] is False

    # The horizon and the favorite.
    horizons = panel.runtime.coordinator.graph_horizon_manager
    assert horizons is not None
    assert horizons.get_all_settings()["circuits"] == {
        renamed[RANGE]: {"horizon": "1d", "has_override": True}
    }
    favorites = await async_get_favorites(hass)
    assert favorites[panel.runtime.panel_device_id]["circuits"] == [renamed[RANGE]]

    # The removal the notice still lists.
    removals = panel.runtime.coordinator.package_rating_manager.diagnostics(panel.snapshot)[
        "unacknowledged"
    ]
    assert list(removals) == [renamed[BATH]]
    assert removals[renamed[BATH]]["circuit_id"] == renamed[BATH]

    # The breaker-switch notice, under the switch's new id and still ignored.
    switch_issue = breaker_switch_area_issue_id(
        panel.entry.entry_id, build_switch_unique_id(panel.serial, renamed[HVAC])
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, switch_issue)
    assert issue is not None and issue.dismissed_version is not None
    assert after.issues == (
        before.issues
        - {
            breaker_switch_area_issue_id(
                panel.entry.entry_id, build_switch_unique_id(panel.serial, HVAC)
            )
        }
    ) | {switch_issue}

    # Nothing announced, and no pairing warning.
    assert not is_standing(hass, panel.entry, _NEW_ENTITIES_NOTICE)
    assert is_standing(hass, panel.entry, _PACKAGE_NOTICE)
    assert not [
        record
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno >= logging.WARNING
    ]


def updated_trees(serial: str | None = None) -> tuple[RetainedTree, RetainedTree]:
    """Return r202639-e and r202645-e, under another serial where one is given (`with_serial`)."""
    before, after = capture(BEFORE).tree(), capture(AFTER).tree()
    if serial is None:
        return before, after
    return with_serial(before, serial), with_serial(after, serial)


async def install_and_update(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    *,
    options: Mapping[str, object] = MONITORED_OPTIONS,
    serial: str | None = None,
) -> tuple[Panel, Registered, str]:
    """Install on r202639-e with every setting the panel can hold, then restart on r202645-e."""
    before_tree, after_tree = updated_trees(serial)
    panel = panels(before_tree, options=options)
    seed_package_removal(hass_storage, panel.entry.entry_id)
    await panel.set_up(before_tree)
    bathroom = await seed_settings(panel)
    before = registered(panel.hass, panel.entry)
    await panel.restart_on(after_tree)
    return panel, before, bathroom


async def assert_a_second_migration_writes_nothing(panel: Panel) -> None:
    """Run the migration again on a migrated install: it looks everywhere and writes nothing.

    Every circuit-keyed store, the settings file, the notices and the three
    registries persist through `Store.async_save` or `Store.async_delay_save`,
    so recording both records every write the migration makes.
    """
    written: list[str] = []
    save, delay_save = Store.async_save, Store.async_delay_save

    async def recording_save(store: Store[object], data: object) -> None:
        written.append(store.key)
        await save(store, data)

    def recording_delay_save(
        store: Store[object], data_func: Callable[[], object], delay: float = 0
    ) -> None:
        written.append(store.key)
        delay_save(store, data_func, delay)

    with (
        patch.object(Store, "async_save", recording_save),
        patch.object(Store, "async_delay_save", recording_delay_save),
    ):
        await async_migrate_device_ids(panel.hass, panel.entry, panel.snapshot)
        await panel.hass.async_block_till_done()

    assert written == []


# ---------------------------------------------------------------------------
# before and after, real captures
# ---------------------------------------------------------------------------


def with_first_letters_upper_cased(serial: str) -> str:
    """Return `serial` with its first two characters upper-cased."""
    return serial[:2].upper() + serial[2:]


MIXED_CASE_SERIAL: Final = with_first_letters_upper_cased(
    snapshot(capture(BEFORE).tree()).serial_number
)
"""`r202639-e`'s serial with its first letters upper-cased: a serial of mixed case, which no capture has.

The circuit sensors' unique ids lower-case the serial, and the breaker
switch's, the priority select's, the inverter card's and every device
identifier keep it as it is (`id_builder`), so the migration must build each
family's old and new ids exactly as its builder does.
"""


@pytest.mark.parametrize(
    ("options", "serial"),
    [
        pytest.param(MONITORED_OPTIONS, None, id="circuit-names"),
        pytest.param({**MONITORED_OPTIONS, USE_CIRCUIT_NUMBERS: True}, None, id="circuit-numbers"),
        pytest.param(MONITORED_OPTIONS, MIXED_CASE_SERIAL, id="a-mixed-case-serial"),
    ],
)
async def test_an_install_keeps_everything_across_the_update(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    caplog: pytest.LogCaptureFixture,
    options: Mapping[str, object],
    serial: str | None,
) -> None:
    """Every entity, device and setting of r202639-e is held under r202645-e's ids.

    In circuit-numbers mode as in the default: a re-keyed row is what keeps
    the platform from creating its circuit entity anew with a circuit-number
    id preset. And on a serial of mixed case, which the builders spell two
    ways. Fails when a registry family, a device kind or a store is skipped,
    when an id is built for a serial spelled otherwise than its builder spells
    it, when the package ratings load before the migration, or when the
    migration runs after the platforms are forwarded.
    """
    caplog.set_level(logging.INFO)
    panel, before, bathroom = await install_and_update(
        panels, hass_storage, options=options, serial=serial
    )
    if serial is not None:
        assert panel.serial == serial and serial not in (serial.lower(), serial.upper())
    renamed = new_ids_of(updated_trees(serial)[1])
    if options.get(USE_CIRCUIT_NUMBERS):
        assert panel.power(renamed[RANGE]).endswith("_circuit_9_power")

    await assert_carried_over(panel, before, bathroom, caplog, renamed=renamed)
    infos = [
        record.getMessage()
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno == logging.INFO
    ]
    assert len(infos) == 1, infos
    assert "devices" in infos[0]


async def test_a_second_setup_on_the_new_ids_changes_nothing(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The migration is idempotent: a later setup leaves registries and stores as they are, and says nothing.

    Fails when the migration acts on anything but an old id the entry still
    holds, or counts as moved what it found already moved.
    """
    caplog.set_level(logging.INFO)
    panel, _, _ = await install_and_update(panels, hass_storage)
    flush_delayed_saves(panel.hass)
    await panel.hass.async_block_till_done()
    registry_before = dict(er.async_get(panel.hass).entities)
    devices_before = {
        device.id: device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(panel.hass), panel.entry.entry_id
        )
    }
    children_before = {
        child.id: child
        for child in dr.async_child_entries_for_config_entry(
            dr.async_get(panel.hass), panel.entry.entry_id
        )
    }
    issues_before = dict(ir.async_get(panel.hass).issues)
    stores_before = stores(hass_storage, panel.entry.entry_id)
    caplog.clear()

    with patch(
        "custom_components.span_panel.device_id_rekey.pair_device_ids",
        wraps=pair_device_ids,
    ) as pairing:
        await panel.restart_on(capture(AFTER).tree())
    flush_delayed_saves(panel.hass)
    await panel.hass.async_block_till_done()

    assert pairing.call_count == 1
    assert not [record.getMessage() for record in caplog.records if record.name == MIGRATION_LOGGER]
    assert dict(er.async_get(panel.hass).entities) == registry_before
    assert {
        device.id: device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(panel.hass), panel.entry.entry_id
        )
    } == devices_before
    assert {
        child.id: child
        for child in dr.async_child_entries_for_config_entry(
            dr.async_get(panel.hass), panel.entry.entry_id
        )
    } == children_before
    assert dict(ir.async_get(panel.hass).issues) == issues_before
    assert stores(hass_storage, panel.entry.entry_id) == stores_before


async def test_a_migrated_install_is_looked_at_again_and_nothing_is_written(
    panels: Callable[..., Panel], hass_storage: dict[str, object]
) -> None:
    """On the new ids every setup pairs, looks in the registries and reads each store; it writes nothing.

    Over every store an install on r202639-e holds a value in: package
    ratings, monitoring, graph horizons, favorites, the announcement record and
    the breaker-switch notice. Fails when a store re-key, or a registry step,
    writes without a value having moved.
    """
    panel, _, _ = await install_and_update(panels, hass_storage)

    await assert_a_second_migration_writes_nothing(panel)


@pytest.mark.parametrize("vendor", [False, True], ids=["captures", "with-a-vendor-extension"])
async def test_a_setup_that_stopped_after_the_registry_is_finished_by_the_next(
    panels: Callable[..., Panel], vendor: bool
) -> None:
    """A store that cannot be re-keyed stops the first setup after the update; the next finishes the job.

    The registry was re-keyed before the store failed, so the next setup moves
    no entity, and finds no extension row under an old scope. The record of
    what was announced still names the old ids, and is brought to the registry
    as it stands, so nothing is announced; the breaker switch's notice, not
    yet moved, follows its switch. Fails when the record or the notices follow
    only what the same setup moved, or the record only the rows it finds under
    old ids.
    """
    before_tree, after_tree = capture(BEFORE).tree(), capture(AFTER).tree()
    if vendor:
        before_tree = with_vendor_readings(before_tree, RANGE)
        after_tree = with_vendor_readings(after_tree, new_id_e(RANGE))
    panel = panels(before_tree)
    await panel.set_up(before_tree)
    original_switch = build_switch_unique_id(panel.serial, HVAC)
    async_raise_breaker_switch_area_notice(
        panel.hass,
        panel.entry,
        switch_entity_id=panel.entity_id("switch", original_switch),
        switch_unique_id=original_switch,
        area_name="Garage",
    )
    await panel.unload()
    panel.tree = after_tree

    with patch(
        "custom_components.span_panel.package_rating_manager.async_rekey_circuit_ids",
        side_effect=OSError("the store could not be written"),
    ):
        assert await panel.hass.config_entries.async_setup(panel.entry.entry_id) is False
        await panel.hass.async_block_till_done()
    assert panel.entry.state is ConfigEntryState.SETUP_ERROR
    row = er.async_get(panel.hass).async_get(panel.power(new_id_e(RANGE)))
    assert row is not None and row.previous_unique_id == build_circuit_unique_id(
        panel.serial, RANGE, "instantPowerW"
    ), "the registry was re-keyed before the store failed"

    await panel.restart_on(after_tree)

    assert not is_standing(panel.hass, panel.entry, _NEW_ENTITIES_NOTICE)
    issues = ir.async_get(panel.hass)
    moved = build_switch_unique_id(panel.serial, new_id_e(HVAC))
    assert issues.async_get_issue(
        DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, moved)
    ), "the notice followed its switch at the setup that finished the job"
    assert not issues.async_get_issue(
        DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, original_switch)
    )


# ---------------------------------------------------------------------------
# old ids in services
# ---------------------------------------------------------------------------


async def test_a_service_naming_a_circuits_old_id_reaches_it(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An automation that names `circuit-5` after the update sets `...-1-16-5`, with a WARNING.

    The WARNING says what to send instead: the circuit's entity, to a service
    that takes one, and the new id to a graph-horizon service, which takes an
    id only. Fails when the resolver's raw-id branch or the graph-horizon
    handlers stop asking `current_circuit_id`, or either is given the other's
    advice.
    """
    panel, _, _ = await install_and_update(panels, hass_storage)
    caplog.clear()

    await panel.call("set_circuit_threshold", circuit_id=RANGE, spike_threshold_pct=120)
    await panel.call("set_circuit_graph_horizon", circuit_id=RANGE, horizon="1w")

    assert panel.monitoring(new_id_e(RANGE))["spike_threshold_pct"] == 120
    horizons = panel.runtime.coordinator.graph_horizon_manager
    assert horizons is not None
    assert horizons.get_effective_horizon(new_id_e(RANGE)) == "1w"
    assert RANGE not in horizons.get_all_settings()["circuits"]
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == MAPPING_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 2, warnings
    assert all(RANGE in line and new_id_e(RANGE) in line for line in warnings)
    assert [line.split(" ", 1)[0] for line in warnings] == [
        "span_panel.set_circuit_threshold",
        "span_panel.set_circuit_graph_horizon",
    ]
    threshold, horizon = warnings
    assert threshold.endswith(
        "Name the circuit's entity instead, which keeps working across a change of device id"
    ), threshold
    assert horizon.endswith(
        f"Name it {new_id_e(RANGE)} instead: this action takes a circuit id, not an entity"
    ), horizon


# ---------------------------------------------------------------------------
# the mapping itself
# ---------------------------------------------------------------------------


def panel_of(*circuit_ids: str) -> SpanPanelSnapshot:
    """Return a panel holding these circuits."""
    return SpanPanelSnapshotFactory.create(
        circuits={cid: SpanCircuitSnapshotFactory.create(circuit_id=cid) for cid in circuit_ids}
    )


class TestPreviousDeviceIds:
    """The pairing reads the trait suffix and nothing else.

    Fails when the anchor is loosened, a prefix check is added, or the duplicate
    guard is removed.
    """

    def test_a_branch_circuit_and_a_remote_ct_pair_by_trait_instance(self) -> None:
        """`-1-16-<n>` pairs with `circuit-<n>`, and `-1-42-<n>` with `remote-ct-<n>`."""
        assert previous_device_ids(panel_of("p-1-16-5", "p-1-42-3")) == {
            "circuit-5": "p-1-16-5",
            "remote-ct-3": "p-1-42-3",
        }

    def test_a_prefix_that_is_not_the_root_still_pairs(self) -> None:
        """The prefix is never checked against the root's id."""
        assert previous_device_ids(panel_of("not-the-root-0123-1-16-9")) == {
            "circuit-9": "not-the-root-0123-1-16-9"
        }

    def test_a_serial_containing_hyphens_still_pairs(self) -> None:
        """Only the trait suffix is read, so hyphens in front of it are nobody's concern."""
        assert previous_device_ids(panel_of("ab-12-cd-34-ffffffffffffffff-1-16-12")) == {
            "circuit-12": "ab-12-cd-34-ffffffffffffffff-1-16-12"
        }

    @pytest.mark.parametrize(
        "circuit_id",
        [
            "p-1-17-5",
            "1-16-5",
            "p-1-16-5a",
            "p-1-16-",
            "p-2-16-5",
            "0123456789abcdef0123456789abcdef",
            "circuit-5",
            "p-1-16-5\n",
        ],
    )
    def test_anything_else_pairs_nothing(self, circuit_id: str) -> None:
        """Another trait, no prefix, a trailing non-digit, an opaque id, an old id: no pair."""
        assert previous_device_ids(panel_of(circuit_id)) == {}

    def test_two_ids_claiming_one_trait_instance_refuse_the_whole_mapping(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A guess between them would carry one circuit's history onto another.

        The refusal comes back with the pairing, and nothing is logged: the
        caller warns of it, once.
        """
        pairing = pair_device_ids(panel_of("a-1-16-5", "b-1-16-5", "a-1-16-6"))
        assert pairing == DeviceIdPairing({}, TraitConflict("circuit-5", "a-1-16-5", "b-1-16-5"))
        assert previous_device_ids(panel_of("a-1-16-5", "b-1-16-5")) == {}
        assert not caplog.records

    def test_every_capture_of_a_new_tree_pairs_each_of_its_circuits(self) -> None:
        """Every circuit of every r202645 capture has an old id to pair with."""
        for name in ("r202645-c", "r202645-e", "r202645-g"):
            circuits = snapshot(capture(name).tree()).circuits
            assert sorted(previous_device_ids(snapshot(capture(name).tree())).values()) == sorted(
                circuits
            ), name

    def test_no_capture_of_an_older_tree_pairs_anything(self) -> None:
        """No r202639 or MAIN 32 capture holds an id of the new form."""
        for name in (
            "main32_r202633",
            "main32_r202639",
            "main32_r202645",
            "main32_r202645-cascade",
            "main32_r202639-upstream-pv",
            "r202639-a",
            "r202639-b",
            "r202639-c",
            "r202639-d",
            "r202639-e",
        ):
            assert previous_device_ids(snapshot(capture(name).tree())) == {}, name


class TestMigrationPending:
    """The guard's predicate: a missing session circuit with an old-form counterpart."""

    def test_new_ids_for_the_sessions_circuits(self) -> None:
        """The session's circuit is missing, and the snapshot pairs it with a new id."""
        assert migration_pending({"circuit-5"}, pair_device_ids(panel_of("p-1-16-5")))

    def test_a_circuit_gone_from_a_panel_on_new_ids(self) -> None:
        """A circuit missing from a panel already on new ids has no counterpart."""
        assert not migration_pending({"p-1-16-6"}, pair_device_ids(panel_of("p-1-16-5")))

    def test_a_circuit_gone_from_a_panel_on_opaque_ids(self) -> None:
        """A circuit missing from a panel on opaque ids has no counterpart either."""
        opaque = "0123456789abcdef0123456789abcdef"
        assert not migration_pending({"x"}, pair_device_ids(panel_of(opaque)))

    def test_a_refused_pairing(self) -> None:
        """Two new ids claiming one trait instance pair nothing, so nothing is pending."""
        refused = pair_device_ids(panel_of("a-1-16-5", "b-1-16-5"))
        assert not migration_pending({"circuit-5"}, refused)


class TestCurrentCircuitId:
    """What a service call's raw id means now."""

    @pytest.mark.parametrize(
        ("takes_entity", "advice"),
        [
            (
                True,
                "Name the circuit's entity instead, which keeps working across a change of device id",
            ),
            (False, "Name it p-1-16-5 instead: this action takes a circuit id, not an entity"),
        ],
        ids=["a-service-that-takes-entities", "a-service-that-takes-ids-only"],
    )
    def test_an_old_id_answers_its_new_one(
        self, caplog: pytest.LogCaptureFixture, takes_entity: bool, advice: str
    ) -> None:
        """Applied to the circuit under its new id, and logged with both ids and what to send instead.

        Only a service that takes the circuit's entity is told to name it; one
        that takes ids only would store the entity as an id no circuit has.
        Fails when every service is given the same advice.
        """
        live = panel_of("p-1-16-5")
        assert (
            current_circuit_id(live, "circuit-5", service="s", takes_entity=takes_entity)
            == "p-1-16-5"
        )
        (record,) = caplog.records
        line = record.getMessage()
        assert "circuit-5" in line and "p-1-16-5" in line
        assert line.endswith(advice), line
        assert ("entity instead" in line) is takes_entity

    def test_a_live_id_and_an_unknown_one_answer_themselves(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Only an old id the snapshot pairs is rewritten, and nothing else is logged."""
        live = panel_of("p-1-16-5")
        for takes_entity in (True, False):
            assert (
                current_circuit_id(live, "p-1-16-5", service="s", takes_entity=takes_entity)
                == "p-1-16-5"
            )
            assert (
                current_circuit_id(live, "circuit-6", service="s", takes_entity=takes_entity)
                == "circuit-6"
            )
            assert current_circuit_id(
                live, "sensor.kitchen_power", service="s", takes_entity=takes_entity
            ) == ("sensor.kitchen_power")
        assert not caplog.records


_SERIAL: Final = "nj-0000-00000"
"""A synthetic panel serial for the registry-level tests."""

_LATE_PAIR: Final = {"circuit-12": f"{_SERIAL}-0123456789abcdef-1-16-12"}


class TestHoldsOldIds:
    """What the guard asks of an entry's registries about circuits that arrive under new ids."""

    @staticmethod
    def entry(hass: HomeAssistant, entry_id: str = "entry-holds") -> tuple[MockConfigEntry, str]:
        """Add an entry with its panel's device; return it and the panel device's registry id."""
        entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id, unique_id=_SERIAL)
        entry.add_to_hass(hass)
        panel = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, _SERIAL)}
        )
        return entry, panel.id

    async def test_nothing_under_the_old_id(self, hass: HomeAssistant) -> None:
        """An entry holding only another circuit's row holds nothing under this one."""
        entry, panel = self.entry(hass)
        er.async_get(hass).async_get_or_create(
            "sensor",
            DOMAIN,
            build_circuit_unique_id(_SERIAL, "circuit-13", "instantPowerW"),
            config_entry=entry,
            device_id=panel,
        )
        assert not holds_old_ids(hass, entry.entry_id, _SERIAL, _LATE_PAIR)

    async def test_the_circuits_own_device(self, hass: HomeAssistant) -> None:
        """Its device alone counts: its rows may be gone while it was missing."""
        entry, panel = self.entry(hass)
        dr.async_get(hass).async_get_or_create_child(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, build_circuit_device_identifier(_SERIAL, "circuit-12"))},
            parent_device_id=panel,
        )
        assert holds_old_ids(hass, entry.entry_id, _SERIAL, _LATE_PAIR)

    async def test_the_card_of_an_inverter_it_feeds(self, hass: HomeAssistant) -> None:
        """The card keyed by the feeding circuit's id counts, as the migration re-keys it."""
        entry, _ = self.entry(hass)
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, pv_inverter_device_identifier(_SERIAL, "circuit-12"))},
        )
        assert holds_old_ids(hass, entry.entry_id, _SERIAL, _LATE_PAIR)

    @pytest.mark.parametrize(
        ("domain", "unique_id"),
        [
            ("sensor", build_circuit_unique_id(_SERIAL, "circuit-12", "instantPowerW")),
            ("switch", build_switch_unique_id(_SERIAL, "circuit-12")),
            ("sensor", f"span_{_SERIAL}_adopted_circuit_circuit-12/vendor/reading"),
        ],
        ids=["a-sensor", "its-breaker-switch", "a-vendor-extension"],
    )
    async def test_a_row_on_the_panels_device(
        self, hass: HomeAssistant, domain: str, unique_id: str
    ) -> None:
        """A row built for the old id counts on the panel's device, as an install from before circuit devices has it."""
        entry, panel = self.entry(hass)
        er.async_get(hass).async_get_or_create(
            domain, DOMAIN, unique_id, config_entry=entry, device_id=panel
        )
        assert holds_old_ids(hass, entry.entry_id, _SERIAL, _LATE_PAIR)

    async def test_another_entrys_row(self, hass: HomeAssistant) -> None:
        """Only this entry's registries are asked."""
        entry, _ = self.entry(hass)
        other, other_panel = self.entry(hass, "entry-other")
        er.async_get(hass).async_get_or_create(
            "sensor",
            DOMAIN,
            build_circuit_unique_id(_SERIAL, "circuit-12", "instantPowerW"),
            config_entry=other,
            device_id=other_panel,
        )
        assert not holds_old_ids(hass, entry.entry_id, _SERIAL, _LATE_PAIR)


def test_a_value_under_the_new_id_wins_over_the_old() -> None:
    """The stores' collision rule: a value set after the update outranks the one it replaces."""
    assert rekeyed({"old": 1, "new": 2, "other": 3}, {"old": "new"}) == ({"new": 2, "other": 3}, 1)
    assert rekeyed({"old": 1, "other": 3}, {"old": "new"}) == ({"other": 3, "new": 1}, 1)
    assert rekeyed({"other": 3}, {"old": "new"}) == ({"other": 3}, 0)


# ---------------------------------------------------------------------------
# the live transition, and the guard staying silent
# ---------------------------------------------------------------------------


def entity_states(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, tuple[object, ...]]:
    """Every entity's state as written: its value, attributes and when it last changed."""
    states: dict[str, tuple[object, ...]] = {}
    for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        state = hass.states.get(row.entity_id)
        if state is not None:
            states[row.entity_id] = (state.state, dict(state.attributes), state.last_updated)
    return states


def with_excluded_circuit_over_its_breaker(tree: RetainedTree, circuit_id: str) -> RetainedTree:
    """Return the tree with one circuit drawing 25 A on its 20 A breaker: an alert, unless excluded."""
    tree[circuit_id]["meter/current"] = "25.0"
    return tree


@pytest.mark.parametrize("path", ["push", "poll"])
async def test_the_panel_updating_under_a_running_session_reloads_it(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    """A snapshot naming the session's circuits by new ids is dropped, and the entry rebuilt.

    Nothing reads it: no store written, no reconcile, no alert -- for the
    circuit the user excluded from monitoring, which defaults would judge -- and
    no entity updated. One rebuild is asked for however often it arrives, and
    the setup it runs carries everything over, as a restart onto the new ids does.

    Fails when the guard is removed, placed after `_reconcile_package_ratings`
    or after `async_set_updated_data`, or asks through `request_reload`.
    """
    caplog.set_level(logging.INFO)
    panel = panels(capture(BEFORE).tree())
    seed_package_removal(hass_storage, panel.entry.entry_id)
    await panel.set_up(capture(BEFORE).tree())
    bathroom = await seed_settings(panel)
    before = registered(panel.hass, panel.entry)
    flush_delayed_saves(panel.hass)
    await panel.hass.async_block_till_done()
    written = stores(hass_storage, panel.entry.entry_id)
    states = entity_states(panel.hass, panel.entry)
    held = panel.runtime.coordinator.data
    updated = with_excluded_circuit_over_its_breaker(capture(AFTER).tree(), new_id_e(RANGE))
    # What the panel serves from now on, the rebuild's client included.
    panel.tree = updated
    rebuilds: list[str] = []
    manager = panel.runtime.coordinator.package_rating_manager

    with (
        patch(
            "custom_components.span_panel.coordinator.async_request_rebuild",
            side_effect=lambda _hass, entry_id: rebuilds.append(entry_id),
        ),
        patch("custom_components.span_panel.current_monitor.dispatch_alert") as alerts,
        patch.object(manager, "reconcile", wraps=manager.reconcile) as reconcile,
    ):
        for _ in range(2):
            if path == "push":
                await panel.push(updated)
            else:
                panel.clients[-1].serve(replay(updated))
                await panel.runtime.coordinator.async_refresh()
                await panel.hass.async_block_till_done()
        flush_delayed_saves(panel.hass)
        await panel.hass.async_block_till_done()

    assert rebuilds == [panel.entry.entry_id]
    alerts.assert_not_called()
    reconcile.assert_not_called()
    assert panel.runtime.coordinator.data is held
    assert stores(hass_storage, panel.entry.entry_id) == written
    assert entity_states(panel.hass, panel.entry) == states
    assert (
        sum("now calls its circuits by new device ids" in r.getMessage() for r in caplog.records)
        == 1
    )

    # The rebuild it asked for, as `entry_reloads` runs it.
    async_request_rebuild(panel.hass, panel.entry.entry_id)
    await panel.hass.async_block_till_done()
    assert panel.entry.state is ConfigEntryState.LOADED
    await panel.push(updated)

    await assert_carried_over(panel, before, bathroom, caplog)


async def test_a_circuit_leaving_a_panel_on_new_ids_reloads_nothing(
    panels: Callable[..., Panel],
) -> None:
    """On a panel whose ids are new already, a circuit that goes is just gone.

    Fails when `migration_pending` drops its old-form test.
    """
    panel = panels(capture(AFTER).tree())
    await panel.set_up(capture(AFTER).tree())
    rebuilds: list[str] = []

    with patch(
        "custom_components.span_panel.coordinator.async_request_rebuild",
        side_effect=lambda _hass, entry_id: rebuilds.append(entry_id),
    ):
        await panel.push(without_device(capture(AFTER).tree(), new_id_e(RANGE)))

    assert rebuilds == []
    assert new_id_e(RANGE) not in panel.runtime.coordinator.data.circuits


async def test_a_main32_update_that_drops_a_circuit_behaves_as_today(
    panels: Callable[..., Panel],
) -> None:
    """MAIN 32's ids do not change: its firmware moving and a circuit going reload nothing.

    The circuit's copied package is removed as it always was, with the notice.
    Fails when `migration_pending` drops its old-form test or keys on the
    firmware version.
    """
    tree = capture("main32_r202639").tree()
    panel = panels(tree)
    await panel.set_up(tree)
    circuit_id, circuit = next(
        (cid, c)
        for cid, c in panel.snapshot.circuits.items()
        if len(c.tabs) == 1 and c.breaker_rating_a is not None
    )
    rating = int(circuit.breaker_rating_a or 0)
    await panel.call(
        "set_circuit_package_ratings",
        circuit_id=panel.power(circuit_id),
        package_ratings_a=[rating, rating],
    )
    manager = panel.runtime.coordinator.package_rating_manager
    assert manager.resolve(circuit_id, panel.snapshot).user_set
    updated = without_device(tree, circuit_id)
    updated[panel.serial]["info/firmware-version"] = "spanos3/r202645/02"
    rebuilds: list[str] = []

    with patch(
        "custom_components.span_panel.coordinator.async_request_rebuild",
        side_effect=lambda _hass, entry_id: rebuilds.append(entry_id),
    ):
        await panel.push(updated)

    assert rebuilds == []
    assert circuit_id not in manager.diagnostics(panel.runtime.coordinator.data)["packages"]
    assert is_standing(panel.hass, panel.entry, _PACKAGE_NOTICE)


LATE: Final = "circuit-12"
"""`Kitchen Sink Garbage Disposal`, 15 A on space 23: the circuit a first new tree lacks."""


def without_first_circuit(tree: RetainedTree) -> RetainedTree:
    """Return the tree without its first circuit by id: a circuit commissioned after it."""
    return without_device(tree, sorted(devices_of_type(tree, CIRCUIT_TYPE))[0])


def without_late_circuit(tree: RetainedTree) -> RetainedTree:
    """Return r202645-e without `LATE`, as the panel's first tree after the update may publish it."""
    return without_device(tree, new_id_e(LATE))


def with_late_circuit_over_its_breaker(tree: RetainedTree) -> RetainedTree:
    """Return r202645-e with `LATE` drawing 60 A on its 15 A breaker: an alert, unless excluded."""
    tree[new_id_e(LATE)]["meter/current"] = "60.0"
    return tree


async def test_a_circuit_the_first_new_tree_lacked_is_carried_over_when_it_arrives(
    panels: Callable[..., Panel],
) -> None:
    """The panel updates under a running session, first without `circuit-12`, then with it.

    The first new tree rebuilds the entry, whose setup migrates the 45 circuits
    it holds. When `circuit-12` arrives, at 60 A on its 15 A breaker, the
    snapshot carrying it is dropped and the entry rebuilt once more, so its
    rows read it under their own entity ids and the user's exclusion from
    monitoring holds: no alert, at any point.

    Fails when the guard judges only the circuits the session is keyed by.
    """
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    await panel.call(
        "set_circuit_threshold", circuit_id=panel.power(LATE), monitoring_enabled=False
    )
    late_rows = {
        key: entity_id
        for key, unique_id in circuit_entity_unique_ids(panel.serial, LATE).items()
        if (entity_id := er.async_get(panel.hass).async_get_entity_id(key[0], DOMAIN, unique_id))
    }
    assert len(late_rows) == 8, late_rows
    partial = without_late_circuit(capture(AFTER).tree())
    full = with_late_circuit_over_its_breaker(capture(AFTER).tree())

    with (
        patch(
            "custom_components.span_panel.coordinator.async_request_rebuild",
            wraps=async_request_rebuild,
        ) as rebuilds,
        patch("custom_components.span_panel.current_monitor.dispatch_alert") as alerts,
    ):
        panel.tree = partial
        await panel.push(partial)
        assert rebuilds.call_count == 1
        assert panel.entry.state is ConfigEntryState.LOADED
        assert new_id_e(LATE) not in panel.snapshot.circuits

        panel.tree = full
        await panel.push(full)
        assert rebuilds.call_count == 2
        assert panel.entry.state is ConfigEntryState.LOADED
        await panel.push(full)
        assert rebuilds.call_count == 2

    alerts.assert_not_called()
    assert new_id_e(LATE) in panel.snapshot.circuits
    entities = er.async_get(panel.hass)
    new_unique_ids = circuit_entity_unique_ids(panel.serial, new_id_e(LATE))
    for key, entity_id in late_rows.items():
        row = entities.async_get(entity_id)
        assert row is not None and row.unique_id == new_unique_ids[key], entity_id
    power = panel.hass.states.get(late_rows["sensor", "instantPowerW"])
    assert power is not None and power.state not in ("unavailable", "unknown")
    assert panel.monitoring(new_id_e(LATE))["monitoring_enabled"] is False


async def test_a_late_circuit_of_an_install_from_before_circuit_devices_is_carried_over(
    panels: Callable[..., Panel],
) -> None:
    """Its rows sit on the panel's device, with no circuit device to find, and still count.

    The first r202645-e setup lacks `circuit-12`, so its rows stay on the
    panel's device under the old ids. When it arrives, the entry is rebuilt,
    and that setup re-keys the rows and converts them onto the circuit's new
    device. Fails when the guard asks only the device registry.
    """
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    power = panel.power(LATE)
    await panel.unload()
    as_before_circuit_devices(panel)
    await panel.set_up(without_late_circuit(capture(AFTER).tree()))
    entities = er.async_get(panel.hass)
    row = entities.async_get(power)
    assert row is not None and row.unique_id == build_circuit_unique_id(
        panel.serial, LATE, "instantPowerW"
    )
    assert row.device_id == panel.runtime.panel_device_id

    with patch(
        "custom_components.span_panel.coordinator.async_request_rebuild",
        wraps=async_request_rebuild,
    ) as rebuilds:
        panel.tree = capture(AFTER).tree()
        await panel.push(capture(AFTER).tree())

    assert rebuilds.call_count == 1
    assert panel.entry.state is ConfigEntryState.LOADED
    row = entities.async_get(power)
    assert row is not None and row.unique_id == build_circuit_unique_id(
        panel.serial, new_id_e(LATE), "instantPowerW"
    )
    assert row.device_id == panel.circuit_device(new_id_e(LATE)).id


async def test_a_late_circuit_whose_rows_the_user_deleted_keeps_its_device(
    panels: Callable[..., Panel],
) -> None:
    """While `circuit-12` is missing its rows read nothing, and a user may delete them; its device stays.

    The device alone is what the entry holds under the old id, and it is
    enough: when the circuit arrives the entry is rebuilt, and the device is
    re-keyed with its name and room. Fails when the guard asks only the
    entity registry.
    """
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    kitchen = ar.async_get(panel.hass).async_create("Kitchen").id
    device = panel.circuit_device(LATE)
    dr.async_get(panel.hass).async_update_child_device(
        device.id, name_by_user="Disposal", area_id=kitchen
    )
    await panel.restart_on(without_late_circuit(capture(AFTER).tree()))
    entities = er.async_get(panel.hass)
    for row in er.async_entries_for_device(entities, device.id, include_disabled_entities=True):
        entities.async_remove(row.entity_id)
    assert panel.circuit_device(LATE).id == device.id

    with patch(
        "custom_components.span_panel.coordinator.async_request_rebuild",
        wraps=async_request_rebuild,
    ) as rebuilds:
        panel.tree = capture(AFTER).tree()
        await panel.push(capture(AFTER).tree())

    assert rebuilds.call_count == 1
    assert panel.entry.state is ConfigEntryState.LOADED
    carried = panel.circuit_device(new_id_e(LATE))
    assert (carried.id, carried.name_by_user, carried.area_id) == (device.id, "Disposal", kitchen)


@pytest.mark.parametrize(
    ("first", "then"),
    [
        pytest.param(
            lambda: without_late_circuit(capture(AFTER).tree()),
            lambda: capture(AFTER).tree(),
            id="a-new-circuit-on-new-ids",
        ),
        pytest.param(
            lambda: without_first_circuit(capture("main32_r202645").tree()),
            lambda: capture("main32_r202645").tree(),
            id="a-new-circuit-on-opaque-ids",
        ),
        pytest.param(
            lambda: capture(AFTER).tree(),
            lambda: capture(BEFORE).tree(),
            id="a-downgrade",
        ),
    ],
)
async def test_circuits_arriving_with_nothing_held_under_an_old_id_reload_nothing(
    panels: Callable[..., Panel],
    first: Callable[[], RetainedTree],
    then: Callable[[], RetainedTree],
) -> None:
    """A circuit the session does not know is just read, unless the entry holds its old id.

    A circuit commissioned after the update has an old-form counterpart that
    this entry holds nothing under; `main32_r202645`'s opaque ids and a
    downgraded panel's old ones pair nothing. Each snapshot is read as it is today. Fails when the
    guard rebuilds for a circuit whose old id the entry holds nothing under.
    """
    panel = panels(first())
    await panel.set_up(first())

    with patch(
        "custom_components.span_panel.coordinator.async_request_rebuild",
        wraps=async_request_rebuild,
    ) as rebuilds:
        await panel.push(then())

    rebuilds.assert_not_called()
    assert panel.snapshot.circuits.keys() == snapshot(then()).circuits.keys()


async def test_the_guard_judges_each_set_of_circuits_once(
    panels: Callable[..., Panel],
) -> None:
    """A settled panel costs no pairing and no registry look-up, however often it publishes.

    Pairing runs once for each new set of circuits a snapshot carries, and the
    registries are asked only about circuits whose new ids the session does
    not know. Fails when the guard pairs every snapshot, or asks the
    registries about pairs the session is keyed by.
    """
    tree = without_late_circuit(capture(AFTER).tree())
    panel = panels(tree)
    with patch(
        "custom_components.span_panel.holds_old_ids", wraps=holds_old_ids
    ) as registry_lookups:
        await panel.set_up(tree)
        with patch(
            "custom_components.span_panel.device_id_migration.pair_device_ids",
            wraps=pair_device_ids,
        ) as pairings:
            for _ in range(3):
                await panel.push(tree)
            settled = (pairings.call_count, registry_lookups.call_count)
            for _ in range(3):
                await panel.push(without_device(tree, new_id_e(RANGE)))
            leaving = (pairings.call_count, registry_lookups.call_count)
            for _ in range(3):
                await panel.push(capture(AFTER).tree())
            arriving = (pairings.call_count, registry_lookups.call_count)

    assert (settled, leaving, arriving) == ((0, 0), (1, 0), (2, 1))
    assert registry_lookups.call_args.args[-1] == {LATE: new_id_e(LATE)}


# ---------------------------------------------------------------------------
# a recommissioned panel
# ---------------------------------------------------------------------------


async def test_a_recommissioned_panels_circuits_follow_their_trait_instance(
    panels: Callable[..., Panel], caplog: pytest.LogCaptureFixture
) -> None:
    """`circuit-1`, -2, -3 and -5 pair by `<n>` whatever they are called now; `circuit-4` has no pair.

    Each pair whose names differ is logged once, naming both; the circuits the
    recommission added are announced as new. Fails when a name gate is added,
    or the WARNING is dropped.
    """
    before_tree = capture("r202639-c").tree()
    panel = panels(before_tree)
    await panel.set_up(before_tree)
    names = {cid: circuit.name for cid, circuit in panel.snapshot.circuits.items()}
    rows = {cid: er.async_get(panel.hass).async_get(panel.power(cid)) for cid in names}
    caplog.clear()

    await panel.restart_on(capture("r202645-c").tree())

    renamed = new_ids("r202645-c")
    after = panel.snapshot.circuits
    for n in (1, 2, 3, 5):
        old_id = f"circuit-{n}"
        new_id = renamed[old_id]
        row = er.async_get(panel.hass).async_get(panel.power(new_id))
        before_row = rows[old_id]
        assert row is not None and before_row is not None
        assert (row.id, row.entity_id) == (before_row.id, before_row.entity_id)
    left = er.async_get(panel.hass).async_get_entity_id(
        "sensor", DOMAIN, build_circuit_unique_id(panel.serial, "circuit-4", "instantPowerW")
    )
    assert left is not None, "circuit-4 has no new id, and its rows are left as they are"
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 4, warnings
    for n in (1, 2, 3, 5):
        old_id = f"circuit-{n}"
        new_id = renamed[old_id]
        (line,) = [line for line in warnings if f"Circuit {old_id} " in line]
        assert names[old_id] in line and new_id in line and str(after[new_id].name) in line
    assert is_standing(panel.hass, panel.entry, _NEW_ENTITIES_NOTICE)


async def test_a_pair_whose_new_circuit_the_panel_has_not_named_yet_is_not_warned_of(
    panels: Callable[..., Panel], caplog: pytest.LogCaptureFixture
) -> None:
    """Names can lag the new tree, and a circuit without one has nothing to compare: no WARNING.

    Three circuits come up unnamed, as a rebuild may set up before their names
    arrive; they are carried over as every circuit is. A circuit the panel
    names otherwise is still warned of
    (`test_a_recommissioned_panels_circuits_follow_their_trait_instance`).
    Fails when an unnamed circuit is compared by its placeholder, its breaker
    positions.
    """
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    rows = {cid: panel.power(cid) for cid in (RANGE, BATH, HVAC)}
    lagging = capture(AFTER).tree()
    for old_id in rows:
        lagging = without_topics(lagging, new_id_e(old_id), "info/name")
    assert not any(snapshot(lagging).circuits[new_id_e(cid)].name for cid in rows)
    caplog.clear()

    await panel.restart_on(lagging)

    assert not [
        record.getMessage()
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno >= logging.WARNING
    ]
    entities = er.async_get(panel.hass)
    for old_id, entity_id in rows.items():
        row = entities.async_get(entity_id)
        assert row is not None and row.unique_id == build_circuit_unique_id(
            panel.serial, new_id_e(old_id), "instantPowerW"
        ), old_id


# ---------------------------------------------------------------------------
# MAIN 32 migrates nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("main32_r202639", "main32_r202645"),
        ("main32_r202633", "main32_r202633"),
        ("main32_r202639-upstream-pv", "main32_r202639-upstream-pv"),
    ],
)
async def test_a_main32_panel_migrates_nothing(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    caplog: pytest.LogCaptureFixture,
    first: str,
    second: str,
) -> None:
    """Its ids are opaque on every release, so nothing pairs and nothing moves.

    Fails when the expression matches a 32-digit hexadecimal id.
    """
    caplog.set_level(logging.INFO)
    panel = panels(capture(first).tree())
    await panel.set_up(capture(first).tree())
    before = registered(panel.hass, panel.entry)
    flush_delayed_saves(panel.hass)
    await panel.hass.async_block_till_done()
    written = stores(hass_storage, panel.entry.entry_id)

    pairings: list[DeviceIdPairing] = []

    def pairing(tree_snapshot: SpanPanelSnapshot) -> DeviceIdPairing:
        pairings.append(pair_device_ids(tree_snapshot))
        return pairings[-1]

    with patch("custom_components.span_panel.device_id_rekey.pair_device_ids", side_effect=pairing):
        await panel.restart_on(capture(second).tree())
    flush_delayed_saves(panel.hass)
    await panel.hass.async_block_till_done()

    assert pairings == [DeviceIdPairing({})]
    after = registered(panel.hass, panel.entry)
    assert {key: after.entities[key] for key in before.entities} == before.entities
    assert {key: after.devices[key] for key in before.devices} == before.devices
    assert not [record for record in caplog.records if record.name == MIGRATION_LOGGER]
    if first == second:
        assert after == before
        assert stores(hass_storage, panel.entry.entry_id) == written


# ---------------------------------------------------------------------------
# what an earlier release added after the update gives way
# ---------------------------------------------------------------------------


async def test_rows_an_earlier_release_added_under_the_new_ids_give_way(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An earlier release on the new firmware registered a second set; the originals win.

    Its entities (`_2`), its circuit devices -- one put in a room -- and its
    breaker-switch notices are removed, and the originals re-keyed: a notice
    the original had takes the newer one's place, and one with no original
    goes with its switch. A setting the user made under a new id outranks the
    one it would replace.

    Fails when the newer entities, devices or notices do not give way: Core
    refuses the re-key of an id already in use.
    """
    caplog.set_level(logging.INFO)
    panel = panels(capture(BEFORE).tree())
    seed_package_removal(hass_storage, panel.entry.entry_id)
    await panel.set_up(capture(BEFORE).tree())
    bathroom = await seed_settings(panel)
    before = registered(panel.hass, panel.entry)

    # An earlier release, set up on the new firmware: no migration.
    await panel.unload()
    with patch("custom_components.span_panel.async_migrate_device_ids"):
        await panel.set_up(capture(AFTER).tree())
    newer = registered(panel.hass, panel.entry)
    newer_devices = newer.devices.keys() - before.devices.keys()
    newer_rows = newer.entities.keys() - before.entities.keys()
    assert newer_devices and newer_rows
    kitchen = ar.async_get(panel.hass).async_create("Kitchen").id
    dr.async_get(panel.hass).async_update_child_device(
        panel.circuit_device(new_id_e(RANGE)).id, area_id=kitchen
    )
    newer_switch = build_switch_unique_id(panel.serial, new_id_e(HVAC))
    async_raise_breaker_switch_area_notice(
        panel.hass,
        panel.entry,
        switch_entity_id=panel.entity_id("switch", newer_switch),
        switch_unique_id=newer_switch,
        area_name="Kitchen",
    )
    # The two notices told apart by what the user did with each: the original
    # shown again, the newer one ignored. The original's state must survive.
    original_switch = build_switch_unique_id(panel.serial, HVAC)
    ir.async_ignore_issue(
        panel.hass,
        DOMAIN,
        breaker_switch_area_issue_id(panel.entry.entry_id, original_switch),
        False,
    )
    ir.async_ignore_issue(
        panel.hass, DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, newer_switch), True
    )
    # A newer notice with no original behind it: the range's newer switch, in
    # the room its newer device was given.
    newer_range_switch = build_switch_unique_id(panel.serial, new_id_e(RANGE))
    async_raise_breaker_switch_area_notice(
        panel.hass,
        panel.entry,
        switch_entity_id=panel.entity_id("switch", newer_range_switch),
        switch_unique_id=newer_range_switch,
        area_name="Kitchen",
    )
    await panel.call("set_circuit_graph_horizon", circuit_id=new_id_e(RANGE), horizon="1w")
    caplog.clear()

    await panel.restart_on(capture(AFTER).tree())

    after = registered(panel.hass, panel.entry)
    assert after.entities.keys() == before.entities.keys()
    assert {rid: eid for rid, (eid, _) in after.entities.items()} == {
        rid: eid for rid, (eid, _) in before.entities.items()
    }
    assert not newer_rows & after.entities.keys()
    assert after.devices.keys() == before.devices.keys()
    switch_issue = breaker_switch_area_issue_id(panel.entry.entry_id, newer_switch)
    issue = ir.async_get(panel.hass).async_get_issue(DOMAIN, switch_issue)
    assert issue is not None
    assert issue.dismissed_version is None, "the original's state, not the newer notice's"
    assert issue.translation_placeholders is not None
    assert "Garage" in issue.translation_placeholders["area"]
    assert (
        ir.async_get(panel.hass).async_get_issue(
            DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, newer_range_switch)
        )
        is None
    ), "the newer switch's notice gave way with its row, though no original took its place"
    bath = panel.circuit_device(new_id_e(BATH))
    assert (bath.name_by_user, bath.area_id) == ("Bath outlets", bathroom)
    assert panel.circuit_device(new_id_e(RANGE)).area_id is None
    horizons = panel.runtime.coordinator.graph_horizon_manager
    assert horizons is not None
    assert horizons.get_effective_horizon(new_id_e(RANGE)) == "1w"
    infos = [
        record.getMessage()
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno == logging.INFO
    ]
    assert len(infos) == 1, infos
    assert f"{len(newer_rows)} entities and {len(newer_devices)} devices" in infos[0]


# ---------------------------------------------------------------------------
# an install from before circuits had devices
# ---------------------------------------------------------------------------


def as_before_circuit_devices(panel: Panel) -> None:
    """Put the registry back as a release before circuit devices left it.

    Every circuit entity on the panel's device, named with its circuit's name
    in front of its label, and no circuit device. Derived from a real install,
    so every entity the platforms build is there, and the announcement record
    knows them all.
    """
    entities = er.async_get(panel.hass)
    devices = dr.async_get(panel.hass)
    panel_device = devices.async_get_device_by_identifier(
        (DOMAIN, panel.serial), panel.entry.entry_id
    )
    assert panel_device is not None
    for child in dr.async_child_entries_for_config_entry(devices, panel.entry.entry_id):
        for row in er.async_entries_for_device(entities, child.id, include_disabled_entities=True):
            entities.async_update_entity(
                row.entity_id,
                device_id=panel_device.id,
                original_name=f"{child.name} {row.original_name}",
            )
        devices.async_remove_device(child.id)


@pytest.mark.parametrize("target", [BEFORE, AFTER])
async def test_an_install_from_before_circuit_devices_converts_under_the_new_ids(
    panels: Callable[..., Panel], target: str
) -> None:
    """The conversion runs on r202645-e as on r202639-e: the same lines, the same entity ids.

    Fails when the migration runs after `async_convert_circuit_entities`: the
    conversion would find no row for a circuit of the snapshot and leave them
    all on the panel's device.
    """
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    fridge = panel.power(RANGE)
    await panel.unload()
    as_before_circuit_devices(panel)
    er.async_get(panel.hass).async_update_entity(fridge, name="Fridge Power")
    entity_ids = {
        row.id: row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(panel.hass), panel.entry.entry_id)
    }

    await panel.set_up(capture(target).tree())

    entities = er.async_get(panel.hass)
    circuit_of = new_id_e if target == AFTER else str
    range_device = panel.circuit_device(circuit_of(RANGE))
    row = entities.async_get(fridge)
    assert row is not None and row.device_id == range_device.id
    on_the_panel = [
        row.entity_id
        for row in er.async_entries_for_config_entry(entities, panel.entry.entry_id)
        if row.device_id == panel.runtime.panel_device_id
        and names_an_old_id(row.unique_id, OLD_IDS_E)
    ]
    assert not on_the_panel
    assert {
        row.id: row.entity_id
        for row in er.async_entries_for_config_entry(entities, panel.entry.entry_id)
        if row.id in entity_ids
    } == entity_ids
    notice = ir.async_get(panel.hass).async_get_issue(
        DOMAIN, f"circuit_devices_converted_{panel.entry.entry_id}"
    )
    assert notice is not None and notice.translation_placeholders is not None
    assert notice.translation_placeholders["renamed"] == (
        f"- {PANEL_NAME} Fridge Power → {PANEL_NAME} Electric range Fridge Power"
    )
    assert not is_standing(panel.hass, panel.entry, _NEW_ENTITIES_NOTICE)


async def test_the_circuit_devices_an_earlier_release_added_are_kept_where_no_original_exists(
    panels: Callable[..., Panel],
) -> None:
    """An install from before circuit devices, which an earlier 2.3 release ran across the update.

    That release found every circuit entity on the panel's device under an old
    id, so it converted none and registered a second set under the new ids, on
    circuit devices of its own -- the only circuit devices there are. Those are
    kept, with the room the user gave one: the second set of entities gives
    way, with the breaker-switch notice raised about one of them, and the
    conversion moves the originals, re-keyed, onto those devices. Nothing is
    announced: every entity there is one the install had.

    Fails when a newer device with no original is removed as though it
    collided. Home Assistant would restore such a device from its tombstone,
    with its registry id and room, when the conversion asks for it again, so
    the registry alone cannot show it went; the removal events do.
    """
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    power = panel.power(RANGE)
    await panel.unload()
    as_before_circuit_devices(panel)
    entities = er.async_get(panel.hass)
    devices = dr.async_get(panel.hass)
    originals = {
        row.id: row.entity_id
        for row in er.async_entries_for_config_entry(entities, panel.entry.entry_id)
    }
    assert not dr.async_child_entries_for_config_entry(devices, panel.entry.entry_id)

    # The earlier release, on the new firmware: no migration.
    with patch("custom_components.span_panel.async_migrate_device_ids"):
        await panel.set_up(capture(AFTER).tree())
    newer_devices = {
        child.id for child in dr.async_child_entries_for_config_entry(devices, panel.entry.entry_id)
    }
    newer_rows = {
        row.id for row in er.async_entries_for_config_entry(entities, panel.entry.entry_id)
    } - originals.keys()
    assert newer_devices and newer_rows
    kitchen = ar.async_get(panel.hass).async_create("Kitchen").id
    range_device = panel.circuit_device(new_id_e(RANGE))
    devices.async_update_child_device(range_device.id, area_id=kitchen)
    # A notice the earlier release raised about its own bath switch, which
    # gives way to the original.
    newer_bath_switch = build_switch_unique_id(panel.serial, new_id_e(BATH))
    async_raise_breaker_switch_area_notice(
        panel.hass,
        panel.entry,
        switch_entity_id=panel.entity_id("switch", newer_bath_switch),
        switch_unique_id=newer_bath_switch,
        area_name="Bathroom",
    )
    removals = async_capture_events(panel.hass, dr.EVENT_DEVICE_REGISTRY_UPDATED)

    with patch(
        "custom_components.span_panel.additions.async_raise", wraps=raise_notice
    ) as announced:
        await panel.restart_on(capture(AFTER).tree())

    assert not [event for event in removals if event.data["action"] == "remove"]
    assert not announced.called, "nothing is new, so nothing is announced"

    rows = {
        row.id: row for row in er.async_entries_for_config_entry(entities, panel.entry.entry_id)
    }
    assert {registry_id: rows[registry_id].entity_id for registry_id in originals} == originals
    assert not newer_rows & rows.keys()
    assert {
        child.id for child in dr.async_child_entries_for_config_entry(devices, panel.entry.entry_id)
    } == newer_devices
    moved = entities.async_get(power)
    assert moved is not None and moved.device_id == range_device.id
    assert panel.circuit_device(new_id_e(RANGE)).area_id == kitchen
    assert not [
        row.entity_id
        for row in rows.values()
        if row.device_id == panel.runtime.panel_device_id
        and names_an_old_id(row.unique_id, OLD_IDS_E)
    ]
    assert (
        ir.async_get(panel.hass).async_get_issue(
            DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, newer_bath_switch)
        )
        is None
    ), "the newer bath switch gave way, and its notice with it"


async def test_a_switchs_notice_moves_only_with_its_switch(panels: Callable[..., Panel]) -> None:
    """The user deleted HVAC's dead breaker switch while an earlier release ran across the update.

    An install from before circuit devices, as in the test above, whose HVAC
    switch had an area notice. The earlier release registered a second switch
    under the new id, on a circuit device of its own, with a notice of its
    own; the original, reading nothing, was deleted. With no original to take
    its place the newer switch is kept, and so is its notice: the deleted
    switch's notice is not moved onto it, and stays where it was. Fails when
    a notice moves whether or not its switch did.
    """
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    original_switch = build_switch_unique_id(panel.serial, HVAC)
    async_raise_breaker_switch_area_notice(
        panel.hass,
        panel.entry,
        switch_entity_id=panel.entity_id("switch", original_switch),
        switch_unique_id=original_switch,
        area_name="Garage",
    )
    original = panel.entity_id("switch", original_switch)
    await panel.unload()
    as_before_circuit_devices(panel)
    with patch("custom_components.span_panel.async_migrate_device_ids"):
        await panel.set_up(capture(AFTER).tree())
    er.async_get(panel.hass).async_remove(original)
    newer_switch = build_switch_unique_id(panel.serial, new_id_e(HVAC))
    newer = panel.entity_id("switch", newer_switch)
    async_raise_breaker_switch_area_notice(
        panel.hass,
        panel.entry,
        switch_entity_id=newer,
        switch_unique_id=newer_switch,
        area_name="Kitchen",
    )

    await panel.restart_on(capture(AFTER).tree())

    assert panel.entity_id("switch", newer_switch) == newer
    issues = ir.async_get(panel.hass)
    kept = issues.async_get_issue(
        DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, newer_switch)
    )
    assert kept is not None and kept.translation_placeholders is not None
    assert kept.translation_placeholders["area"] == "Kitchen"
    assert newer in kept.translation_placeholders["switch"]
    assert (
        issues.async_get_issue(
            DOMAIN, breaker_switch_area_issue_id(panel.entry.entry_id, original_switch)
        )
        is not None
    )


# ---------------------------------------------------------------------------
# A remote CT, and the panel it is on, before and after
# ---------------------------------------------------------------------------


async def test_a_remote_ct_and_its_panel_carry_over_and_what_is_new_is_announced(
    panels: Callable[..., Panel], caplog: pytest.LogCaptureFixture
) -> None:
    """`r202639-a` to `r202645-a`: `remote-ct-3` becomes `{prefix}-1-42-3` and keeps everything.

    Its sensors and its own device, `Remote CT 3`, keep their registry ids and
    entity ids, and its label does not change. `circuit-3` and `circuit-4` keep
    theirs too, and are logged once each, because the panel dropped the number
    in front of their names. The three circuits and the charger the panel
    gained are new, and announced as new.

    Fails when the `42` family is dropped from the pairing, or the label stops
    reading the trailing number.
    """
    caplog.set_level(logging.WARNING)
    renamed = new_ids("r202645-a")
    before_tree = capture("r202639-a").tree()
    panel = panels(before_tree)
    await panel.set_up(before_tree)
    entities = er.async_get(panel.hass)
    kept = ("remote-ct-3", "circuit-3", "circuit-4")
    devices = {old_id: panel.circuit_device(old_id) for old_id in kept}
    rows = {
        old_id: {row.id: row.entity_id for row in er.async_entries_for_device(entities, device.id)}
        for old_id, device in devices.items()
    }
    assert all(rows.values())
    assert devices["remote-ct-3"].name == "Remote CT 3"
    registered = {
        row.id for row in er.async_entries_for_config_entry(entities, panel.entry.entry_id)
    }
    caplog.clear()

    await panel.restart_on(capture("r202645-a").tree())

    for old_id in kept:
        moved = panel.circuit_device(renamed[old_id])
        assert moved.id == devices[old_id].id, old_id
        assert {
            row.id: row.entity_id for row in er.async_entries_for_device(entities, moved.id)
        } == rows[old_id], old_id
    assert panel.circuit_device(renamed["remote-ct-3"]).name == "Remote CT 3"
    assert outside_meter_label(renamed["remote-ct-3"]) == "Remote CT 3"

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == MIGRATION_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 2, warnings
    for old_id in ("circuit-3", "circuit-4"):
        (line,) = [line for line in warnings if f"Circuit {old_id} " in line]
        new_name = panel.snapshot.circuits[renamed[old_id]].name
        assert f"({devices[old_id].name})" in line and f"now names {new_name}" in line

    charger = {
        device.id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(panel.hass), panel.entry.entry_id
        )
        if any(
            identifier.endswith(f"_evse_{key}")
            for _, identifier in device.identifiers
            for key in panel.snapshot.evse
        )
    }
    # The charger's feed circuit has no device of its own: its sensors are on
    # the charger's, and it has neither a breaker switch nor a priority select.
    gained = {
        device.id
        for n in (1, 2, 5)
        if (
            device := dr.async_get(panel.hass).async_get_child_device_by_identifier(
                (DOMAIN, build_circuit_device_identifier(panel.serial, renamed[f"circuit-{n}"])),
                panel.entry.entry_id,
            )
        )
        is not None
    }
    added = {
        row.device_id
        for row in er.async_entries_for_config_entry(entities, panel.entry.entry_id)
        if row.id not in registered
    }
    assert charger and added == gained | charger
    assert is_standing(panel.hass, panel.entry, _NEW_ENTITIES_NOTICE)


# ---------------------------------------------------------------------------
# Derived trees: what no capture has
# ---------------------------------------------------------------------------

DERIVED_HEX: Final = "0123456789abcdef"
"""Stands in for the sixteen hexadecimal digits r202645 puts after a panel's root id."""


def on_new_ids(tree: RetainedTree, renames: Mapping[str, str]) -> RetainedTree:
    """Return the tree re-ided as r202645 does it, each circuit declaring where it is connected."""
    renamed = renamed_devices(tree, renames)
    return with_connection_point(
        renamed,
        {
            new_id: "MONITORED" if old_id.startswith("remote-ct-") else "BRANCH"
            for old_id, new_id in renames.items()
        },
    )


def fed_inverters(tree: RetainedTree, feeds: Mapping[str, str]) -> RetainedTree:
    """Return the tree with each circuit of `feeds` feeding its inverter, by the connection it declares."""
    for circuit_id, inverter in feeds.items():
        tree[circuit_id]["connection/feeds-device-id"] = inverter
        tree[circuit_id]["connection/feeds-device-status"] = "OK"
        tree[circuit_id]["connection/feeds-device-type"] = "energy.ebus.device.pv"
    return tree


async def test_an_inverter_a_circuit_feeds_follows_its_circuit(
    panels: Callable[..., Panel], hass_storage: dict[str, object]
) -> None:
    """On a panel with two inverters, each fed by a circuit, everything keyed by the feed follows.

    The bound record, the other inverter's card and entities, its curated
    extension key and its graph horizon. Fails when inverter devices are not
    re-keyed in place, or the binding or the curation is skipped.

    Derived from `main32_r202639-upstream-pv`, the one capture with two
    inverters. No circuit feeds them there, so their keys are their own device
    ids and nothing would follow a circuit; the derived tree has two of its
    circuits feed them, through the `connection/feeds-device-*` properties every
    circuit there already declares.
    """
    upstream = capture("main32_r202639-upstream-pv").tree()
    snap = snapshot(upstream)
    inverters = sorted(snap.pv_inverters)
    by_space = sorted(snap.circuits, key=lambda cid: min(snap.circuits[cid].tabs))
    root = panel_device_id(upstream)
    before_names = {cid: f"circuit-{min(snap.circuits[cid].tabs)}" for cid in by_space}
    feeding = dict(zip(by_space[:2], inverters, strict=True))
    fed = fed_inverters(upstream, feeding)
    before_tree = renamed_devices(fed, before_names)
    bound, carded = (before_names[cid] for cid in by_space[:2])
    after_ids = {
        old: f"{root}-{DERIVED_HEX}-1-16-{old.removeprefix('circuit-')}"
        for old in before_names.values()
    }
    after_tree = on_new_ids(before_tree, after_ids)

    panel = panels(before_tree)
    entry_id = panel.entry.entry_id
    hass_storage[f"{DOMAIN}.pv_binding.{entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.pv_binding.{entry_id}",
        "data": {"circuit_id": bound},
    }
    hass_storage[f"{DOMAIN}.curation.{entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.curation.{entry_id}",
        "data": {"records": {f"pv_{carded}/vendor/cell-temperature": {"entity_category": "none"}}},
    }
    await panel.set_up(before_tree)
    assert panel.runtime.pv_binding.bound_key == bound
    devices = dr.async_get(panel.hass)
    card = devices.async_get_device_by_identifier((DOMAIN, f"{panel.serial}_pv_{carded}"), entry_id)
    assert card is not None
    card_rows = {
        row.id: row.entity_id
        for row in er.async_entries_for_device(er.async_get(panel.hass), card.id)
    }
    assert card_rows
    await panel.call("set_subdevice_graph_horizon", subdevice_id=card.id, horizon="1M")

    await panel.restart_on(after_tree)

    assert panel.runtime.pv_binding.bound_key == after_ids[bound]
    moved = devices.async_get_device_by_identifier(
        (DOMAIN, f"{panel.serial}_pv_{after_ids[carded]}"), entry_id
    )
    assert moved is not None and moved.id == card.id
    assert {
        row.id: row.entity_id
        for row in er.async_entries_for_device(er.async_get(panel.hass), moved.id)
    } == card_rows
    assert panel.runtime.curation.as_dicts() == {
        f"pv_{after_ids[carded]}/vendor/cell-temperature": {"entity_category": "none"}
    }
    horizons = panel.runtime.coordinator.graph_horizon_manager
    assert horizons is not None
    assert horizons.get_effective_subdevice_horizon(card.id) == "1M"
    await assert_a_second_migration_writes_nothing(panel)


VENDOR_READINGS: Final = 61
"""One more than a device may add (`extension.MAX_PER_DEVICE`), so the overflow notice stands."""


def with_vendor_readings(tree: RetainedTree, circuit_id: str) -> RetainedTree:
    """Return the tree with a vendor node on one circuit, declaring and publishing `VENDOR_READINGS` readings."""
    parsed = dict(description(tree, circuit_id))
    nodes = dict(parsed["nodes"]) if isinstance(parsed.get("nodes"), dict) else {}
    nodes["vendor"] = {
        "name": "vendor",
        "type": "com.example.vendor",
        "properties": {
            f"reading-{i}": {"datatype": "float", "unit": "V", "name": f"Reading {i}"}
            for i in range(VENDOR_READINGS)
        },
    }
    parsed["nodes"] = nodes
    tree[circuit_id]["$description"] = json.dumps(parsed)
    for i in range(VENDOR_READINGS):
        tree[circuit_id][f"vendor/reading-{i}"] = f"{120 + i}.0"
    return tree


async def test_a_circuits_vendor_extension_and_its_overflow_notice_follow_it(
    panels: Callable[..., Panel],
    hass_storage: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its entities, its curated key and the overflow notice's record move; the notice is not raised again.

    Fails when the notices re-key is skipped: the overflow would read as news
    and be raised again, naming the new id.
    """
    caplog.set_level(logging.WARNING)
    before_tree = with_vendor_readings(capture(BEFORE).tree(), RANGE)
    after_tree = with_vendor_readings(capture(AFTER).tree(), new_id_e(RANGE))
    panel = panels(before_tree)
    await panel.set_up(before_tree)
    prefix = f"span_{panel.serial}_adopted_circuit_{RANGE}/"
    rows = {
        row.id: row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(panel.hass), panel.entry.entry_id)
        if row.unique_id.startswith(prefix)
    }
    assert len(rows) == VENDOR_READINGS - 1
    notice_id = "extension_overflow"
    assert is_standing(panel.hass, panel.entry, notice_id)
    flush_delayed_saves(panel.hass)
    await panel.hass.async_block_till_done()
    notices_key = f"{_NOTICES_KEY}.{panel.entry.entry_id}"
    standing = json.dumps(hass_storage[notices_key], sort_keys=True)
    assert f"circuit:{RANGE} (1)" in standing
    curation = hass_storage.setdefault(
        f"{DOMAIN}.curation.{panel.entry.entry_id}",
        {"version": 1, "key": f"{DOMAIN}.curation.{panel.entry.entry_id}", "data": {"records": {}}},
    )
    assert isinstance(curation, dict)
    curation["data"] = {
        "records": {f"circuit_{RANGE}/vendor/reading-0": {"entity_category": "none"}}
    }
    caplog.clear()

    await panel.restart_on(after_tree)
    flush_delayed_saves(panel.hass)
    await panel.hass.async_block_till_done()

    new_prefix = f"span_{panel.serial}_adopted_circuit_{new_id_e(RANGE)}/"
    after = {
        row.id: row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(panel.hass), panel.entry.entry_id)
        if row.unique_id.startswith(new_prefix)
    }
    assert after == rows
    assert panel.runtime.curation.as_dicts() == {
        f"circuit_{new_id_e(RANGE)}/vendor/reading-0": {"entity_category": "none"}
    }
    stored = hass_storage[notices_key]
    assert isinstance(stored, dict)
    assert stored["data"]["announced"][notice_id] == f"circuit:{new_id_e(RANGE)} (1)"
    assert (
        stored["data"]["standing"][notice_id] == json.loads(standing)["data"]["standing"][notice_id]
    )
    assert "Vendor readings beyond the per-device limit" not in caplog.text
    await assert_a_second_migration_writes_nothing(panel)


@pytest.mark.parametrize("heal", ["arriving", "restarting"])
async def test_circuits_a_partial_tree_lacks_migrate_once_they_arrive(
    panels: Callable[..., Panel], heal: str
) -> None:
    """Three circuits missing from the first r202645-e setup migrate when the panel publishes them.

    Within the session: the snapshot that brings them is dropped and the entry
    rebuilt once, as for the update itself. Or at the next setup, when Home
    Assistant restarts first. Fails when the migration needs the whole tree or
    records itself as done, or when the guard judges only the circuits the
    session is keyed by.
    """
    late = ("circuit-12", "circuit-13", "circuit-14")
    partial = capture(AFTER).tree()
    for old_id in late:
        partial = without_device(partial, new_id_e(old_id))
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    old_power = {
        cid: build_circuit_unique_id(panel.serial, cid, "instantPowerW") for cid in (*late, RANGE)
    }
    rows = {cid: panel.entity_id("sensor", uid) for cid, uid in old_power.items()}

    await panel.restart_on(partial)

    entities = er.async_get(panel.hass)
    for cid in late:
        row = entities.async_get(rows[cid])
        assert row is not None and row.unique_id == old_power[cid], cid
    moved = entities.async_get(rows[RANGE])
    assert moved is not None and new_id_e(RANGE) in moved.unique_id

    with patch(
        "custom_components.span_panel.coordinator.async_request_rebuild",
        wraps=async_request_rebuild,
    ) as rebuilds:
        if heal == "arriving":
            panel.tree = capture(AFTER).tree()
            await panel.push(capture(AFTER).tree())
            assert rebuilds.call_count == 1
            assert panel.entry.state is ConfigEntryState.LOADED
            await panel.push(capture(AFTER).tree())
        else:
            await panel.restart_on(capture(AFTER).tree())
        assert rebuilds.call_count == (1 if heal == "arriving" else 0)

    for cid in late:
        row = entities.async_get(rows[cid])
        assert row is not None and row.unique_id == build_circuit_unique_id(
            panel.serial, new_id_e(cid), "instantPowerW"
        ), cid
        state = panel.hass.states.get(rows[cid])
        assert state is not None and state.state not in ("unavailable", "unknown"), cid


async def test_a_control_withdrawn_before_the_update_returns_as_a_new_entity(
    panels: Callable[..., Panel],
) -> None:
    """Tombstones are not re-keyed: a switch the panel withdrew comes back new, and is announced.

    A characterisation of the accepted gap. Fails when a change starts
    re-keying Core's `deleted_entities`.
    """
    tree = capture(BEFORE).tree()
    panel = panels(tree)
    await panel.set_up(tree)
    switch_uid = build_switch_unique_id(panel.serial, RANGE)
    original = er.async_get(panel.hass).async_get(panel.entity_id("switch", switch_uid))
    assert original is not None
    withdrawn = capture(BEFORE).tree()
    withdrawn[RANGE]["switch/relay-controllable"] = "false"
    await panel.restart_on(withdrawn)
    assert er.async_get(panel.hass).async_get_entity_id("switch", DOMAIN, switch_uid) is None

    await panel.restart_on(capture(AFTER).tree())

    returned = er.async_get(panel.hass).async_get(
        panel.entity_id("switch", build_switch_unique_id(panel.serial, new_id_e(RANGE)))
    )
    assert returned is not None and returned.id != original.id
    assert is_standing(panel.hass, panel.entry, _NEW_ENTITIES_NOTICE)


async def test_an_inverter_card_an_earlier_release_added_gives_way(
    panels: Callable[..., Panel],
) -> None:
    """An inverter's card: the original card is re-keyed in place, the newer one removed."""
    upstream = capture("main32_r202639-upstream-pv").tree()
    snap = snapshot(upstream)
    by_space = sorted(snap.circuits, key=lambda cid: min(snap.circuits[cid].tabs))
    root = panel_device_id(upstream)
    before_names = {cid: f"circuit-{min(snap.circuits[cid].tabs)}" for cid in by_space}
    fed = fed_inverters(upstream, dict(zip(by_space[:2], sorted(snap.pv_inverters), strict=True)))
    before_tree = renamed_devices(fed, before_names)
    after_ids = {
        old: f"{root}-{DERIVED_HEX}-1-16-{old.removeprefix('circuit-')}"
        for old in before_names.values()
    }
    after_tree = on_new_ids(before_tree, after_ids)
    panel = panels(before_tree)
    await panel.set_up(before_tree)
    devices = dr.async_get(panel.hass)
    cards = {
        key: devices.async_get_device_by_identifier(
            (DOMAIN, f"{panel.serial}_pv_{key}"), panel.entry.entry_id
        )
        for key in panel.snapshot.pv_inverters
    }
    originals = {key: card.id for key, card in cards.items() if card is not None}
    assert originals
    await panel.unload()
    with patch("custom_components.span_panel.async_migrate_device_ids"):
        await panel.set_up(after_tree)
    newer = {
        key: card.id
        for key in originals
        if (
            card := devices.async_get_device_by_identifier(
                (DOMAIN, f"{panel.serial}_pv_{after_ids[key]}"), panel.entry.entry_id
            )
        )
        is not None
    }
    assert newer.keys() == originals.keys()

    await panel.restart_on(after_tree)

    for key, device_id in originals.items():
        card = devices.async_get_device_by_identifier(
            (DOMAIN, f"{panel.serial}_pv_{after_ids[key]}"), panel.entry.entry_id
        )
        assert card is not None and card.id == device_id
        assert devices.async_get(newer[key]) is None


async def test_a_new_unique_id_another_entry_holds_is_left_alone(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A row is never re-keyed onto a unique id another entry's row holds: it is logged and kept."""
    ours = MockConfigEntry(domain=DOMAIN, entry_id="entry-ours", unique_id="sp3-x")
    theirs = MockConfigEntry(domain=DOMAIN, entry_id="entry-theirs", unique_id="sp3-y")
    ours.add_to_hass(hass)
    theirs.add_to_hass(hass)
    registry = er.async_get(hass)
    old = build_circuit_unique_id("sp3-x", "circuit-5", "instantPowerW")
    new = build_circuit_unique_id("sp3-x", "p-1-16-5", "instantPowerW")
    kept = registry.async_get_or_create("sensor", DOMAIN, old, config_entry=ours)
    registry.async_get_or_create("sensor", DOMAIN, new, config_entry=theirs)

    await async_migrate_device_ids(
        hass,
        ours,
        SpanPanelSnapshotFactory.create(
            serial_number="sp3-x",
            circuits={"p-1-16-5": SpanCircuitSnapshotFactory.create(circuit_id="p-1-16-5")},
        ),
    )

    row = registry.async_get(kept.entity_id)
    assert row is not None and row.unique_id == old
    assert f"its new unique id {new} is held by" in caplog.text


# ---------------------------------------------------------------------------
# A refused pairing
# ---------------------------------------------------------------------------


def conflicts(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return every warning that two new ids claimed one trait instance."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == MAPPING_LOGGER and "share trait instance" in record.getMessage()
    ]


async def test_a_refused_pairing_is_warned_of_once_a_session(
    panels: Callable[..., Panel], caplog: pytest.LogCaptureFixture
) -> None:
    """Two new ids claiming one trait instance migrate nothing, reload nothing, and warn once.

    Once a session, not once a snapshot: a running session that keeps being
    told the same thing is warned once, whichever circuits the snapshots carry
    besides, and a new session -- the setup after a restart -- once more. SPAN
    says the case cannot arise; the tree is derived.

    Fails when the guard warns at every snapshot or at every set of circuits
    it judges, or the pairing logs by itself.
    """
    caplog.set_level(logging.WARNING)
    after = capture(AFTER).tree()
    second = f"{panel_device_id(after)}-{DERIVED_HEX}-1-16-5"
    conflicting = renamed_devices(after, {new_id_e("circuit-6"): second})
    panel = panels(capture(BEFORE).tree())
    await panel.set_up(capture(BEFORE).tree())
    rebuilds: list[str] = []

    with patch(
        "custom_components.span_panel.coordinator.async_request_rebuild",
        side_effect=lambda _hass, entry_id: rebuilds.append(entry_id),
    ):
        # Three sets of circuits for the guard to judge, each refused.
        for tree in (conflicting, without_device(conflicting, new_id_e(BATH)), conflicting):
            await panel.push(tree)

    assert rebuilds == []
    assert len(conflicts(caplog)) == 1
    assert second in conflicts(caplog)[0]

    # A new session warns once more, at its setup. That setup builds entities
    # for circuits it could not pair, whose first update asks for one reload,
    # and the reload's setup is a session of its own; so the setups are counted.
    with patch(
        "custom_components.span_panel.device_id_rekey.pair_device_ids", wraps=pair_device_ids
    ) as setups:
        await panel.restart_on(conflicting)
        settled = len(conflicts(caplog))
        for _ in range(2):
            await panel.push(conflicting)

    assert setups.call_count >= 1
    assert settled == 1 + setups.call_count
    assert len(conflicts(caplog)) == settled
