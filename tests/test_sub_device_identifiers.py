"""Each sub-device card's identifier has one builder, which its card, an extension on it and the classifier agree on.

The battery's `{panel serial}_bess`, the microgrid interconnect's
`{panel serial}_mid` and each charger's `{panel serial}_evse_{node id}` are
built in `util` alone. The card's `DeviceInfo` and
`extension.extension_device_identifier` both ask there, and
`classify_sub_device_identifier` reads the kind back by the token each
carries. The identifiers are pinned to what every installed card carries;
the expected records hold every capture's devices to them.
"""

from __future__ import annotations

from typing import Final

from span_panel_api import ExtensionSubject

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.extension import extension_device_identifier
from custom_components.span_panel.util import (
    SUB_DEVICE_BESS,
    SUB_DEVICE_EVSE,
    SUB_DEVICE_MID,
    bess_device_identifier,
    bess_device_info,
    classify_sub_device_identifier,
    evse_device_identifier,
    evse_device_info,
    mid_device_identifier,
    mid_device_info,
)

from .captures_replay import capture, snapshot

CAPTURE: Final = "r202639-d"
"""A capture with a battery, a microgrid interconnect and a charger, whose snapshots the cards are built from."""

PANEL_DEVICE: Final = "panel-device"


def test_the_battery_card_has_one_builder() -> None:
    replayed = snapshot(capture(CAPTURE).tree())
    panel = replayed.serial_number
    identifier = bess_device_identifier(panel)
    assert identifier == f"{panel}_bess"
    info = bess_device_info(panel, replayed.battery, "Span Panel", panel_device_id=PANEL_DEVICE)
    assert info.get("identifiers") == {(DOMAIN, identifier)}
    assert extension_device_identifier(panel, ExtensionSubject(kind="battery")) == identifier
    assert classify_sub_device_identifier(identifier) == SUB_DEVICE_BESS


def test_the_microgrid_interconnect_card_has_one_builder() -> None:
    replayed = snapshot(capture(CAPTURE).tree())
    panel = replayed.serial_number
    assert replayed.mid is not None
    identifier = mid_device_identifier(panel)
    assert identifier == f"{panel}_mid"
    info = mid_device_info(panel, replayed.mid, "Span Panel", panel_device_id=PANEL_DEVICE)
    assert info.get("identifiers") == {(DOMAIN, identifier)}
    assert extension_device_identifier(panel, ExtensionSubject(kind="mid")) == identifier
    assert classify_sub_device_identifier(identifier) == SUB_DEVICE_MID


def test_a_chargers_card_has_one_builder() -> None:
    replayed = snapshot(capture(CAPTURE).tree())
    panel = replayed.serial_number
    (charger,) = replayed.evse.values()
    identifier = evse_device_identifier(panel, charger.node_id)
    assert identifier == f"{panel}_evse_{charger.node_id}"
    info = evse_device_info(panel, charger, "Span Panel", panel_device_id=PANEL_DEVICE)
    assert info.get("identifiers") == {(DOMAIN, identifier)}
    subject = ExtensionSubject(kind="evse", instance_key=charger.node_id)
    assert extension_device_identifier(panel, subject) == identifier
    assert classify_sub_device_identifier(identifier) == SUB_DEVICE_EVSE
