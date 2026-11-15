"""The notices circuit devices call for: one for an install's conversion, one per breaker in a room.

## The conversion notice

`circuit_device_conversion` moves the entities and reports what it changed.
This module tells the user: which displayed names changed, which entities
changed area, and which circuits kept their entities' areas as they were.

- **Raised after the platforms are set up,** so each name after the move is the
  one Home Assistant composes for the entity as it now stands, by the same
  function that composed the name before it (`async_get_full_entity_name`).
  Only the names that differ are listed.
- **Persistent.** The conversion moves each entity once, so nothing re-derives
  the notice at the next setup, and a notice that is not persistent comes back
  inactive after a restart.
- **Not fixable.** There is nothing to repair. The user dismisses it once read.
- **Severity `WARNING`.** Everything keeps working, and names the user sees
  have changed.
- **One per config entry,** its id carrying the entry's, so two panels each
  report their own circuits. Cleared when the entry is removed, because Home
  Assistant deletes no issue with its entry.
- **Raised only when there is something to list.** A run that converted
  nothing, as every setup after the first is, leaves an earlier notice exactly
  as it stands.

The lists reach the description as placeholders, already formatted as markdown
lists. A panel can change hundreds of names at once, so each list stops at
`_MAX_LINES` and says how many more there are.

## The breaker switch notice

An entity with no area of its own is in its device's, and an area-wide action,
such as turning off everything in a room, reaches every switch in the area. A
circuit's device can have an area before its breaker switch is on it, so a
setup can put a breaker in a room nobody put it in. Each such switch gets a
notice naming it and the area.

A setup puts a switch on a circuit's device in two ways, and both are covered,
so no breaker joins an area unannounced:

- the switch platform adds one that had no registry row: the panel started
  offering control on the circuit, or controls were switched on for the first
  time. A row Home Assistant restores from its deleted entities is such a
  switch too, and comes back with whatever area it had;
- the conversion moves one an older release left on the panel's device, at a
  setup later than its circuit's other entities, which the conversion's report
  lists where the switch had no area and the device had one by then.

A switch already on its circuit's device is in whatever area its user gives the
device afterwards. That is the user's own doing and raises nothing. Nor does a
switch on a device with no area of its own: such a device is in the panel's
area, where every breaker switch was while it sat on the panel's device, so the
switch is where a new one has always been.

- **Decided after the platforms are set up,** from the registry as they left
  it. A platform's call to add its entities only schedules the add, which Home
  Assistant finishes after the platform's own setup has returned, so the
  platform cannot see the row it asked for. The registry is read once before
  the platforms are forwarded (`async_registered_breaker_switches`) and again
  after, and a switch in the second reading alone is new. Read from the
  registry, a switch registered disabled counts: enabling it is all it takes
  for the area to reach it.
- **Persistent, not fixable, severity `WARNING`,** for the reasons the
  conversion notice is. What to do about a breaker in a room is the user's
  choice: give the switch an area of its own, clear the device's, or leave it.
- **One per switch,** its id carrying the entry's and the switch's unique id.
  Cleared with the conversion notice when the entry is removed.
- **Raised afresh each time.** A switch the panel withdraws and offers again
  joins the area again, and a notice raised over one the user dismissed would
  stay dismissed, so the earlier one is deleted first.
"""

from __future__ import annotations

from collections.abc import Container, Iterable
from typing import Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from span_panel_api import SpanPanelSnapshot

from .circuit_device_conversion import ConversionReport
from .const import DOMAIN
from .entity_resolver import entity_id_in_entry
from .id_builder import build_switch_unique_id
from .runtime import SpanPanelConfigEntry, loaded_runtime_data

CONVERSION_NOTICE_ISSUE_PREFIX: Final = "circuit_devices_converted_"

BREAKER_SWITCH_AREA_ISSUE_PREFIX: Final = "breaker_switch_in_area_"

_TRANSLATION_KEY: Final = "circuit_devices_converted"

_BREAKER_SWITCH_AREA_TRANSLATION_KEY: Final = "breaker_switch_in_area"

_MAX_LINES: Final = 50
"""How many lines one list shows before it counts the rest."""

_NOTHING_LISTED: Final = "—"
"""What stands in for a list with nothing in it."""


def conversion_notice_issue_id(entry_id: str) -> str:
    """Issue id for one entry's conversion notice."""
    return f"{CONVERSION_NOTICE_ISSUE_PREFIX}{entry_id}"


def breaker_switch_area_issue_id(entry_id: str, switch_unique_id: str) -> str:
    """Issue id for the notice about one of an entry's breaker switches."""
    return f"{_breaker_switch_area_issue_prefix(entry_id)}{switch_unique_id}"


def _breaker_switch_area_issue_prefix(entry_id: str) -> str:
    """Return what the id of every breaker switch notice of one entry starts with.

    Closed with a separator, so no entry's prefix is the start of another's.
    """
    return f"{BREAKER_SWITCH_AREA_ISSUE_PREFIX}{entry_id}_"


def _area_name(areas: ar.AreaRegistry, area_id: str) -> str:
    """Return an area's name, or its id where the area is not in the registry."""
    area = areas.async_get_area(area_id)
    return area.name if area is not None else area_id


def _markdown_list(lines: Iterable[str]) -> str:
    """Format lines as a markdown list of at most `_MAX_LINES` items, or a dash for none.

    The count of the rest is a paragraph of its own. Directly under the list,
    markdown would read it as more of the last item.
    """
    items = [f"- {line}" for line in lines]
    if not items:
        return _NOTHING_LISTED
    listed = "\n".join(items[:_MAX_LINES])
    if len(items) > _MAX_LINES:
        return f"{listed}\n\nand {len(items) - _MAX_LINES} more"
    return listed


def _panel_name(hass: HomeAssistant, entry: SpanPanelConfigEntry) -> str:
    """Return the name the user knows the panel's device by, or failing that the entry's title."""
    runtime_data = loaded_runtime_data(entry)
    if runtime_data is not None:
        device = dr.async_get(hass).async_get(runtime_data.panel_device_id)
        if device is not None and (name := device.name_by_user or device.name):
            return name
    return entry.title


@callback
def async_raise_conversion_notice(
    hass: HomeAssistant, entry: SpanPanelConfigEntry, report: ConversionReport
) -> None:
    """Raise the notice for what a conversion changed, if it changed anything to list.

    Call it once the platforms are set up. An entity the platforms removed in
    between, a control the panel no longer offers, has no name now and is left
    out.
    """
    if report.empty:
        return

    entity_registry = er.async_get(hass)
    names_after = {
        converted.entity_id: er.async_get_full_entity_name(hass, row)
        for converted in report.entities
        if (row := entity_registry.async_get(converted.entity_id)) is not None
    }

    renamed = [
        f"{converted.name_before or converted.entity_id} → {name_after or converted.entity_id}"
        for converted in report.entities
        if (name_after := names_after.get(converted.entity_id)) is not None
        and converted.name_before != name_after
    ]

    areas = ar.async_get(hass)

    def area_name(area_id: str | None) -> str:
        """Return an area's name, or a dash for no area."""
        return _NOTHING_LISTED if area_id is None else _area_name(areas, area_id)

    area_changes = [
        f"{name_after or change.entity_id}: "
        f"{area_name(change.area_before)} → {area_name(change.area_after)}"
        for change in report.area_changes
        if (name_after := names_after.get(change.entity_id)) is not None
    ]

    if not (renamed or area_changes or report.circuits_left_in_areas):
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        conversion_notice_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_TRANSLATION_KEY,
        translation_placeholders={
            "panel": _panel_name(hass, entry),
            "renamed": _markdown_list(renamed),
            "area_changes": _markdown_list(area_changes),
            "circuits_left_in_areas": _markdown_list(report.circuits_left_in_areas),
        },
        data={"entry_id": entry.entry_id},
    )


@callback
def async_registered_breaker_switches(
    hass: HomeAssistant, entry: SpanPanelConfigEntry, snapshot: SpanPanelSnapshot
) -> dict[str, str]:
    """Return the breaker switches this entry has in the registry: unique id to entity id.

    Those of the circuits in `snapshot`, enabled or not. Read before the
    platforms are forwarded, it is what `async_raise_breaker_switch_area_notices`
    tells a new switch from afterwards.
    """
    registry = er.async_get(hass)
    registered: dict[str, str] = {}
    for circuit_id in snapshot.circuits:
        unique_id = build_switch_unique_id(snapshot.serial_number, circuit_id)
        entity_id = entity_id_in_entry(registry, entry.entry_id, "switch", unique_id)
        if entity_id is not None:
            registered[unique_id] = entity_id
    return registered


@callback
def async_raise_breaker_switch_area_notices(
    hass: HomeAssistant,
    entry: SpanPanelConfigEntry,
    snapshot: SpanPanelSnapshot,
    *,
    registered_before: Container[str],
    converted: Iterable[str],
) -> None:
    """Raise a notice for each breaker switch this setup put in its circuit's device's area.

    Call it once the platforms are set up. `registered_before` holds the unique
    ids `async_registered_breaker_switches` read before they were forwarded, and
    a switch registered now that was not among them is new. `converted` are the
    entity ids of the switches the conversion moved into an area
    (`ConversionReport.breaker_switches_joining_areas`).

    Each is in its device's area only while it has none of its own and the
    device has one, and that is checked here for all of them, as the registry
    now stands: a new switch restored with the area it had raises nothing.
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    areas = ar.async_get(hass)

    new = [
        entity_id
        for unique_id, entity_id in async_registered_breaker_switches(hass, entry, snapshot).items()
        if unique_id not in registered_before
    ]
    for entity_id in (*new, *converted):
        row = entity_registry.async_get(entity_id)
        if row is None or row.area_id is not None or row.device_id is None:
            continue
        device = device_registry.async_get(row.device_id)
        if device is None or device.area_id is None:
            continue
        async_raise_breaker_switch_area_notice(
            hass,
            entry,
            switch_entity_id=entity_id,
            switch_unique_id=row.unique_id,
            area_name=_area_name(areas, device.area_id),
        )


@callback
def async_raise_breaker_switch_area_notice(
    hass: HomeAssistant,
    entry: SpanPanelConfigEntry,
    *,
    switch_entity_id: str,
    switch_unique_id: str,
    area_name: str,
) -> None:
    """Raise the notice that a breaker switch has joined an area, naming both.

    The switch is named as Home Assistant displays it, followed by its entity
    id, which is what an automation or a dashboard knows it by. Any earlier
    notice for the switch is deleted first, so one the user dismissed does not
    keep this one out of sight.
    """
    row = er.async_get(hass).async_get(switch_entity_id)
    name = er.async_get_full_entity_name(hass, row) if row is not None else ""
    identified = f"`{switch_entity_id}`"
    issue_id = breaker_switch_area_issue_id(entry.entry_id, switch_unique_id)
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_BREAKER_SWITCH_AREA_TRANSLATION_KEY,
        translation_placeholders={
            "switch": f"{name} {identified}" if name else identified,
            "area": area_name,
        },
        data={"entry_id": entry.entry_id},
    )


@callback
def async_clear_conversion_notice(hass: HomeAssistant, entry_id: str) -> None:
    """Drop an entry's conversion notice and its breaker switch notices, on removal of the entry."""
    ir.async_delete_issue(hass, DOMAIN, conversion_notice_issue_id(entry_id))
    prefix = _breaker_switch_area_issue_prefix(entry_id)
    for issue_id in [
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(prefix)
    ]:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
