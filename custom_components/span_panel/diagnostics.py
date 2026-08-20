"""Diagnostics support for the Span Panel integration."""

from __future__ import annotations

from typing import Any, TypedDict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from span_panel_api import SpanPanelSnapshot

from . import SpanPanelConfigEntry
from .adoption import adopted_control_count, classify
from .const import (
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
)
from .schema_validation import SchemaFindings

TO_REDACT = {
    CONF_ACCESS_TOKEN,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
    "password",
    "username",
}
"""Config-entry keys whose values are replaced before the payload leaves.

Key-based, and only over `entry.as_dict()`. It knows nothing about wire
property names and cannot be taught them cheaply, so anything added to this
payload from the panel has to be safe by construction rather than by redaction.
That is the constraint `_discovery` is built to — see its docstring.
"""


class DiscoveredRow(TypedDict):
    """One declared-but-unread property, as it appears in the payload."""

    path: str
    datatype: str
    unit: str | None
    retained: bool | None


class DiscoveryBlock(TypedDict):
    """The `schema_discovery` section. Typed so the shape is checked, not described.

    Four keys per row and no more: a fifth would be the seam a wire value slips
    through, and this is what makes adding one a type error rather than a review
    comment.
    """

    available: bool
    count: int
    properties: list[DiscoveredRow]


def _discovery(findings: SchemaFindings | None) -> DiscoveryBlock:
    """Report what the panel declares that this integration reads nothing from.

    Maintainer-facing, and the reason it is here rather than anywhere a user
    looks: a diagnostics attachment on an issue is where the question "what does
    that fleet publish that we ignore" actually gets asked, and aggregating a
    few attachments is how the rate gets measured. Nothing is created from it —
    no entity, no Repair, no notification.

    **Declarations, never values.** Each row is the property's path, its declared
    datatype and unit, and whether the panel has published a value for it. A
    diagnostics payload leaves the house into issues and forum posts, and
    `TO_REDACT` above is key-based over the config entry — it could not protect a
    wire value put here, so no wire value is put here. `test_diagnostics` asserts
    that against the vendored capture rather than leaving it to review.

    `available` is False while the adapter has not reported metadata yet, which
    is a real state on a reconnect. It is not the same as an empty report: an
    empty `properties` list means the panel declares nothing this integration
    ignores, which is a finding.
    """
    if findings is None:
        return {"available": False, "count": 0, "properties": []}
    return {
        "available": True,
        "count": len(findings.discovered),
        "properties": [
            {
                "path": entry.path,
                "datatype": entry.datatype,
                "unit": entry.unit,
                "retained": entry.retained,
            }
            for entry in findings.discovered
        ],
    }


class AdoptedDeviceRow(TypedDict):
    """One adopted device, as it appears in the payload.

    No `name`, no `serial_number`. Both are on `AdoptedDevice` and both reach the
    device card, but neither answers the question this block exists to ask -- and
    a vendor-set device name is free text a household chose. The type, the shape
    and the counts are what a maintainer needs.
    """

    device_type: str
    model: str | None
    property_count: int
    properties: list[AdoptedPropertyRow]


class AdoptedPropertyRow(TypedDict):
    """One adopted property's declaration, with no value.

    The same rule `DiscoveredRow` follows and for the same reason: `TO_REDACT` is
    key-based over the config entry and cannot protect a wire value put here.
    `platform` is included because it is derived from the other three and is the
    decision a maintainer would otherwise recompute by hand.
    """

    path: str
    datatype: str
    unit: str | None
    settable: bool
    platform: str


class AdoptionBlock(TypedDict):
    """The `adopted_devices` section.

    `controls` counts the adopted properties that write back to the panel rather
    than only reporting. It is the highest-consequence thing adoption creates, so
    it is worth a number in the one artefact that reaches a maintainer.
    """

    count: int
    controls: int
    devices: list[AdoptedDeviceRow]


def _adoption(snapshot: SpanPanelSnapshot) -> AdoptionBlock:
    """Report the devices this integration models nothing for.

    Declarations, never values -- see `AdoptedPropertyRow`. Empty on every panel
    that publishes only device types this integration reads, which is every panel
    seen so far, and a non-empty block is the first evidence that the schema's
    vendor extensibility is being used in the field.
    """
    return {
        "count": len(snapshot.adopted_devices),
        "controls": adopted_control_count(snapshot),
        "devices": [
            {
                "device_type": device.device_type,
                "model": device.model,
                "property_count": len(device.properties),
                "properties": [
                    {
                        "path": declaration.path,
                        "datatype": declaration.datatype,
                        "unit": declaration.unit,
                        "settable": declaration.settable,
                        "platform": classify(declaration).value,
                    }
                    for declaration in device.properties
                ],
            }
            for device in snapshot.adopted_devices
        ],
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: SpanPanelConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator
    snapshot = coordinator.data

    panel_data: dict[str, Any] = {
        "serial_number": snapshot.serial_number,
        "firmware_version": snapshot.firmware_version,
        "panel_size": snapshot.panel_size,
    }

    if snapshot.wifi_ssid is not None:
        panel_data["wifi_ssid"] = snapshot.wifi_ssid
    if snapshot.eth0_link is not None:
        panel_data["eth0_link"] = snapshot.eth0_link
    if snapshot.wlan_link is not None:
        panel_data["wlan_link"] = snapshot.wlan_link

    circuit_data: dict[str, dict[str, Any]] = {}
    for circuit_id, circuit in snapshot.circuits.items():
        circuit_data[circuit_id] = {
            "name": circuit.name,
            "relay_state": circuit.relay_state,
            "relay_state_target": circuit.relay_state_target,
            "priority": circuit.priority,
            "priority_target": circuit.priority_target,
            "is_user_controllable": circuit.is_user_controllable,
            "instant_power_w": circuit.instant_power_w,
            "produced_energy_wh": circuit.produced_energy_wh,
            "consumed_energy_wh": circuit.consumed_energy_wh,
        }
        if hasattr(circuit, "device_type"):
            circuit_data[circuit_id]["device_type"] = circuit.device_type
        if hasattr(circuit, "tabs"):
            circuit_data[circuit_id]["tabs"] = circuit.tabs

    evse_data: dict[str, dict[str, Any]] = {}
    if snapshot.evse:
        for evse_id, evse in snapshot.evse.items():
            evse_data[evse_id] = {
                "node_id": evse.node_id,
                "feed_circuit_id": evse.feed_circuit_id,
                "status": evse.status,
                "lock_state": evse.lock_state,
                "advertised_current_a": evse.advertised_current_a,
            }

    battery_data: dict[str, Any] = {}
    if snapshot.battery:
        battery_data = {
            "connected": snapshot.battery.connected,
            "soe_percentage": snapshot.battery.soe_percentage,
            "soe_kwh": snapshot.battery.soe_kwh,
        }

    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "panel": panel_data,
        "circuits": circuit_data,
        "evse": evse_data,
        "battery": battery_data,
        "coordinator": {
            "panel_offline": coordinator.panel_offline,
            "last_update_success": coordinator.last_update_success,
        },
        "schema_discovery": _discovery(coordinator.schema_findings),
        "adopted_devices": _adoption(snapshot),
    }
