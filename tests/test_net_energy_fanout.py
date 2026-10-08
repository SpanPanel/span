"""Net Energy reads its siblings' dip offsets on the very update that books them.

Through the real coordinator fan-out, with the entities built by the platform's
own factories in production order -- never in an order the test chooses, since
the order is what is under test. Net is created after Produced and Consumed,
listeners run in the order they were added, and so Net sees this update's
offsets rather than the previous update's. A PanelBench restart, which resets
every counter to zero, is the dip this models.
"""

from __future__ import annotations

import logging
from typing import Final
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockEntityPlatform
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import DOMAIN, ENABLE_ENERGY_DIP_COMPENSATION
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.sensor import create_native_sensors
from custom_components.span_panel.sensor_base import SpanEnergySensorBase
from custom_components.span_panel.sensor_circuit import SpanCircuitEnergySensor

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for

PV: Final = "pv-circuit"
LOAD: Final = "load-circuit"
SERIAL: Final = "sp3-fanout-001"


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """The real coordinator schedules its fallback poll on every pushed update."""
    return True


def _snapshot(
    *,
    pv: tuple[float, float],
    load: tuple[float, float],
    main: tuple[float, float],
    feed: tuple[float, float],
) -> SpanPanelSnapshot:
    """Each pair is (produced, consumed)."""
    return SpanPanelSnapshotFactory.create(
        serial_number=SERIAL,
        circuits={
            PV: SpanCircuitSnapshotFactory.create(
                circuit_id=PV,
                name="Solar",
                device_type="pv",
                tabs=[1, 3],
                produced_energy_wh=pv[0],
                consumed_energy_wh=pv[1],
            ),
            LOAD: SpanCircuitSnapshotFactory.create(
                circuit_id=LOAD,
                name="Kitchen",
                tabs=[2],
                produced_energy_wh=load[0],
                consumed_energy_wh=load[1],
            ),
        },
        main_meter_energy_produced_wh=main[0],
        main_meter_energy_consumed_wh=main[1],
        feedthrough_energy_produced_wh=feed[0],
        feedthrough_energy_consumed_wh=feed[1],
    )


BEFORE: Final = _snapshot(pv=(1000.0, 40.0), load=(30.0, 500.0), main=(300.0, 2500.0), feed=(20.0, 80.0))
# Every counter dropped by at least 1 Wh, as a PanelBench restart drops them all.
AFTER: Final = _snapshot(pv=(50.0, 4.0), load=(3.0, 20.0), main=(10.0, 100.0), feed=(1.0, 5.0))


def _key(entity: SpanEnergySensorBase[object, object]) -> str:
    if isinstance(entity, SpanCircuitEnergySensor):
        return f"{entity.circuit_id}:{entity.original_key}"
    return entity.entity_description.key


def _entry(hass: HomeAssistant, *, compensate: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device_name": "SPAN Panel"},
        options={ENABLE_ENERGY_DIP_COMPENSATION: compensate},
        title="SPAN Panel",
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)
    return entry


async def _built(
    hass: HomeAssistant, *, compensate: bool = True
) -> tuple[SpanPanelCoordinator, dict[str, SpanEnergySensorBase[object, object]]]:
    coordinator, energy, _ = await _set_up(hass, _entry(hass, compensate=compensate))
    return coordinator, energy


async def _set_up(
    hass: HomeAssistant, entry: MockConfigEntry
) -> tuple[SpanPanelCoordinator, dict[str, SpanEnergySensorBase[object, object]], MockEntityPlatform]:
    """Build the entry's sensors with the platform's own factories and add them, as a (re)load does."""
    coordinator = SpanPanelCoordinator(hass, MagicMock(), entry)
    coordinator.data = BEFORE
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(BEFORE),
        setup_snapshot=BEFORE,
    )
    entities = create_native_sensors(coordinator, BEFORE, entry)

    # Main Meter Net and the Feed Through sensors are disabled by default; the
    # rehearsal enables them, and so does this, before the platform sees them.
    registry = er.async_get(hass)
    for entity in entities:
        if entity.unique_id is not None and entity.entity_registry_enabled_default is False:
            registry.async_get_or_create(
                "sensor", DOMAIN, entity.unique_id, config_entry=entry, disabled_by=None
            )

    platform = MockEntityPlatform(
        hass, domain="sensor", platform_name=DOMAIN, logger=logging.getLogger(__name__)
    )
    platform.config_entry = entry
    await platform.platform_data.async_load_translations()
    await platform.async_add_entities(entities)

    energy = {_key(e): e for e in entities if isinstance(e, SpanEnergySensorBase)}
    return coordinator, energy, platform


def _reading(energy: dict[str, SpanEnergySensorBase[object, object]], key: str) -> float:
    value = energy[key].native_value
    assert isinstance(value, float), (key, value)
    return value


# Each compensated Net with the sibling it credits and the one it debits.
NETS: Final = (
    (f"{PV}:circuit_energy_net", f"{PV}:circuit_energy_produced", f"{PV}:circuit_energy_consumed"),
    (f"{LOAD}:circuit_energy_net", f"{LOAD}:circuit_energy_consumed", f"{LOAD}:circuit_energy_produced"),
    ("mainMeterNetEnergyWh", "mainMeterEnergyConsumedWh", "mainMeterEnergyProducedWh"),
)


def _entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    """Every registered entity of the entry, unique id to entity id."""
    return {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


def _nets(energy: dict[str, SpanEnergySensorBase[object, object]]) -> dict[str, float]:
    return {net: _reading(energy, net) for net, _, _ in NETS}


def _assert_every_net_on_its_siblings(energy: dict[str, SpanEnergySensorBase[object, object]]) -> None:
    for net, credit, debit in NETS:
        assert _reading(energy, net) == pytest.approx(
            _reading(energy, credit) - _reading(energy, debit)
        ), net


async def test_net_reads_this_updates_offsets_on_the_update_that_books_them(hass: HomeAssistant) -> None:
    coordinator, energy = await _built(hass)
    coordinator.async_set_updated_data(BEFORE)

    coordinator.async_set_updated_data(AFTER)

    # Compensated counters continue from where they were, with no drop.
    assert _reading(energy, f"{PV}:circuit_energy_produced") == pytest.approx(1000.0)
    assert _reading(energy, f"{PV}:circuit_energy_consumed") == pytest.approx(40.0)
    assert _reading(energy, "mainMeterEnergyConsumedWh") == pytest.approx(2500.0)
    # On this same update, every Net is its compensated difference.
    assert _reading(energy, f"{PV}:circuit_energy_net") == pytest.approx(
        _reading(energy, f"{PV}:circuit_energy_produced") - _reading(energy, f"{PV}:circuit_energy_consumed")
    )
    assert _reading(energy, f"{LOAD}:circuit_energy_net") == pytest.approx(
        _reading(energy, f"{LOAD}:circuit_energy_consumed") - _reading(energy, f"{LOAD}:circuit_energy_produced")
    )
    assert _reading(energy, "mainMeterNetEnergyWh") == pytest.approx(
        _reading(energy, "mainMeterEnergyConsumedWh") - _reading(energy, "mainMeterEnergyProducedWh")
    )


async def test_feed_through_net_stays_the_raw_difference(hass: HomeAssistant) -> None:
    coordinator, energy = await _built(hass)
    coordinator.async_set_updated_data(BEFORE)

    coordinator.async_set_updated_data(AFTER)

    assert _reading(energy, "feedthroughNetEnergyWh") == pytest.approx(5.0 - 1.0)
    assert energy["feedthroughEnergyProducedWh"].energy_offset == 0.0
    assert energy["feedthroughEnergyConsumedWh"].energy_offset == 0.0


async def test_net_is_the_raw_difference_with_dip_compensation_off(hass: HomeAssistant) -> None:
    """Review Focus 1."""
    coordinator, energy = await _built(hass, compensate=False)
    coordinator.async_set_updated_data(BEFORE)

    coordinator.async_set_updated_data(AFTER)

    assert _reading(energy, f"{PV}:circuit_energy_net") == pytest.approx(50.0 - 4.0)
    assert _reading(energy, f"{LOAD}:circuit_energy_net") == pytest.approx(20.0 - 3.0)
    assert _reading(energy, "mainMeterNetEnergyWh") == pytest.approx(100.0 - 10.0)


# ---------------------------------------------------------------------------
# A dip that is taken back, a restart, and an option changed before a reload
# ---------------------------------------------------------------------------

# Every counter at zero, as a replay that has not yet delivered the readings.
ZERO: Final = _snapshot(pv=(0.0, 0.0), load=(0.0, 0.0), main=(0.0, 0.0), feed=(0.0, 0.0))
# Back above where they were: the drop was an artefact, and every dip retracts.
BACK: Final = _snapshot(pv=(1001.0, 41.0), load=(31.0, 501.0), main=(301.0, 2501.0), feed=(21.0, 81.0))
# Counting up from AFTER, and once more from there.
LATER: Final = _snapshot(pv=(55.0, 5.0), load=(4.0, 22.0), main=(11.0, 110.0), feed=(1.5, 6.0))
LATER_STILL: Final = _snapshot(pv=(60.0, 6.0), load=(5.0, 24.0), main=(12.0, 120.0), feed=(2.0, 7.0))


async def test_net_stays_flat_when_a_booked_dip_is_retracted(hass: HomeAssistant) -> None:
    """Audit P1: the offset goes on and comes off on the updates the siblings book and retract it."""
    coordinator, energy = await _built(hass)
    coordinator.async_set_updated_data(BEFORE)
    before = _nets(energy)

    coordinator.async_set_updated_data(ZERO)

    assert energy[f"{PV}:circuit_energy_produced"].energy_offset == pytest.approx(1000.0)
    assert _nets(energy) == pytest.approx(before)
    _assert_every_net_on_its_siblings(energy)

    coordinator.async_set_updated_data(BACK)

    assert energy[f"{PV}:circuit_energy_produced"].energy_offset == 0.0
    assert energy["mainMeterEnergyConsumedWh"].energy_offset == 0.0
    assert _nets(energy) == pytest.approx(before)
    _assert_every_net_on_its_siblings(energy)


async def test_restored_offsets_reach_nets_first_update_after_a_restart(hass: HomeAssistant) -> None:
    """Audit P2: through the real platform add order, a restart moves no Net."""
    entry = _entry(hass)
    coordinator, energy, platform = await _set_up(hass, entry)
    coordinator.async_set_updated_data(BEFORE)
    coordinator.async_set_updated_data(AFTER)
    before_restart = _nets(energy)
    assert energy["mainMeterEnergyConsumedWh"].energy_offset == pytest.approx(2400.0)
    entity_ids = _entity_ids(hass, entry)
    assert len(entity_ids) >= len(energy)

    # Removing the entities hands their state and dip records to the restore
    # cache, which the next set of entities reads as they are added.
    await platform.async_reset()
    coordinator, energy, _ = await _set_up(hass, entry)
    coordinator.async_set_updated_data(AFTER)

    assert _entity_ids(hass, entry) == entity_ids
    assert energy["mainMeterEnergyConsumedWh"].energy_offset == pytest.approx(2400.0)
    assert _nets(energy) == pytest.approx(before_restart)
    _assert_every_net_on_its_siblings(energy)


async def test_compensation_turned_off_before_a_reload_leaves_every_net_on_its_siblings(
    hass: HomeAssistant,
) -> None:
    """Audit R2: the counters stop compensating at once, so their Nets must stop too.

    While Home Assistant is starting, an options change waits for the reload
    that applies it, and the sensors re-read the option on every update.
    """
    coordinator, energy = await _built(hass)
    coordinator.async_set_updated_data(BEFORE)
    coordinator.async_set_updated_data(AFTER)
    assert energy["mainMeterEnergyConsumedWh"].energy_offset == pytest.approx(2400.0)

    hass.config_entries.async_update_entry(
        coordinator.config_entry, options={ENABLE_ENERGY_DIP_COMPENSATION: False}
    )
    coordinator.async_set_updated_data(LATER)

    assert _reading(energy, "mainMeterEnergyConsumedWh") == pytest.approx(110.0)
    assert _reading(energy, "mainMeterNetEnergyWh") == pytest.approx(110.0 - 11.0)
    assert _reading(energy, f"{PV}:circuit_energy_net") == pytest.approx(55.0 - 5.0)
    _assert_every_net_on_its_siblings(energy)


async def test_compensation_turned_back_on_before_a_reload_returns_to_every_net(
    hass: HomeAssistant,
) -> None:
    """The held offsets come back to the counters and their Nets on the same update."""
    coordinator, energy = await _built(hass)
    coordinator.async_set_updated_data(BEFORE)
    coordinator.async_set_updated_data(AFTER)
    hass.config_entries.async_update_entry(
        coordinator.config_entry, options={ENABLE_ENERGY_DIP_COMPENSATION: False}
    )
    coordinator.async_set_updated_data(LATER)

    hass.config_entries.async_update_entry(
        coordinator.config_entry, options={ENABLE_ENERGY_DIP_COMPENSATION: True}
    )
    coordinator.async_set_updated_data(LATER_STILL)

    assert _reading(energy, "mainMeterEnergyConsumedWh") == pytest.approx(120.0 + 2400.0)
    assert _reading(energy, f"{PV}:circuit_energy_produced") == pytest.approx(60.0 + 950.0)
    _assert_every_net_on_its_siblings(energy)
