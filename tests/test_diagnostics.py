"""Tests for Span Panel diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.diagnostics import REDACTED
from custom_components.span_panel import SpanPanelRuntimeData
from custom_components.span_panel.const import (
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
    DOMAIN,
)
from custom_components.span_panel.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .factories import (
    SpanBatterySnapshotFactory,
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry


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
    entry.runtime_data = SpanPanelRuntimeData(coordinator=coordinator, panel_device_id="panel-device-id")

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
            "uuid_minimal": SimpleNamespace(
                name=None,
                relay_state="OPEN",
                relay_state_target=None,
                priority="NEVER",
                priority_target=None,
                is_user_controllable=False,
                instant_power_w=0.0,
                produced_energy_wh=0.0,
                consumed_energy_wh=0.0,
            )
        },
        evse={},
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
    entry.runtime_data = SpanPanelRuntimeData(coordinator=coordinator, panel_device_id="panel-device-id")

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
        "name": None,
        "relay_state": "OPEN",
        "relay_state_target": None,
        "priority": "NEVER",
        "priority_target": None,
        "is_user_controllable": False,
        "instant_power_w": 0.0,
        "produced_energy_wh": 0.0,
        "consumed_energy_wh": 0.0,
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

    coordinator = MagicMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number="sp3-diag-003")
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.last_update_success = True
    coordinator.schema_findings = None
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    result = await async_get_config_entry_diagnostics(hass, entry)

    rows = {row["entity_id"]: row for row in result["entities"]}
    assert "sensor.span_panel_l1_voltage" in rows
    row = rows["sensor.span_panel_l1_voltage"]
    assert row["disabled_by"] == "integration"
    assert row["unique_id"] == "span_sp3_diag_003_l1_voltage"
