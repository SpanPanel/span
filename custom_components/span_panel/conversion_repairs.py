"""The notices circuit devices call for: one for an install's conversion, one per breaker in a room.

## The conversion notice

`circuit_device_conversion` moves the entities and reports what it changed.
This module tells the user: which displayed names changed, which entities
changed area, and which circuits kept their entities' areas as they were.

- **Raised after the platforms are set up,** so each name after the move is the
  one Home Assistant composes for the entity as it now stands, by the same
  function that composed the name before it (`async_get_full_entity_name`).
  Only the names that differ are listed.
- **Persistent.** The conversion moves each entity once, so nothing can derive
  its lines again at a later setup, and a notice that is not persistent comes
  back inactive after a restart.
- **Not fixable.** There is nothing to repair. The user dismisses it once read.
- **Severity `WARNING`.** Everything keeps working, and names the user sees
  have changed.
- **One per config entry,** its id carrying the entry's, so two panels each
  report their own circuits. Linked to the entry, so Home Assistant deletes it
  with the entry.
- **Raised only when there is something to list.** A run with nothing to
  list, as most setups after the first are, leaves an earlier notice exactly as
  it stands, dismissed or not.
- **Added to by a later run, and shown again.** A setup after the first still
  converts the entities it is the first to build: a breaker switch or priority
  select once controls are switched on, a Net Energy sensor once those are, a
  Current or Breaker Rating sensor whose value the panel publishes again, a
  circuit that returns. Its lines follow the earlier run's, which no setup can
  derive again, and the earlier notice is deleted before this one is raised.
  Raised over it instead, Home Assistant would keep a dismissal, hiding the new
  lines from the user who had read the old ones, and would replace the lists,
  losing the old lines for the user who had not.

The lists reach the description as placeholders, already formatted as markdown
lists, and every line is listed however many there are: the notice is the only
record of what a name or an area was. The lines themselves are kept in the
issue's data, which is what a later run adds to.

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
  it. Home Assistant starts a platform's add as a task and promises it finished
  only once the platform's setup has returned to the forward. The task usually
  registers every entity before the platform's own call returns, but an entity
  whose add yields leaves those after it unregistered, so the platform cannot
  rely on seeing the rows it asked for. After the forward is also where both
  kinds of switch are known, the new ones and those the conversion reported, so
  one check decides for all of them. The registry is read once before the
  platforms are forwarded (`async_registered_breaker_switches`) and again
  after, and a switch in the second reading alone is new. Read from the
  registry, a switch registered disabled counts: enabling it is all it takes
  for the area to reach it.
- **Persistent, not fixable, severity `WARNING`,** for the reasons the
  conversion notice is. What to do about a breaker in a room is the user's
  choice: give the switch an area of its own, clear the device's, or leave it.
- **One per switch,** its id carrying the entry's and the switch's unique id.
  Linked to the entry, as the conversion notice is.
- **Raised afresh each time.** A switch the panel withdraws and offers again
  joins the area again, and a notice raised over one the user dismissed would
  stay dismissed, so the earlier one is deleted first.
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Mapping
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
from .entry_issues import EntryIssue, EntryIssueFamily, async_create_entry_issue
from .id_builder import build_switch_unique_id
from .runtime import SpanPanelConfigEntry, loaded_runtime_data

_TRANSLATION_KEY: Final = "circuit_devices_converted"

_BREAKER_SWITCH_AREA_TRANSLATION_KEY: Final = "breaker_switch_in_area"

_NOTHING_LISTED: Final = "—"
"""What stands in for a list with nothing in it."""

_RENAMED: Final = "renamed"
_AREA_CHANGES: Final = "area_changes"
_CIRCUITS_LEFT_IN_AREAS: Final = "circuits_left_in_areas"
"""The conversion notice's lists.

Each is the name of the placeholder the list fills and the key its lines are
kept under in the issue's data.
"""


def conversion_notice_issue_id(entry_id: str) -> str:
    """Issue id for one entry's conversion notice."""
    return EntryIssue.CIRCUIT_DEVICES_CONVERTED.issue_id(entry_id)


def breaker_switch_area_issue_id(entry_id: str, switch_unique_id: str) -> str:
    """Issue id for the notice about one of an entry's breaker switches."""
    return EntryIssueFamily.BREAKER_SWITCH_IN_AREA.issue_id(entry_id, switch_unique_id)


@callback
def async_rekey_circuit_ids(
    hass: HomeAssistant, entry: SpanPanelConfigEntry, serial: str, mapping: Mapping[str, str]
) -> int:
    """Move each breaker switch notice from its switch's old unique id to its new one.

    A notice's id carries the switch's unique id, which the device-id migration
    re-keys. A notice follows its switch: where the switch now carries the new
    unique id, re-keyed from the old one, the same notice is raised again
    under the new id -- its severity, persistence, text and data copied --
    ignored again if the user had ignored it, and the old one deleted. Its
    `created` becomes now, and an ignored one's `dismissed_version` the
    running Home Assistant version. Read from the registry as it stands, so a
    setup that stopped before this step leaves the next to finish it.

    A notice an older release raised under the new id, about the second
    switch it registered after the panel changed its ids, gives way. The
    migration deletes it with that switch (`async_forget_breaker_switch`); one
    whose switch went before -- withdrawn by the panel, or deleted by the user
    -- is deleted here, before the original's takes its key, since raising a
    notice over a standing one keeps that one's dismissal. Only a notice whose
    switch now holds the key moves, so what is deleted is never about the
    switch under it. A notice whose switch was not carried over -- one the
    user deleted while it read nothing -- stays as it was, and so does the
    notice of the switch now under the new id. `serial` is the panel's, as
    `build_switch_unique_id` takes it. Returns how many notices moved.
    """
    registry = ir.async_get(hass)
    entity_registry = er.async_get(hass)
    moved = 0
    for old_id, new_id in mapping.items():
        old_switch = build_switch_unique_id(serial, old_id)
        old_issue_id = breaker_switch_area_issue_id(entry.entry_id, old_switch)
        issue = registry.async_get_issue(DOMAIN, old_issue_id)
        if (
            issue is None
            or issue.is_fixable is None
            or issue.severity is None
            or issue.translation_key is None
        ):
            # None, or one Home Assistant restored without what raising it
            # again needs: a notice is persistent, so it always has it.
            continue
        new_switch = build_switch_unique_id(serial, new_id)
        switch = entity_id_in_entry(entity_registry, entry.entry_id, "switch", new_switch)
        row = entity_registry.async_get(switch) if switch is not None else None
        if row is None or row.previous_unique_id != old_switch:
            continue
        ir.async_delete_issue(
            hass, DOMAIN, breaker_switch_area_issue_id(entry.entry_id, new_switch)
        )
        async_create_entry_issue(
            hass,
            entry,
            EntryIssueFamily.BREAKER_SWITCH_IN_AREA.for_key(new_switch),
            is_fixable=issue.is_fixable,
            is_persistent=issue.is_persistent,
            severity=issue.severity,
            translation_key=issue.translation_key,
            translation_placeholders=issue.translation_placeholders,
            data=issue.data,
        )
        if issue.dismissed_version is not None:
            ir.async_ignore_issue(
                hass, DOMAIN, breaker_switch_area_issue_id(entry.entry_id, new_switch), True
            )
        ir.async_delete_issue(hass, DOMAIN, old_issue_id)
        moved += 1
    return moved


@callback
def async_forget_breaker_switch(hass: HomeAssistant, entry_id: str, switch_unique_id: str) -> None:
    """Delete the area notice about a breaker switch whose row is being removed.

    The notice names the switch, so it outlives the row only as a notice about
    nothing. The device-id migration removes a second breaker switch an older
    release registered under a new id, and its notice with it.
    """
    ir.async_delete_issue(hass, DOMAIN, breaker_switch_area_issue_id(entry_id, switch_unique_id))


def _area_name(areas: ar.AreaRegistry, area_id: str) -> str:
    """Return an area's name, or its id where the area is not in the registry."""
    area = areas.async_get_area(area_id)
    return area.name if area is not None else area_id


def _markdown_list(lines: Iterable[str]) -> str:
    """Format lines as a markdown list, or a dash for none."""
    return "\n".join(f"- {line}" for line in lines) or _NOTHING_LISTED


def _one_line(text: str) -> str:
    """Return text on one line, which is what a list item and a kept line each are."""
    return " ".join(text.splitlines())


def _lines_kept(issue: ir.IssueEntry | None, listed: str) -> list[str]:
    """Return the lines an earlier notice kept for one of its lists, or none."""
    if issue is None or issue.data is None:
        return []
    kept = issue.data.get(listed)
    return kept.split("\n") if isinstance(kept, str) and kept else []


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

    Where an earlier run's notice stands, this run's lines are added after the
    lines it kept, and it is deleted before the notice is raised again, so the
    user who dismissed it sees what is new and nobody loses what was listed.
    A circuit already listed as left in its areas is not listed twice.
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
        _one_line(
            f"{converted.name_before or converted.entity_id} → {name_after or converted.entity_id}"
        )
        for converted in report.entities
        if (name_after := names_after.get(converted.entity_id)) is not None
        and converted.name_before != name_after
    ]

    areas = ar.async_get(hass)

    def area_name(area_id: str | None) -> str:
        """Return an area's name, or a dash for no area."""
        return _NOTHING_LISTED if area_id is None else _area_name(areas, area_id)

    area_changes = [
        _one_line(
            f"{name_after or change.entity_id}: "
            f"{area_name(change.area_before)} → {area_name(change.area_after)}"
        )
        for change in report.area_changes
        if (name_after := names_after.get(change.entity_id)) is not None
    ]
    circuits_left_in_areas = [_one_line(name) for name in report.circuits_left_in_areas]

    if not (renamed or area_changes or circuits_left_in_areas):
        return

    issue_id = conversion_notice_issue_id(entry.entry_id)
    earlier = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    lists = {
        _RENAMED: [*_lines_kept(earlier, _RENAMED), *renamed],
        _AREA_CHANGES: [*_lines_kept(earlier, _AREA_CHANGES), *area_changes],
        _CIRCUITS_LEFT_IN_AREAS: list(
            dict.fromkeys([*_lines_kept(earlier, _CIRCUITS_LEFT_IN_AREAS), *circuits_left_in_areas])
        ),
    }

    ir.async_delete_issue(hass, DOMAIN, issue_id)
    async_create_entry_issue(
        hass,
        entry,
        EntryIssue.CIRCUIT_DEVICES_CONVERTED,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_TRANSLATION_KEY,
        translation_placeholders={
            "panel": _panel_name(hass, entry),
            **{listed: _markdown_list(lines) for listed, lines in lists.items()},
        },
        data={
            "entry_id": entry.entry_id,
            **{listed: "\n".join(lines) for listed, lines in lists.items()},
        },
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
    ir.async_delete_issue(
        hass, DOMAIN, breaker_switch_area_issue_id(entry.entry_id, switch_unique_id)
    )
    async_create_entry_issue(
        hass,
        entry,
        EntryIssueFamily.BREAKER_SWITCH_IN_AREA.for_key(switch_unique_id),
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
