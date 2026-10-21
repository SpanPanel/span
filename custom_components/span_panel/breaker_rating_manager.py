"""Stored breaker rating overrides for one panel, and the announcements they raise.

`breaker_ratings` is the rule: whether an override applies, and what to tell the
user when the panel overtakes one. This module is what holds the overrides and
acts on that rule. It stores them per config entry, reconciles them against each
complete snapshot, fires an event for every change so a card can re-read its
ratings, and keeps one notice per panel listing what the user has not yet seen.

Nothing here decides whether a rating applies. Every comparison is a call into
`breaker_ratings`, so every reader of a rating agrees.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import TYPE_CHECKING, Final, TypedDict

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from span_panel_api import FieldMetadata, SpanPanelSnapshot

from . import notices
from .breaker_ratings import (
    BreakerRating,
    ChangeReason,
    Declaration,
    OverrideChange,
    StoredOverride,
    reconcile as reconcile_overrides,
    resolve as resolve_rating,
    service_rating_a,
    service_rating_declaration,
)
from .const import DOMAIN, EVENT_BREAKER_RATING_CHANGED
from .helpers import construct_circuit_label

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION: Final = 1
_STORAGE_KEY_PREFIX: Final = "span_panel_breaker_ratings"

_NOTICE_ID: Final = "breaker_rating_changed"

_REASONS: Final[tuple[ChangeReason, ...]] = (
    "panel_changed",
    "panel_agrees",
    "rating_undeclared",
    "circuit_removed",
    "above_service_rating",
    "service_rating_undeclared",
)


class _StoredOverrideRow(TypedDict):
    rating_a: int
    panel_a_at_set: float
    label: str


class _StoredChangeRow(TypedDict):
    circuit_id: str
    label: str
    reason: ChangeReason
    rating_a: int
    panel_before_a: float
    panel_after_a: float | None
    service_a: float | None
    removed: bool


class StoredRatings(TypedDict):
    """What is written to disk for one panel."""

    overrides: dict[str, _StoredOverrideRow]
    unacknowledged: dict[str, _StoredChangeRow]


def _number(value: object) -> float | None:
    """Return a JSON number as a float; None for anything else, a bool included."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _load_overrides(stored: object) -> dict[str, StoredOverride]:
    """Read the overrides off disk, dropping any row that is not what was written."""
    rows = stored.get("overrides") if isinstance(stored, dict) else None
    if not isinstance(rows, dict):
        return {}
    kept: dict[str, StoredOverride] = {}
    for circuit_id, row in rows.items():
        if not isinstance(row, dict):
            continue
        rating, panel, label = (
            row.get("rating_a"),
            _number(row.get("panel_a_at_set")),
            row.get("label"),
        )
        if (
            isinstance(rating, int)
            and not isinstance(rating, bool)
            and panel is not None
            and isinstance(label, str)
        ):
            kept[str(circuit_id)] = StoredOverride(rating, panel, label)
        else:
            _LOGGER.warning("Dropping a malformed breaker rating override for %s", circuit_id)
    return kept


def _integer(value: object) -> int | None:
    """Return a JSON integer; None for anything else, a bool included."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _load_change(circuit_id: str, row: object) -> OverrideChange | None:
    """Read one change off disk, or None where the row is not what was written."""
    if not isinstance(row, dict):
        return None
    reason = row.get("reason")
    rating = _integer(row.get("rating_a"))
    before = _number(row.get("panel_before_a"))
    after = _number(row.get("panel_after_a"))
    service = _number(row.get("service_a"))
    label, removed = row.get("label"), row.get("removed")
    if reason not in _REASONS or rating is None or before is None:
        return None
    if not isinstance(label, str) or not isinstance(removed, bool):
        return None
    if (after is None) != (row.get("panel_after_a") is None):
        return None
    if (service is None) != (row.get("service_a") is None):
        return None
    return OverrideChange(circuit_id, label, reason, rating, before, after, service, removed)


def _load_unacknowledged(stored: object) -> dict[str, OverrideChange]:
    """Read the changes the user has not yet seen, dropping any row that is not what was written."""
    rows = stored.get("unacknowledged") if isinstance(stored, dict) else None
    if not isinstance(rows, dict):
        return {}
    kept: dict[str, OverrideChange] = {}
    for circuit_id, row in rows.items():
        change = _load_change(str(circuit_id), row)
        if change is None:
            _LOGGER.warning("Dropping a malformed breaker rating change for %s", circuit_id)
        else:
            kept[str(circuit_id)] = change
    return kept


def _figure(value: float | None) -> str:
    """Print a rating as the notice does: 20, not 20.0."""
    return "" if value is None else f"{value:g}"


class BreakerRatingManager:
    """Holds one panel's breaker rating overrides and tells the user when they change."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager with nothing stored and no first reconcile run."""
        self._hass = hass
        self._entry = entry
        self._overrides: dict[str, StoredOverride] = {}
        self._unacknowledged: dict[str, OverrideChange] = {}
        self._service: Declaration = "unknown"
        self._begun = False
        self._announce_pending = False
        self._store: Store[StoredRatings] = Store(
            hass,
            _STORAGE_VERSION,
            f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )

    async def async_load(self) -> None:
        """Load the overrides and the unacknowledged changes from HA Storage."""
        data = await self._store.async_load()
        self._overrides = _load_overrides(data)
        self._unacknowledged = _load_unacknowledged(data)

    def resolve(self, circuit_id: str, snapshot: SpanPanelSnapshot) -> BreakerRating:
        """Return the rating a reader should use for one circuit.

        Judged against the service rating declaration of the most recent
        reconcile, which is "unknown" until `begin`, so before then it returns
        the panel's rating.
        """
        return resolve_rating(
            snapshot.circuits.get(circuit_id),
            self._overrides.get(circuit_id),
            service_rating_a(snapshot),
            self._service,
        )

    def begin(
        self,
        snapshot: SpanPanelSnapshot,
        field_metadata: Mapping[str, FieldMetadata] | None,
    ) -> None:
        """Run the first reconcile of a setup, and let later ones run.

        Until this is called `reconcile` does nothing: an override must not be
        judged against a snapshot that is not yet the panel's complete state.
        """
        self._begun = True
        self.reconcile(snapshot, field_metadata)

    def reconcile(
        self,
        snapshot: SpanPanelSnapshot,
        field_metadata: Mapping[str, FieldMetadata] | None,
    ) -> None:
        """Apply the rule to every stored override and announce what changed."""
        if not self._begun:
            return
        self._service = service_rating_declaration(field_metadata)
        result = reconcile_overrides(
            self._overrides, snapshot, self._service, construct_circuit_label
        )
        stored_changed = result.overrides != self._overrides
        self._overrides = result.overrides
        if result.changes:
            self._record(result.changes)
        if stored_changed or result.changes:
            self._save()
        if result.changes:
            self._fire(change.circuit_id for change in result.changes)
            self._announce_pending = True
            self._hass.async_create_task(self._async_announce())

    def set_override(self, circuit_id: str, snapshot: SpanPanelSnapshot, rating_a: int) -> None:
        """Correct a circuit's breaker rating.

        Raises ServiceValidationError where the circuit is not on the panel, the
        panel publishes no rating for it, the panel publishes no service rating
        to bound the correction, or the correction is above it. A correction
        equal to the panel's rating is a clear.
        """
        circuit = snapshot.circuits.get(circuit_id)
        if circuit is None:
            raise _refusal("breaker_rating_circuit_unknown")
        panel = circuit.breaker_rating_a
        if panel is None:
            raise _refusal("breaker_rating_not_published")
        service_a = service_rating_a(snapshot)
        if self._service != "declared" or service_a is None:
            raise _refusal("breaker_rating_no_service_rating")
        if rating_a > service_a:
            raise _refusal("breaker_rating_above_service_rating", service_rating=_figure(service_a))
        if rating_a == panel:
            self._remove(circuit_id)
            return
        override = StoredOverride(rating_a, panel, construct_circuit_label(circuit, circuit_id))
        if self._overrides.get(circuit_id) == override:
            return
        self._overrides[circuit_id] = override
        self._save()
        self._fire([circuit_id])

    def clear_override(self, circuit_id: str, snapshot: SpanPanelSnapshot) -> None:
        """Remove a circuit's correction; raises where the circuit is not on the panel."""
        if circuit_id not in snapshot.circuits:
            raise _refusal("breaker_rating_circuit_unknown")
        self._remove(circuit_id)

    def _remove(self, circuit_id: str) -> None:
        if self._overrides.pop(circuit_id, None) is None:
            return
        self._save()
        self._fire([circuit_id])

    def _record(self, changes: tuple[OverrideChange, ...]) -> None:
        """Merge changes into those the user has not seen, the latest per circuit winning.

        What the user has seen is what was on screen: once the notice has been
        dismissed the earlier changes are spent. A notice still waiting to be
        raised counts as on screen, or two changes in a row would drop the first.
        """
        if not self._announce_pending and not notices.is_standing(
            self._hass, self._entry, _NOTICE_ID
        ):
            self._unacknowledged = {}
        for change in changes:
            self._unacknowledged[change.circuit_id] = change

    async def _async_announce(self) -> None:
        """Raise the notice listing every unacknowledged change.

        Reading the translations is blocking file I/O, so it runs in an executor
        and the raise is deferred to a task, as the schema-change notice is.
        """
        try:
            text = await self._hass.async_add_executor_job(
                notices.read_translations, self._hass.config.language, _NOTICE_ID
            )
        finally:
            self._announce_pending = False
        lines = [
            "- "
            + text.get(change.reason, "**{circuit}**").format(
                circuit=change.label,
                rating=change.rating_a,
                before=_figure(change.panel_before_a),
                after=_figure(change.panel_after_a),
                service=_figure(change.service_a),
            )
            for change in self._unacknowledged.values()
        ]
        message = "\n\n".join(filter(None, [text.get("intro", ""), "\n".join(lines)]))
        notices.async_raise(
            self._hass,
            self._entry,
            _NOTICE_ID,
            title=text.get("title") or "SPAN Panel breaker ratings changed",
            message=message,
        )

    def _fire(self, circuit_ids: Iterable[str]) -> None:
        self._hass.bus.async_fire(
            EVENT_BREAKER_RATING_CHANGED,
            {"config_entry_id": self._entry.entry_id, "circuit_ids": sorted(set(circuit_ids))},
        )

    def _save(self) -> None:
        self._hass.async_create_task(self.async_save())

    async def async_save(self) -> None:
        """Persist the overrides and the unacknowledged changes to HA Storage."""
        await self._store.async_save(
            StoredRatings(
                overrides={
                    circuit_id: _StoredOverrideRow(
                        rating_a=override.rating_a,
                        panel_a_at_set=override.panel_a_at_set,
                        label=override.label,
                    )
                    for circuit_id, override in self._overrides.items()
                },
                unacknowledged={
                    circuit_id: _change_row(change)
                    for circuit_id, change in self._unacknowledged.items()
                },
            )
        )


def _change_row(change: OverrideChange) -> _StoredChangeRow:
    return _StoredChangeRow(
        circuit_id=change.circuit_id,
        label=change.label,
        reason=change.reason,
        rating_a=change.rating_a,
        panel_before_a=change.panel_before_a,
        panel_after_a=change.panel_after_a,
        service_a=change.service_a,
        removed=change.removed,
    )


def _refusal(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )
