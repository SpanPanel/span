"""WebSocket commands for curating adopted entities.

Separate from `websocket.py` because the two answer different questions about
the same panel. That module reports the *curated* topology -- circuits, tabs,
sub-devices, the entity ids a dashboard renders from -- and its readers are
dashboards. This one reports what the panel publishes that nobody has modelled
yet, and its reader is an editor: every row carries the choices the user may
assert, computed from the wire declaration through Core's own maps, so the card
never offers an option the curate command would refuse.

**Nothing here writes registry state, and that is a boundary rather than an
omission.** Enabling, naming, icons, areas and display units are registry acts
the user makes through Core's own websocket commands, which already ask for
admin and already carry the undo. This module owns exactly the metadata Core has
nowhere to put -- a state class, a device class and prominence for an entity
built from a vendor declaration -- and `curation.py` owns whether an assertion
is admissible.

This module must not import `websocket.py`: registration runs the other way, so
the dependency has one direction and no cycle can appear as further commands
join the ones here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import voluptuous as vol

from .adoption import (
    adopted_curation_key,
    adopted_device_label,
    adopted_unique_id,
    classify,
    humanised,
    resolve_identifier,
)
from .const import DOMAIN
from .curation import (
    CurationOverlay,
    RowContext,
    allowed_device_classes,
    allowed_state_classes,
    record_as_dict,
)
from .extension import adoptable, extension_curation_key, resolve_platform
from .runtime import SpanPanelRuntimeData, loaded_runtime_data

if TYPE_CHECKING:
    from span_panel_api import SpanPanelSnapshot


@dataclass(frozen=True, slots=True)
class _AdoptableRow:
    """One curatable row: what it is on the wire, and where it renders.

    One derivation of what is curatable, rather than one per command. The key a
    row carries is what the store is keyed on and what the editor hands back
    when the user asserts something, so a second derivation of the same set
    would let the editor offer a row the store cannot resolve.
    """

    key: str
    """The curation-store key -- scope-prefixed and injective, per `curation.py`."""

    path: str
    """The `{node}/{property}` wire address, as the capability catalogs spell it."""

    context: RowContext
    """The declaration, as far as validation and the allowed-choice helpers need it."""

    unique_id: str
    """The id this row's entity carries, whether or not that entity exists yet."""

    device_identifier: str
    """The registry identifier of the card this row renders on.

    The grouping key rather than `device_registry_id`, because the registry id is
    absent for a device adopted since the last setup and two such devices must
    not collapse into one group.
    """

    device_registry_id: str | None
    """The card's registry id, or None while the card is still to be created."""

    device_label: str
    """What that card is called, for a group heading the user can recognise."""

    name: str
    """The entity's own name, in the same wire vocabulary the entity carries."""

    settable: bool
    """Whether the panel accepts a write. Declaration fact, reported for triage."""

    adopted_device: bool
    """Whether the card is one adoption minted, rather than a curated device."""


@websocket_api.websocket_command(
    {
        vol.Required("type"): "span_panel/adopted/list",
        vol.Required("device_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def handle_adopted_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return every curatable row on this panel, grouped by the device it renders on.

    Admin users pass the HA device registry ID for the **main SPAN panel**, the
    same contract `handle_panel_topology` has: one panel is one entry, and the
    rows come from that entry's snapshot and overlay together.

    Read-only. The response is the editor's whole input -- the stored record, the
    admissible choices, and the names of any stored fields the current
    declaration no longer supports.
    """
    resolved = _resolve_panel_entry(hass, connection, msg)
    if resolved is None:
        return
    _entry, runtime_data, snapshot = resolved

    entity_registry = er.async_get(hass)
    devices: list[dict[str, Any]] = []
    # Each device's row list, held here as the same object its group carries, so
    # one pass both opens the group and fills it.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(hass, snapshot):
        rows = grouped.get(row.device_identifier)
        if rows is None:
            rows = []
            grouped[row.device_identifier] = rows
            devices.append(
                {
                    "device_id": row.device_registry_id,
                    "name": row.device_label,
                    "adopted_device": row.adopted_device,
                    "rows": rows,
                }
            )
        rows.append(_row_payload(row, runtime_data.curation, entity_registry))

    connection.send_result(msg["id"], {"devices": devices})


def _row_payload(
    row: _AdoptableRow,
    overlay: CurationOverlay,
    entity_registry: er.EntityRegistry,
) -> dict[str, Any]:
    """Return the wire record for one row: its declaration, its record, its choices.

    **The record is reported as stored, not as it would be applied.** Entity
    construction reads the same record through `for_row`, which drops what the
    current declaration no longer supports -- that is right for an entity and
    wrong for an editor, because a silently sanitised record shows the user an
    assertion they never made and hides that theirs was dropped. So the stored
    fields go out verbatim, beside `stale_fields` naming the ones the wire has
    outgrown.
    """
    record = overlay.record_for(row.key)
    return {
        "key": row.key,
        "path": row.path,
        "platform": row.context.platform.value,
        "entity_id": entity_registry.async_get_entity_id(
            row.context.platform.value, DOMAIN, row.unique_id
        ),
        "datatype": row.context.datatype,
        "unit": row.context.unit,
        "settable": row.settable,
        "name": row.name,
        "curation": {} if record is None else record_as_dict(record),
        "allowed_device_classes": allowed_device_classes(row.context),
        "allowed_state_classes": allowed_state_classes(row.context),
        "stale_fields": list(overlay.stale_fields(row.key, row.context)),
    }


def _rows(hass: HomeAssistant, snapshot: SpanPanelSnapshot) -> list[_AdoptableRow]:
    """Every row on this panel a user may curate, in a deterministic order.

    Both halves of vendor extensibility, resolved through the same functions the
    entity builders use rather than beside them: `resolve_identifier` and
    `classify` for a device nobody modelled, `adoptable` and `resolve_platform`
    for a vendor property on a device this integration does model. A second
    derivation here would let the editor disagree with the entities it edits --
    offering a state class for a row that is really a control, or a key the
    curate command cannot resolve.

    `adoptable` is what decides which extension rows exist at all, so the cap and
    the wait-for-the-card deferral apply here exactly as they do to the entities:
    a row it declines has no entity, no card to group under and no name to show.

    Adopted declarations are sorted by `path` for the same reason `_create`
    sorts them -- adapter emission order tracks the wire, so an order derived
    from it moves when a firmware update declares a property earlier. Extension
    rows are sorted for a weaker version of the same reason: `adoptable` returns
    the already-registered rows first, so an unsorted list would reshuffle the
    card the moment a new row's entity appeared.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    rows: list[_AdoptableRow] = []

    for device in snapshot.adopted_devices:
        identifier = resolve_identifier(device_registry, snapshot.serial_number, device)
        card = device_registry.async_get_device(identifiers={(DOMAIN, identifier)})
        for declaration in sorted(device.properties, key=lambda row: row.path):
            rows.append(
                _AdoptableRow(
                    key=adopted_curation_key(identifier, declaration),
                    path=declaration.path,
                    context=RowContext(
                        platform=classify(declaration),
                        datatype=declaration.datatype,
                        unit=declaration.unit,
                    ),
                    unique_id=adopted_unique_id(identifier, declaration),
                    device_identifier=identifier,
                    device_registry_id=None if card is None else card.id,
                    device_label=_device_label(card, adopted_device_label(device)),
                    name=humanised(declaration.property_id),
                    settable=declaration.settable,
                    adopted_device=True,
                )
            )

    for extension, unique_id, identifier in sorted(
        adoptable(snapshot, device_registry, entity_registry),
        key=lambda adopted: (adopted[2], adopted[0].path),
    ):
        key = extension_curation_key(extension.subject, extension.path)
        card = device_registry.async_get_device(identifiers={(DOMAIN, identifier)})
        if key is None or card is None:
            # Neither happens: `adoptable` declines a subject with no scope, which
            # is exactly what `extension_curation_key` declines, and it declines a
            # card the registry does not hold. Both branches are the type system
            # holding those contracts to one answer rather than cases to handle.
            continue
        rows.append(
            _AdoptableRow(
                key=key,
                path=extension.path,
                context=RowContext(
                    platform=resolve_platform(entity_registry, unique_id, extension.datatype),
                    datatype=extension.datatype,
                    unit=extension.unit,
                ),
                unique_id=unique_id,
                device_identifier=identifier,
                device_registry_id=card.id,
                device_label=_device_label(card, identifier),
                name=f"{humanised(extension.node_id)} {humanised(extension.property_id)}",
                settable=extension.settable,
                adopted_device=False,
            )
        )

    return rows


def _device_label(card: dr.DeviceEntry | None, fallback: str) -> str:
    """Return what this card is called, preferring what the user renamed it to.

    A group heading has to be the name the user sees in their device list, or the
    editor is grouping rows under a device they cannot find. The fallback is for
    a card that does not exist yet -- an adopted device that arrived since the
    last setup -- where the wire's own label is the only name there is.
    """
    if card is None:
        return fallback
    return card.name_by_user or card.name or fallback


def _resolve_panel_entry(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> tuple[ConfigEntry, SpanPanelRuntimeData, SpanPanelSnapshot] | None:
    """Resolve the panel device id in a request to the entry, its runtime data and its snapshot.

    Sends the refusal itself and answers None, so a handler's first line is the
    whole of its validation. The checks and their codes mirror
    `handle_panel_topology`, because the two commands take the same handle and a
    consumer that learned one set of codes must not meet a second.

    Runtime state is reached through `loaded_runtime_data`, per AGENTS.md's
    runtime-data guard: core deletes `runtime_data` on unload, and what is there
    on a loaded entry is whatever the owning integration put there.

    One divergence from topology, in an unreachable case: a device carrying a
    SPAN identifier whose entry the registry no longer holds is refused as
    `not_span_panel` rather than `not_loaded`. Resolving the entry and checking
    its domain is one step here, and a device whose SPAN entry is gone is not a
    SPAN panel any more.
    """
    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get(msg["device_id"])

    if device_entry is None:
        connection.send_error(msg["id"], "device_not_found", "Device not found")
        return None

    if not any(domain == DOMAIN for domain, _ in device_entry.identifiers):
        connection.send_error(msg["id"], "not_span_panel", "Device is not a SPAN Panel device")
        return None

    # Every sub-device registers with via_device_id pointing at the panel.
    if device_entry.via_device_id is not None:
        connection.send_error(
            msg["id"],
            "not_panel_device",
            "Use the SPAN panel device registry ID, not a sub-device.",
        )
        return None

    entry = _config_entry(hass, device_entry)
    if entry is None:
        connection.send_error(msg["id"], "not_span_panel", "Device is not a SPAN Panel device")
        return None

    if entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], "not_loaded", "SPAN Panel integration is not loaded")
        return None

    runtime_data = loaded_runtime_data(entry)
    if runtime_data is None:
        connection.send_error(msg["id"], "not_loaded", "SPAN Panel integration is not loaded")
        return None

    snapshot = runtime_data.coordinator.data
    if snapshot is None:
        connection.send_error(msg["id"], "no_data", "SPAN Panel has not yet provided any data")
        return None

    return entry, runtime_data, snapshot


def _config_entry(hass: HomeAssistant, device_entry: dr.DeviceEntry) -> ConfigEntry | None:
    """Return the SPAN Panel entry this device belongs to, if one still does.

    The entry's domain is checked rather than assumed: a device row may carry
    entries from more than one integration, and the first one is not necessarily
    ours.
    """
    for entry_id in device_entry.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == DOMAIN:
            return entry
    return None
