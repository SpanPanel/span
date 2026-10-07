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
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot, SpanPVSnapshot

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
from custom_components.span_panel.helpers import identity_digest
from custom_components.span_panel.pv_binding import PvBinding, StoredPvBinding, resolve

from .adapter_fixtures import schema_one_snapshot
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
        "wifi_ssid": "Span WiFi",
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
