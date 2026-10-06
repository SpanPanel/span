"""Keep the PV entities' ids across the one-inverter and many-inverter layouts.

With one inverter, the `{serial}_pv` card carries that inverter's vendor, model,
nameplate and link under panel-scoped unique ids. With several, each inverter
has a card and per-inverter unique ids of its own (see
`has_multiple_pv_inverters`). Crossing between the two re-keys one inverter's
entities in place, together with its solar extension entities and their
curation records, so every entity_id a user has survives; nothing is deleted.
A per-inverter device the panel stops publishing stays, unavailable, until its
owner removes it, as a departed SPAN Drive does.

Going to several inverters, the entities move onto the primary inverter: the
one `snapshot.pv` describes, chosen by the library. It may not be the inverter
the panel published before it published several, in which case the re-keyed
entities describe the primary one from then on.

Going back to one, they move off the inverter whose per-inverter entities are
the oldest in the registry. Those are the ones re-keyed at the upgrade, which
keep the registry entry, and so the creation time, the user's single-inverter
entities always had; every other inverter's were born at or after the upgrade.
That inverter may have left the panel, so the user's original entity_ids come
back to the single layout rather than staying on a departed inverter's card.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Final

from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from span_panel_api import ExtensionSubject

from .const import CONF_DEVICE_NAME, DOMAIN, PV_PANEL_LINK_KEY
from .curation import async_rekey_records
from .extension import extension_scope
from .helpers import (
    build_binary_sensor_unique_id_for_entry,
    build_pv_inverter_unique_id_for_entry,
    construct_panel_unique_id_for_entry,
    has_multiple_pv_inverters,
)
from .sensor_definitions import PV_METADATA_SENSORS
from .util import ADOPTED_IDENTIFIER_TOKEN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from span_panel_api import SpanPanelSnapshot

    from .coordinator import SpanPanelCoordinator

_LOGGER = logging.getLogger(__name__)

_METADATA: Final[tuple[tuple[Platform, str], ...]] = (
    *((Platform.SENSOR, description.key) for description in PV_METADATA_SENSORS),
    (Platform.BINARY_SENSOR, PV_PANEL_LINK_KEY),
)
"""`(platform, description key)` of every entity the two layouts both carry.

`pv_power` is absent: it is the panel's aggregate in both layouts and keeps its
id.
"""

_KEY_SLOT: Final = "\x00"
"""Stands in for an inverter key, to read the per-inverter id grammar off its builder.

No `pv_inverters` key contains it, and building an id around it splits the
builder's own output into the prefix and suffix every inverter's id shares.
"""


def primary_inverter_key(snapshot: SpanPanelSnapshot) -> str | None:
    """Return the `pv_inverters` key of the inverter `snapshot.pv` describes, if any."""
    key: str | None = snapshot.pv.node_id
    if key is None or key not in snapshot.pv_inverters:
        return None
    return key


async def async_reconcile_pv_inverter_layout(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
) -> list[tuple[str, str]]:
    """Re-key the PV metadata and solar extension entities for the current layout.

    Run at setup before the curation overlay is loaded, so the re-keyed curation
    records are the ones it reads, and before the platforms add entities, so
    each platform finds its entity already registered under the id it is about
    to use. Returns the `(source, target)` unique ids of every entity re-keyed,
    for `additions.async_rekey_announced`.
    """
    registry = er.async_get(hass)
    multiple = has_multiple_pv_inverters(snapshot)
    if multiple:
        key = primary_inverter_key(snapshot)
    else:
        key = _single_layout_source_key(registry, entry.entry_id, coordinator, snapshot)
    if key is None:
        return []
    moves: list[tuple[str, str]] = []
    for platform, single_id, inverter_id in _metadata_ids(coordinator, snapshot, key):
        source, target = (single_id, inverter_id) if multiple else (inverter_id, single_id)
        if _rekey(registry, entry.entry_id, platform, source, target):
            moves.append((source, target))
    scopes = _extension_scopes(key)
    if scopes is not None:
        single_scope, inverter_scope = scopes
        source_scope, target_scope = (
            (single_scope, inverter_scope) if multiple else (inverter_scope, single_scope)
        )
        moves.extend(
            _rekey_extensions(
                registry, entry.entry_id, snapshot.serial_number, source_scope, target_scope
            )
        )
        await async_rekey_records(hass, entry, source_scope, target_scope)
    return moves


def _device_name(coordinator: SpanPanelCoordinator) -> str | None:
    """Return the `device_name` every PV entity passes to its unique-id builder."""
    name: str | None = coordinator.config_entry.data.get(
        CONF_DEVICE_NAME, coordinator.config_entry.title
    )
    return name


def _single_layout_id(
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
    platform: Platform,
    description_key: str,
    device_name: str | None,
) -> str:
    """Build the single-layout id with the call its creating entity makes.

    `SpanPanelBinarySensor._construct_binary_sensor_unique_id` for the link and
    `SpanPVMetadataSensor._generate_unique_id` for the metadata sensors.
    """
    if platform is Platform.BINARY_SENSOR:
        return build_binary_sensor_unique_id_for_entry(
            coordinator, snapshot, description_key, device_name
        )
    return construct_panel_unique_id_for_entry(coordinator, snapshot, description_key, device_name)


def _metadata_ids(
    coordinator: SpanPanelCoordinator, snapshot: SpanPanelSnapshot, key: str
) -> list[tuple[Platform, str, str]]:
    """`(platform, single-layout unique id, per-inverter unique id)` for each re-keyed entity.

    Each id is built by the call its creating entity makes, with the same
    arguments, so the two can never disagree: see `_single_layout_id`, and
    `SpanPVInverterSensor` and `SpanPVInverterBinarySensor` for the per-inverter
    ids.
    """
    device_name = _device_name(coordinator)
    return [
        (
            platform,
            _single_layout_id(coordinator, snapshot, platform, description_key, device_name),
            build_pv_inverter_unique_id_for_entry(
                coordinator, snapshot, key, description_key, device_name
            ),
        )
        for platform, description_key in _METADATA
    ]


def _single_layout_source_key(
    registry: er.EntityRegistry,
    entry_id: str,
    coordinator: SpanPanelCoordinator,
    snapshot: SpanPanelSnapshot,
) -> str | None:
    """Choose the inverter whose per-inverter entities go back to the single layout.

    The one whose per-inverter metadata entity is the oldest in this entry's
    registry, for the reason the module docstring gives; on a tie, the
    remaining inverter. With no per-inverter entity at all -- a panel that has
    only ever had one inverter -- the remaining inverter, which then has
    nothing to move.

    None once any single-layout entity is registered: the user's entities are
    already home, and moving another inverter's onto their ids would only
    collide, at every setup after the one that brought them back.
    """
    remaining = next(iter(snapshot.pv_inverters), None)
    device_name = _device_name(coordinator)
    for platform, description_key in _METADATA:
        single_id = _single_layout_id(coordinator, snapshot, platform, description_key, device_name)
        entity_id = registry.async_get_entity_id(platform, DOMAIN, single_id)
        held = registry.async_get(entity_id) if entity_id is not None else None
        if held is not None and held.config_entry_id == entry_id:
            return None
    templates: list[tuple[Platform, str, str]] = []
    for platform, description_key in _METADATA:
        prefix, _, suffix = build_pv_inverter_unique_id_for_entry(
            coordinator, snapshot, _KEY_SLOT, description_key, device_name
        ).partition(_KEY_SLOT)
        templates.append((platform, prefix, suffix))
    oldest: dict[str, datetime] = {}
    for entity in er.async_entries_for_config_entry(registry, entry_id):
        for platform, prefix, suffix in templates:
            unique_id = entity.unique_id
            if (
                entity.domain != platform
                or len(unique_id) <= len(prefix) + len(suffix)
                or not unique_id.startswith(prefix)
                or not unique_id.endswith(suffix)
            ):
                continue
            key = unique_id[len(prefix) : len(unique_id) - len(suffix)]
            if key not in oldest or entity.created_at < oldest[key]:
                oldest[key] = entity.created_at
    if not oldest:
        return remaining
    return min(oldest, key=lambda key: (oldest[key], key != remaining))


def _extension_scopes(key: str) -> tuple[str, str] | None:
    """Return the solar extension scope with one inverter, and inverter `key`'s with several.

    Read from `extension_scope`, which mints the scope segment of both the
    extension unique ids and the curation keys, so the re-key cannot drift from
    either. `None` only if it ever declined a PV subject.
    """
    single = extension_scope(ExtensionSubject(kind="pv"))
    inverter = extension_scope(ExtensionSubject(kind="pv", instance_key=key))
    if single is None or inverter is None:
        return None
    return single, inverter


def _rekey(
    registry: er.EntityRegistry, entry_id: str, platform: str, source: str, target: str
) -> bool:
    entity_id = registry.async_get_entity_id(platform, DOMAIN, source)
    if entity_id is None:
        return False
    entry = registry.async_get(entity_id)
    if entry is None or entry.config_entry_id != entry_id:
        return False
    if registry.async_get_entity_id(platform, DOMAIN, target) is not None:
        _LOGGER.warning(
            "Not re-keying %s: %s is already registered. %s stays unavailable and can be "
            "deleted from its device page",
            entity_id,
            target,
            entity_id,
        )
        return False
    registry.async_update_entity(entity_id, new_unique_id=target)
    _LOGGER.info("Re-keyed %s from %s to %s", entity_id, source, target)
    return True


def _rekey_extensions(
    registry: er.EntityRegistry, entry_id: str, serial: str, source_scope: str, target_scope: str
) -> list[tuple[str, str]]:
    source = f"span_{serial}_{ADOPTED_IDENTIFIER_TOKEN}_{source_scope}/"
    target = f"span_{serial}_{ADOPTED_IDENTIFIER_TOKEN}_{target_scope}/"
    moves: list[tuple[str, str]] = []
    for entity in list(er.async_entries_for_config_entry(registry, entry_id)):
        if not entity.unique_id.startswith(source):
            continue
        moved = target + entity.unique_id[len(source) :]
        if _rekey(registry, entry_id, entity.domain, entity.unique_id, moved):
            moves.append((entity.unique_id, moved))
    return moves
