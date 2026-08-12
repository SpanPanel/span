"""Sub-devices hang off the panel by registry id, not by identifiers.

Home Assistant deprecated `via_device=(DOMAIN, serial)` and the unscoped
`async_get_device(identifiers=...)` together in 2026.8, for one reason: device
identifiers are unique only *within* a config entry, so anything treating them as
globally unique is ambiguous by construction. Both stop working in 2027.8.

The replacement is a registry id, which only exists once the panel device does.
That is the whole design constraint here — a sub-device cannot name its parent
until its parent is registered — and it is why setup resolves the id once and
carries it on the entry's runtime data instead of every platform looking it up.

The end-to-end test below is the one that matters. `via_device_id` naming a
device that does not exist is dropped by the registry rather than rejected, so a
wrong id threaded through these builders would not raise anywhere: the device
would simply appear unparented in the UI, which is exactly the kind of failure
a unit test asserting on a dict cannot see.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.span_panel import ensure_device_registered
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.util import (
    bess_device_info,
    evse_device_info,
    mid_device_info,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from span_panel_api import SpanMidSnapshot

from .factories import (
    SpanBatterySnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry

_PANEL_ID = "a-registry-id"


def _mid() -> SpanMidSnapshot:
    return SpanMidSnapshot(
        node_id="sp3-link-001-mid",
        serial_number="sp3-link-001-mid",
        vendor_name="Span",
        model=None,
        islanding_state="ON_GRID",
        grid_state="UP",
        grid_forming_entity="GRID",
    )


def _builders() -> list[tuple[str, Any]]:
    """Every sub-device builder, so a new one is covered the day it lands."""
    return [
        (
            "bess",
            lambda: bess_device_info(
                "sp3-link-001",
                SpanBatterySnapshotFactory.create(),
                "Panel",
                panel_device_id=_PANEL_ID,
            ),
        ),
        (
            "mid",
            lambda: mid_device_info(
                "sp3-link-001", _mid(), "Panel", panel_device_id=_PANEL_ID
            ),
        ),
        (
            "evse",
            lambda: evse_device_info(
                "sp3-link-001",
                SpanEvseSnapshotFactory.create(),
                "Panel",
                panel_device_id=_PANEL_ID,
            ),
        ),
    ]


@pytest.mark.parametrize(("label", "build"), _builders(), ids=lambda v: v if isinstance(v, str) else "")
def test_sub_devices_link_by_registry_id(label: str, build: Any) -> None:
    """Both halves asserted: the new key is set and the old one is gone.

    Checking only that `via_device_id` is present would pass a builder that
    passes both, which Home Assistant accepts today and stops accepting in
    2027.8 -- a regression that would sit unnoticed until the deadline.
    """
    info = build()

    assert info.get("via_device_id") == _PANEL_ID, label
    assert "via_device" not in info, f"{label} still links by identifiers"


async def test_registering_the_panel_answers_with_the_id_sub_devices_need(
    hass: HomeAssistant,
) -> None:
    """The returned id is the created device's, on the branch that creates it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.30"},
        entry_id="entry-link-new",
        unique_id="sp3-link-new",
    )
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-link-new")

    panel_device_id = await ensure_device_registered(hass, entry, snapshot, "SPAN Panel")

    created = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "sp3-link-new"), entry.entry_id
    )
    assert created is not None
    assert panel_device_id == created.id


async def test_an_already_registered_panel_answers_with_the_same_id(
    hass: HomeAssistant,
) -> None:
    """And on the branch that finds one, which is every reload after the first.

    A second registration must not mint a new id: sub-devices registered against
    the old one would be orphaned, and a user would watch their battery and
    chargers detach from the panel on a restart.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.31"},
        entry_id="entry-link-existing",
        unique_id="sp3-link-existing",
    )
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-link-existing")

    first = await ensure_device_registered(hass, entry, snapshot, "SPAN Panel")
    second = await ensure_device_registered(hass, entry, snapshot, "SPAN Panel")

    assert first == second


async def test_a_foreign_device_sharing_the_identifier_is_not_adopted(
    hass: HomeAssistant,
) -> None:
    """The ambiguity the deprecation exists to remove, made concrete.

    Identifiers are unique within a config entry and nowhere else, so a lookup
    that searches every entry can answer with a device this entry does not own --
    and then every sub-device built from that answer hangs off somebody else's
    panel. Scoping the lookup is what makes the answer unambiguous.

    A second SPAN entry cannot reach this state today, because the serial is the
    entry's unique_id. That is a property of our config flow rather than of the
    registry, though, and it is not the assumption the sub-device links should
    rest on.
    """
    other = MockConfigEntry(
        domain=DOMAIN, data={}, entry_id="entry-other", unique_id="other"
    )
    other.add_to_hass(hass)
    registry = dr.async_get(hass)
    foreign = registry.async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={(DOMAIN, "sp3-link-shared")},
        name="Somebody else's panel",
    )

    mine = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.33"},
        entry_id="entry-mine",
        unique_id="sp3-link-shared",
    )
    mine.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-link-shared")

    panel_device_id = await ensure_device_registered(hass, mine, snapshot, "SPAN Panel")

    assert panel_device_id != foreign.id
    assert registry.async_get(panel_device_id) is not None


async def test_a_sub_device_really_lands_under_the_panel(hass: HomeAssistant) -> None:
    """End to end through the registry, because a bad id fails silently.

    The registry drops a `via_device_id` it cannot resolve instead of raising, so
    the only way to know the id threaded from setup through runtime data into a
    builder is the right one is to register a device with it and read the link
    back.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.32"},
        entry_id="entry-link-e2e",
        unique_id="sp3-link-e2e",
    )
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-link-e2e")
    registry = dr.async_get(hass)

    panel_device_id = await ensure_device_registered(hass, entry, snapshot, "SPAN Panel")
    bess = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **bess_device_info(
            "sp3-link-e2e",
            SpanBatterySnapshotFactory.create(),
            "SPAN Panel",
            panel_device_id=panel_device_id,
        ),
    )

    assert bess.via_device_id == panel_device_id
