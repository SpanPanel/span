"""Tests for Span Panel diagnostics."""

from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import FieldMetadata, MaskedTree, SpanPanelSnapshot, SpanPVSnapshot, TreeExportingAdapter

from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import (
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
    DOMAIN,
)
from custom_components.span_panel.curation import (
    CurationOverlay,
    CurationRecord,
    async_load_curation,
    async_save_record,
)
from custom_components.span_panel.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.span_panel.extension import create_extension_sensors
from custom_components.span_panel.helpers import identity_digest
from custom_components.span_panel.notices import async_restore
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.pv_binding import PvBinding, StoredPvBinding, resolve

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .captures_replay import replay
from .factories import (
    SpanBatterySnapshotFactory,
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
    pv_binding_for,
)


async def test_config_entry_diagnostics_includes_redacted_runtime_data(
    hass: HomeAssistant,
) -> None:
    """Return redacted diagnostics with optional runtime sections populated."""
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number="sp3-diag-001",
        firmware_version="spanos2/r202603/05",
        panel_size=32,
        wifi_ssid="Span WiFi",
        eth0_link=True,
        wlan_link=False,
        circuits={
            "uuid_kitchen": SpanCircuitSnapshotFactory.create(
                circuit_id="uuid_kitchen",
                name="Kitchen",
                relay_state="CLOSED",
                priority="SOC_THRESHOLD",
                instant_power_w=245.5,
                produced_energy_wh=10.0,
                consumed_energy_wh=2500.0,
                device_type="circuit",
                tabs=[5, 6],
            )
        },
        evse={"evse-0": SpanEvseSnapshotFactory.create()},
        battery=SpanBatterySnapshotFactory.create(
            connected=True,
            soe_percentage=84.0,
            soe_kwh=11.2,
        ),
    )
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    # Explicit: a MagicMock answers `len()` and iteration happily, so leaving
    # this unset would let the discovery block render as an empty report rather
    # than as the "no metadata yet" state it actually is.
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None

    # Version 6 deliberately: an entry that has not yet been through the v7
    # migration still carries the passphrase, and redaction must still cover it.
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=6,
        data={
            CONF_ACCESS_TOKEN: "access-secret",
            CONF_EBUS_BROKER_PASSWORD: "mqtt-password",
            CONF_EBUS_BROKER_USERNAME: "mqtt-user",
            CONF_HOP_PASSPHRASE: "hop-secret",
        },
        title="SPAN Panel",
        unique_id="sp3-diag-001",
    )
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["config_entry"]["data"][CONF_ACCESS_TOKEN] == REDACTED
    assert result["config_entry"]["data"][CONF_EBUS_BROKER_PASSWORD] == REDACTED
    assert result["config_entry"]["data"][CONF_EBUS_BROKER_USERNAME] == REDACTED
    assert result["config_entry"]["data"][CONF_HOP_PASSPHRASE] == REDACTED

    assert result["panel"] == {
        "serial_number": "sp3-diag-001",
        "firmware_version": "spanos2/r202603/05",
        "panel_size": 32,
        "lugs_at_service_entrance": True,
        "instant_grid_power_w": 2500.75,
        "power_flow_grid": None,
        "wifi_ssid": REDACTED,
        "eth0_link": True,
        "wlan_link": False,
    }
    assert result["circuits"]["uuid_kitchen"] == {
        "name": "Kitchen",
        "relay_state": "CLOSED",
        "relay_state_target": None,
        "priority": "SOC_THRESHOLD",
        "priority_target": None,
        "is_user_controllable": True,
        "instant_power_w": 245.5,
        "produced_energy_wh": 10.0,
        "consumed_energy_wh": 2500.0,
        "device_type": "circuit",
        "tabs": [5, 6],
    }
    assert result["evse"]["evse-0"] == {
        "node_id": "evse-0",
        "feed_circuit_id": "evse_circuit_1",
        "status": "CHARGING",
        "lock_state": "LOCKED",
        "advertised_current_a": 32.0,
    }
    assert result["battery"] == {
        "connected": True,
        "soe_percentage": 84.0,
        "soe_kwh": 11.2,
    }
    assert result["coordinator"] == {
        "panel_offline": False,
        "last_update_success": True,
    }


async def test_config_entry_diagnostics_omits_optional_sections_when_unavailable(
    hass: HomeAssistant,
) -> None:
    """Return empty optional sections when the snapshot lacks them."""
    snapshot = SimpleNamespace(
        serial_number="sp3-diag-002",
        firmware_version="spanos2/r202603/06",
        panel_size=None,
        wifi_ssid=None,
        eth0_link=None,
        wlan_link=None,
        circuits={
            # The real model, not a namespace: every field the diagnostics dump
            # reads off a circuit is present on `SpanCircuitSnapshot`, and a hand
            # -rolled double that omitted two of them is what kept a pair of
            # unreachable `hasattr` guards alive in the dump.
            "uuid_minimal": SpanCircuitSnapshotFactory.create(
                circuit_id="uuid_minimal",
                name="",
                relay_state="OPEN",
                priority="NEVER",
                is_user_controllable=False,
                instant_power_w=0.0,
                produced_energy_wh=0.0,
                consumed_energy_wh=0.0,
                tabs=[],
            )
        },
        evse={},
        pv_inverters={},
        publishes_solar_roles=False,
        power_flow_pv=None,
        battery=None,
        adopted_devices=(),
        lugs_at_service_entrance=True,
        instant_grid_power_w=0.0,
        power_flow_grid=None,
    )
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = True
    coordinator.last_update_success = False
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None

    entry = MockConfigEntry(domain=DOMAIN, data={}, title="SPAN Panel")
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["panel"] == {
        "serial_number": "sp3-diag-002",
        "firmware_version": "spanos2/r202603/06",
        "panel_size": None,
        "lugs_at_service_entrance": True,
        "instant_grid_power_w": 0.0,
        "power_flow_grid": None,
    }
    assert result["circuits"]["uuid_minimal"] == {
        "name": "",
        "relay_state": "OPEN",
        "relay_state_target": None,
        "priority": "NEVER",
        "priority_target": None,
        "is_user_controllable": False,
        "instant_power_w": 0.0,
        "produced_energy_wh": 0.0,
        "consumed_energy_wh": 0.0,
        "device_type": "circuit",
        "tabs": [],
    }
    assert result["evse"] == {}
    assert result["battery"] == {}
    assert result["coordinator"] == {
        "panel_offline": True,
        "last_update_success": False,
    }


async def test_diagnostics_reports_the_entity_registry(hass: HomeAssistant) -> None:
    """The registry is where an upgrade complaint is settled, and the UI hides it.

    Home Assistant says "This entity is disabled" without saying by what, and a
    user without shell access to `.storage` cannot read `disabled_by` at all. Four
    causes look identical on screen and need four different fixes, so the field
    that distinguishes them has to leave the machine somehow.

    `unique_id` rides along because it answers the question underneath: an entity
    whose id changed is a new entity however familiar its name, and that is the
    difference between an upgrade defect and a surprise.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="SPAN Panel")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "span_sp3_diag_003_l1_voltage",
        config_entry=entry,
        suggested_object_id="span_panel_l1_voltage",
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-diag-003")
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None
    # The real runtime data, not a namespace, for the reason the circuit double
    # above is the real model: a hand-rolled stand-in carrying only the fields
    # the dump happened to read when it was written goes stale silently, and
    # mypy cannot see the drift on a `SimpleNamespace`.
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    rows = {row["entity_id"]: row for row in result["entities"]}
    assert "sensor.span_panel_l1_voltage" in rows
    row = rows["sensor.span_panel_l1_voltage"]
    assert row["disabled_by"] == "integration"
    assert row["unique_id"] == "span_sp3_diag_003_l1_voltage"


async def test_diagnostics_reports_the_stored_curation(hass: HomeAssistant) -> None:
    """What the user asserted about adopted rows is the other half of the adoption block.

    The adoption block says what the panel published. Without this one, a
    maintainer reading an attachment cannot tell a state class the integration
    derived from one the household declared, and those two fail differently.

    Seeded through the store rather than by handing an overlay in, because what
    the payload has to carry is what a *loaded* entry holds: the overlay
    diagnostics reads is the one setup read off disk.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="curated-entry", title="SPAN Panel")
    entry.add_to_hass(hass)
    await async_save_record(
        hass,
        entry,
        "bess/battery-2/cell-voltage",
        CurationRecord(state_class=SensorStateClass.MEASUREMENT, device_class="voltage"),
    )
    await async_save_record(hass, entry, "bess/battery-2/enabled", CurationRecord(promote=True))

    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-diag-004")
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=await async_load_curation(hass, entry),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["adopted_curation"] == {
        "bess/battery-2/cell-voltage": {"state_class": "measurement", "device_class": "voltage"},
        "bess/battery-2/enabled": {"entity_category": "none"},
    }
    # Keys and enum values, and the assertion is on the shape rather than on a
    # filter this test applies: a record that grew a name, an icon or a wire
    # value would be a leak the payload cannot redact, because `TO_REDACT` is
    # key-based over the config entry and reaches nothing here.
    assert all(
        set(record) <= {"state_class", "device_class", "entity_category"}
        for record in result["adopted_curation"].values()
    )


async def test_diagnostics_reports_which_inverter_the_solar_card_reads(hass: HomeAssistant) -> None:
    """The first facts a span#269-style report needs: a circuit-fed inverter, shown by its circuit."""
    snapshot = schema_one_snapshot()
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="pv-diag-entry", title="SPAN Panel")
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None
    identity = pv_binding_for(snapshot)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=identity,
        setup_snapshot=snapshot,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    (key,) = snapshot.pv_inverters
    inverter = snapshot.pv_inverters[key]
    assert result["pv"] == {
        "mode": "inverter",
        "bound_circuit_id": key,
        "legacy_key": key,
        "solar_card_reads": key,
        "solar_link": True,
        "withheld": [],
        "inverters": {
            key: {
                "feed_circuit_id": inverter.feed_circuit_id,
                "relative_position": inverter.relative_position,
                "connected": inverter.connected,
                "own_card": False,
                "own_link": False,
            }
        },
    }


async def test_diagnostics_says_an_unbound_card_reads_several_inverters_together(hass: HomeAssistant) -> None:
    snapshot = replace(
        SpanPanelSnapshotFactory.create(serial_number="sp3-diag-pv2"),
        pv_inverters={
            "c-1": SpanPVSnapshot(device_id="pv-1", node_id="c-1", feed_circuit_id="c-1"),
            "c-2": SpanPVSnapshot(device_id="pv-2", node_id="c-2", feed_circuit_id="c-2"),
        },
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="pv-diag-two", title="SPAN Panel")
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["pv"]["mode"] == "unbound"
    assert result["pv"]["solar_card_reads"] == "together"
    assert {row["own_card"] for row in result["pv"]["inverters"].values()} == {True}
    # Neither inverter's circuit publishes a link record, so neither card, nor the Solar card, has a link.
    assert {row["own_link"] for row in result["pv"]["inverters"].values()} == {False}
    assert result["pv"]["solar_link"] is False


async def _pv_block(hass: HomeAssistant, entry_id: str, snapshot: SpanPanelSnapshot, binding: PvBinding) -> dict[str, object]:
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id=entry_id, title="SPAN Panel")
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=binding,
        setup_snapshot=snapshot,
    )
    result = await async_get_config_entry_diagnostics(hass, entry)
    block: dict[str, object] = result["pv"]
    return block


SERIAL_PV = "sp3-diag-serial-001"
DEVICE_ID_KEY = f"{SERIAL_PV}-pv"
"""An inverter no circuit feeds is keyed by its device id, which embeds the panel serial."""


async def test_an_unfed_inverters_device_id_key_is_digested_where_the_card_reads_it(hass: HomeAssistant) -> None:
    """Unbound, one inverter no circuit feeds: its key is shown digested, as the Solar card's legacy key too."""
    snapshot = replace(
        SpanPanelSnapshotFactory.create(serial_number=SERIAL_PV),
        pv_inverters={DEVICE_ID_KEY: SpanPVSnapshot(device_id=DEVICE_ID_KEY, node_id=DEVICE_ID_KEY)},
    )
    binding = resolve(snapshot, None, frozenset(), link_held=False, inverter_links_held=frozenset())[0]
    assert binding.legacy_key == DEVICE_ID_KEY

    block = await _pv_block(hass, "pv-diag-unfed", snapshot, binding)

    digest = identity_digest(DEVICE_ID_KEY)
    assert block["mode"] == "unbound"
    assert block["legacy_key"] == digest
    assert block["solar_card_reads"] == digest
    assert block["inverters"] == {
        digest: {
            "feed_circuit_id": None,
            "relative_position": None,
            "connected": None,
            "own_card": False,
            "own_link": False,
        }
    }
    assert SERIAL_PV not in json.dumps(block)


async def test_a_withheld_inverters_device_id_key_is_digested(hass: HomeAssistant) -> None:
    """Pending: the bound circuit is published, its inverter is not, and a device-id inverter is withheld."""
    snapshot = replace(
        SpanPanelSnapshotFactory.create(
            serial_number=SERIAL_PV, circuits={"c-bound": SpanCircuitSnapshotFactory.create(circuit_id="c-bound")}
        ),
        pv_inverters={DEVICE_ID_KEY: SpanPVSnapshot(device_id=DEVICE_ID_KEY, node_id=DEVICE_ID_KEY)},
    )
    binding = resolve(snapshot, StoredPvBinding(circuit_id="c-bound"), frozenset(), link_held=False, inverter_links_held=frozenset())[0]
    assert binding.withheld == frozenset({DEVICE_ID_KEY})

    block = await _pv_block(hass, "pv-diag-withheld", snapshot, binding)

    digest = identity_digest(DEVICE_ID_KEY)
    assert block["mode"] == "inverter"
    assert block["bound_circuit_id"] == "c-bound"
    assert block["legacy_key"] == "c-bound"
    assert block["withheld"] == [digest]
    assert block["inverters"] == {
        digest: {
            "feed_circuit_id": None,
            "relative_position": None,
            "connected": None,
            "own_card": False,
            "own_link": False,
        }
    }
    assert SERIAL_PV not in json.dumps(block)


# -- site data never leaves the house in a dump ------------------------------


def _export(tree: dict[str, dict[str, str]]) -> MaskedTree:
    """The masked export the library produces for `tree`, replayed as the broker delivers it."""
    adapter = replay(tree)
    assert isinstance(adapter, TreeExportingAdapter)
    export = adapter.export_masked_tree()
    assert export is not None
    return export


def _reference_entry(
    snapshot: SpanPanelSnapshot,
    tree: dict[str, dict[str, str]] | None = None,
    *,
    schema_major: str = "schema_1",
    panel_offline: bool = False,
) -> MockConfigEntry:
    """Return an entry whose coordinator holds `snapshot`, as diagnostics reads it.

    Given `tree`, the client exports the library's real masked tree for it, so a
    test of what reaches the dump is a test of what the library masks.
    """
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.panel_offline = panel_offline
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    coordinator.client.schema_major = schema_major
    coordinator.client.masked_tree.return_value = None if tree is None else _export(tree)
    entry = MockConfigEntry(domain=DOMAIN, title="SPAN Panel", unique_id=snapshot.serial_number)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    return entry


async def test_diagnostics_redact_the_wifi_network_name(hass: HomeAssistant) -> None:
    """The network name says where the panel is, and a dump is posted where anyone reads it.

    That the panel reports one is kept, because it says the panel is on Wi-Fi;
    what it is, is not.
    """
    snapshot = schema_one_snapshot()
    assert snapshot.wifi_ssid

    result = await async_get_config_entry_diagnostics(hass, _reference_entry(snapshot, schema_one_tree()))

    assert result["panel"]["wifi_ssid"] == REDACTED
    assert snapshot.wifi_ssid not in json.dumps(result, default=str)


async def test_diagnostics_hold_no_postal_code(hass: HomeAssistant) -> None:
    """The postal code the panel publishes reaches no dump, and its entity is not taken away.

    The entity is disabled by default and stays where an install already holds
    it: removing it would remove one a user may have enabled. A new install
    creates none (`test_privacy.py`).
    """
    snapshot = schema_one_snapshot()
    (postal_code,) = (
        row for row in snapshot.extension_properties if row.path == "status/postal-code"
    )
    assert postal_code.value

    entry = _reference_entry(snapshot, schema_one_tree())
    result = await async_get_config_entry_diagnostics(hass, entry)

    assert postal_code.value not in json.dumps(result, default=str)

    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, snapshot.serial_number)},
        name="Span Panel",
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"span_{snapshot.serial_number}_adopted_panel/{postal_code.path}",
        config_entry=entry,
    )
    sensors = create_extension_sensors(
        entry.runtime_data.coordinator,
        snapshot,
        dr.async_get(hass),
        er.async_get(hass),
        config_entry_id=entry.entry_id,
        overlay=CurationOverlay.empty(),
        pv_binding=entry.runtime_data.pv_binding,
    )
    (postal_code_sensor,) = (
        sensor
        for sensor in sensors
        if (sensor.extra_state_attributes or {}).get("wire_path") == postal_code.path
    )
    assert postal_code_sensor.entity_registry_enabled_default is False


# -- the masked device tree --------------------------------------------------


async def test_diagnostics_carry_the_masked_device_tree(hass: HomeAssistant) -> None:
    """The report carries the library's masked export as it is, so a case needs no separate capture."""
    snapshot = schema_one_snapshot()
    export = _export(schema_one_tree())

    result = await async_get_config_entry_diagnostics(hass, _reference_entry(snapshot, schema_one_tree()))

    assert result["device_tree"] == export
    assert "device_tree_unavailable" not in result


async def test_diagnostics_say_a_flat_panel_has_no_tree(hass: HomeAssistant) -> None:
    """A flat data model has no device tree to export, and the report says that is why."""
    entry = _reference_entry(schema_one_snapshot(), schema_major="schema_0")

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["device_tree"] is None
    assert result["device_tree_unavailable"] == "flat_data_model"


async def test_diagnostics_say_an_undescribed_panel_has_no_tree(hass: HomeAssistant) -> None:
    """A parent/child panel that has not described its root yet has nothing to export."""
    result = await async_get_config_entry_diagnostics(hass, _reference_entry(schema_one_snapshot()))

    assert result["device_tree"] is None
    assert result["device_tree_unavailable"] == "not_described"


async def test_an_offline_panel_still_reports_its_last_tree(hass: HomeAssistant) -> None:
    """A panel that dropped off is the case a report is written about, so its last tree stays."""
    export = _export(schema_one_tree())
    entry = _reference_entry(schema_one_snapshot(), schema_one_tree(), panel_offline=True)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"]["panel_offline"] is True
    assert result["device_tree"] == export


def _rated_panel(dryer_a: float | None, oven_a: float) -> SpanPanelSnapshot:
    return SpanPanelSnapshotFactory.create(
        serial_number="sp3-diag-rating",
        circuits={
            circuit_id: SpanCircuitSnapshotFactory.create(
                circuit_id=circuit_id,
                name=name,
                tabs=spaces,
                breaker_rating_a=rating,
                breaker_rating_declared=True,
            )
            for circuit_id, name, spaces, rating in (
                ("uuid_kitchen", "Kitchen", [3], 20.0),
                ("uuid_dryer", "Dryer", [5], dryer_a),
                ("uuid_oven", "Oven", [7], oven_a),
                ("uuid_heater", "Heater", [11, 9], 30.0),
            )
        },
        main_breaker_rating_a=100,
    )


async def test_diagnostics_report_the_stored_package_ratings_and_the_removals_not_yet_seen(
    hass: HomeAssistant,
) -> None:
    """Keyed by circuit id as the circuits block is: each copy, the spaces it was copied for, and whether it applies."""
    entry = MockConfigEntry(domain=DOMAIN, title="SPAN Panel", unique_id="sp3-diag-rating")
    entry.add_to_hass(hass)
    await async_restore(hass, entry)  # as setup does, so the notice the removal raises stands
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    before = _rated_panel(dryer_a=30.0, oven_a=40.0)
    manager.begin(before, {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")})
    manager.set_package_ratings("uuid_kitchen", before, [20, 20])
    manager.set_package_ratings("uuid_dryer", before, [30, 15])
    manager.set_package_ratings("uuid_oven", before, [40, 20])
    manager.set_package_ratings("uuid_heater", before, [30, 20])
    # The dryer's rating is declared and has not arrived again; the oven's panel rating moved, removing its copy.
    # The heater's copy, across two spaces, is kept on averaged legs.
    now = _rated_panel(dryer_a=None, oven_a=45.0)
    manager.reconcile(now, lambda: None)
    await hass.async_block_till_done()

    coordinator = MagicMock()
    coordinator.data = now
    coordinator.panel_offline = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    coordinator.client.schema_major = "schema_1"
    coordinator.client.masked_tree.return_value = None
    coordinator.package_rating_manager = manager
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(now),
        setup_snapshot=now,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["package_ratings"] == {
        "panel_describes_breakers": False,
        "service_rating_declaration": "declared",
        "packages": {
            "uuid_kitchen": {"label": "Kitchen", "package_ratings_a": [20, 20], "spaces": [3], "state": "applies"},
            "uuid_dryer": {"label": "Dryer", "package_ratings_a": [30, 15], "spaces": [5], "state": "pending"},
            "uuid_heater": {
                "label": "Heater",
                "package_ratings_a": [30, 20],
                "spaces": [9, 11],
                "state": "averaged_legs",
            },
        },
        "unacknowledged": {
            "uuid_oven": {
                "circuit_id": "uuid_oven",
                "label": "Oven",
                "reason": "panel_rating_changed",
                "package_ratings_a": [40, 20],
                "panel_after_a": 45.0,
                "service_a": 100.0,
            }
        },
    }
    assert set(result["package_ratings"]["packages"]) <= set(result["circuits"])
    assert json.dumps(result["package_ratings"])
