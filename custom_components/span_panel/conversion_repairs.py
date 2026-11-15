"""The notice raised when setup has moved an install's circuit entities to circuit devices.

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
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

from .circuit_device_conversion import ConversionReport
from .const import DOMAIN
from .runtime import SpanPanelConfigEntry, loaded_runtime_data

CONVERSION_NOTICE_ISSUE_PREFIX: Final = "circuit_devices_converted_"

_TRANSLATION_KEY: Final = "circuit_devices_converted"

_MAX_LINES: Final = 50
"""How many lines one list shows before it counts the rest."""

_NOTHING_LISTED: Final = "—"
"""What stands in for a list with nothing in it."""


def conversion_notice_issue_id(entry_id: str) -> str:
    """Issue id for one entry's conversion notice."""
    return f"{CONVERSION_NOTICE_ISSUE_PREFIX}{entry_id}"


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
        """Return an area's name, or its id where the area is not in the registry."""
        if area_id is None:
            return _NOTHING_LISTED
        area = areas.async_get_area(area_id)
        return area.name if area is not None else area_id

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
def async_clear_conversion_notice(hass: HomeAssistant, entry_id: str) -> None:
    """Drop an entry's conversion notice, on removal of the entry."""
    ir.async_delete_issue(hass, DOMAIN, conversion_notice_issue_id(entry_id))
