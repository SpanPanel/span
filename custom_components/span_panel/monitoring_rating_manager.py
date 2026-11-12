"""Stored monitoring ratings for one panel, and the announcements they raise.

`monitoring_ratings` is the rule: whether a monitoring rating applies, and what to tell the
user when the panel overtakes one. This module is what holds the ratings and
acts on that rule. It stores them per config entry, reconciles them against each
complete snapshot, fires an event for every change so a card can re-read its
ratings, and keeps one notice per panel listing what the user has not yet seen.

Nothing here decides whether a rating applies. Every comparison is a call into
`monitoring_ratings`, so every reader of a rating agrees.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
import logging
from typing import TYPE_CHECKING, Final, TypedDict

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from span_panel_api import FieldMetadata, SpanPanelSnapshot

from . import notices
from .const import DOMAIN, EVENT_MONITORING_RATING_CHANGED
from .helpers import construct_circuit_label
from .monitoring_ratings import (
    ChangeReason,
    Declaration,
    MonitoringRating,
    RatingChange,
    StoredRating,
    is_change_reason,
    panel_describes_breakers,
    reconcile as reconcile_ratings,
    resolve as resolve_rating,
    service_rating_a,
    service_rating_declaration,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION: Final = 1
_STORAGE_KEY_PREFIX: Final = "span_panel_monitoring_ratings"

_NOTICE_ID: Final = "monitoring_rating_changed"

type FieldMetadataSource = Callable[[], Mapping[str, FieldMetadata] | None]
"""Reads the adapter's field metadata: the client's `field_metadata`, read on demand."""


class _StoredRatingRow(TypedDict):
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

    ratings: dict[str, _StoredRatingRow]
    unacknowledged: dict[str, _StoredChangeRow]


def _number(value: object) -> float | None:
    """Return a JSON number as a float; None for anything else, a bool included."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _load_ratings(stored: object) -> dict[str, StoredRating]:
    """Read the ratings off disk, dropping any row that is not what was written."""
    rows = stored.get("ratings") if isinstance(stored, dict) else None
    if not isinstance(rows, dict):
        return {}
    kept: dict[str, StoredRating] = {}
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
            kept[str(circuit_id)] = StoredRating(rating, panel, label)
        else:
            _LOGGER.warning("Dropping a malformed monitoring rating for %s", circuit_id)
    return kept


def _integer(value: object) -> int | None:
    """Return a JSON integer; None for anything else, a bool included."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _load_change(circuit_id: str, row: object) -> RatingChange | None:
    """Read one change off disk, or None where the row is not what was written."""
    if not isinstance(row, dict):
        return None
    reason: object = row.get("reason")
    rating = _integer(row.get("rating_a"))
    before = _number(row.get("panel_before_a"))
    after = _number(row.get("panel_after_a"))
    service = _number(row.get("service_a"))
    label: object = row.get("label")
    removed: object = row.get("removed")
    if not is_change_reason(reason) or rating is None or before is None:
        return None
    if not isinstance(label, str) or not isinstance(removed, bool):
        return None
    if (after is None) != (row.get("panel_after_a") is None):
        return None
    if (service is None) != (row.get("service_a") is None):
        return None
    return RatingChange(circuit_id, label, reason, rating, before, after, service, removed)


def _load_unacknowledged(stored: object) -> dict[str, RatingChange]:
    """Read the changes the user has not yet seen, dropping any row that is not what was written."""
    rows = stored.get("unacknowledged") if isinstance(stored, dict) else None
    if not isinstance(rows, dict):
        return {}
    kept: dict[str, RatingChange] = {}
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


class MonitoringRatingManager:
    """Holds one panel's monitoring ratings and tells the user when they change."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager with nothing stored and no first reconcile run."""
        self._hass = hass
        self._entry = entry
        self._ratings: dict[str, StoredRating] = {}
        self._unacknowledged: dict[str, RatingChange] = {}
        self._service: Declaration = "unknown"
        self._begun = False
        self._announce_pending = False
        self._store = _store_for(hass, entry)

    async def async_load(self) -> None:
        """Load the ratings and the unacknowledged changes from HA Storage."""
        data = await self._store.async_load()
        self._ratings = _load_ratings(data)
        self._unacknowledged = _load_unacknowledged(data)

    def resolve(self, circuit_id: str, snapshot: SpanPanelSnapshot) -> MonitoringRating:
        """Return the rating a reader should use for one circuit.

        Judged against the panel's service rating declaration, which is
        "unknown" until `begin`, so before then a circuit with a monitoring rating is pending
        and has no judged rating.
        """
        return resolve_rating(
            snapshot.circuits.get(circuit_id),
            self._ratings.get(circuit_id),
            service_rating_a(snapshot),
            self._service,
            describes_breakers=panel_describes_breakers(snapshot),
        )

    def begin(
        self,
        snapshot: SpanPanelSnapshot,
        field_metadata: Mapping[str, FieldMetadata] | None,
    ) -> None:
        """Run the first reconcile of a setup, and let later ones run.

        Until this is called `reconcile` does nothing: a monitoring rating must not be
        judged against a snapshot that is not yet the panel's complete state.

        The service rating declaration is taken from `field_metadata` here, and
        once it is "declared" or "undeclared" it holds for the session. The
        declaration is static within one: a schema change reloads the entry,
        which starts a new session with a new manager. Taking it again on every
        snapshot would cost a schema walk each time, and would read the None a
        rebuild hands out as "unknown", leaving every monitoring rating unrated for
        that snapshot.
        """
        self._begun = True
        self._service = service_rating_declaration(field_metadata)
        self._reconcile(snapshot)

    def reconcile(self, snapshot: SpanPanelSnapshot, field_metadata: FieldMetadataSource) -> None:
        """Apply the rule to every stored monitoring rating and announce what changed.

        `field_metadata` is read only while the service rating declaration is
        still unknown, as when the metadata was not ready at `begin`.
        """
        if not self._begun:
            return
        if self._service == "unknown":
            self._service = service_rating_declaration(field_metadata())
        self._reconcile(snapshot)

    def _reconcile(self, snapshot: SpanPanelSnapshot) -> None:
        result = reconcile_ratings(self._ratings, snapshot, self._service, construct_circuit_label)
        stored_changed = result.ratings != self._ratings
        self._ratings = result.ratings
        if result.changes:
            self._record(result.changes)
        if stored_changed or result.changes:
            self._save()
        if result.changes:
            self._fire(change.circuit_id for change in result.changes)
            self._announce()

    def set_rating(self, circuit_id: str, snapshot: SpanPanelSnapshot, rating_a: int) -> None:
        """Set a circuit's monitoring rating.

        Raises ServiceValidationError where the circuit is not on the panel, the
        panel describes its multi-load breakers itself, the panel publishes no
        rating for the circuit, the panel publishes no service rating to bound
        the monitoring rating, or the monitoring rating is above it. A
        monitoring rating equal to the panel's rating is a clear.
        """
        circuit = snapshot.circuits.get(circuit_id)
        if circuit is None:
            raise _refusal("monitoring_rating_circuit_unknown")
        if panel_describes_breakers(snapshot):
            raise _refusal("monitoring_rating_panel_describes_breakers")
        panel = circuit.breaker_rating_a
        if panel is None:
            raise _refusal("monitoring_rating_not_published")
        service_a = service_rating_a(snapshot)
        if self._service != "declared" or service_a is None:
            raise _refusal("monitoring_rating_no_service_rating")
        if rating_a > service_a:
            raise _refusal(
                "monitoring_rating_above_service_rating", service_rating=_figure(service_a)
            )
        if rating_a == panel:
            self._remove(circuit_id)
            return
        stored = StoredRating(rating_a, panel, construct_circuit_label(circuit, circuit_id))
        if self._ratings.get(circuit_id) == stored:
            return
        self._ratings[circuit_id] = stored
        self._forget(circuit_id)
        self._save()
        self._fire([circuit_id])

    def clear_rating(self, circuit_id: str, snapshot: SpanPanelSnapshot) -> None:
        """Remove a circuit's monitoring rating; raises where the circuit is not on the panel."""
        if circuit_id not in snapshot.circuits:
            raise _refusal("monitoring_rating_circuit_unknown")
        self._remove(circuit_id)

    def _remove(self, circuit_id: str) -> None:
        if self._ratings.pop(circuit_id, None) is None:
            return
        self._forget(circuit_id)
        self._save()
        self._fire([circuit_id])

    def _forget(self, circuit_id: str) -> None:
        """Drop a circuit the user has just changed from what the notice says.

        The notice reports the panel overtaking a monitoring rating, and the user has
        since replaced it, so its line would be false. A notice left with no line
        is not dismissed on the user's behalf; one with others is raised again
        without it.
        """
        if self._unacknowledged.pop(circuit_id, None) is None or not self._unacknowledged:
            return
        if self._announce_pending or notices.is_standing(self._hass, self._entry, _NOTICE_ID):
            self._announce_pending = True
            self._hass.async_create_task(self._async_announce())

    def _record(self, changes: tuple[RatingChange, ...]) -> None:
        """Merge changes into those the user has not seen, each circuit as it stands.

        What the user has seen is what was on screen: once the notice has been
        dismissed the earlier changes are spent. A notice still waiting to be
        raised counts as on screen, or two changes in a row would drop the first.

        A circuit already listed keeps the panel rating it was listed from, the
        last one the user saw, and takes everything else from the latest change.
        A monitoring rating still in force whose panel rating is back where it was has
        nothing to report, so its line goes; a removal always stands, since the
        monitoring rating is gone whatever the panel now says.
        """
        if not self._announce_pending and not notices.is_standing(
            self._hass, self._entry, _NOTICE_ID
        ):
            self._unacknowledged = {}
        for change in changes:
            earlier = self._unacknowledged.get(change.circuit_id)
            merged = (
                change
                if earlier is None
                else replace(change, panel_before_a=earlier.panel_before_a)
            )
            if merged.removed or merged.panel_after_a != merged.panel_before_a:
                self._unacknowledged[change.circuit_id] = merged
            else:
                self._unacknowledged.pop(change.circuit_id, None)

    def _announce(self) -> None:
        """Raise the notice again as it now stands, or take it down if nothing is left.

        Taking it down is for the panel undoing its own change before the user
        read it. A user's change that empties the notice goes through `_forget`,
        which leaves the notice for the user to dismiss.
        """
        if self._unacknowledged:
            self._announce_pending = True
            self._hass.async_create_task(self._async_announce())
        else:
            notices.async_withdraw(self._hass, self._entry, _NOTICE_ID)

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
        if not self._unacknowledged:
            # Emptied while the text was read: by `_forget`, which leaves what
            # stands alone, or by the panel undoing its change, which took the
            # notice down already.
            return
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
            EVENT_MONITORING_RATING_CHANGED,
            {"config_entry_id": self._entry.entry_id, "circuit_ids": sorted(set(circuit_ids))},
        )

    def _save(self) -> None:
        self._hass.async_create_task(self.async_save())

    async def async_save(self) -> None:
        """Persist the ratings and the unacknowledged changes to HA Storage."""
        await self._store.async_save(
            StoredRatings(
                ratings={
                    circuit_id: _StoredRatingRow(
                        rating_a=stored.rating_a,
                        panel_a_at_set=stored.panel_a_at_set,
                        label=stored.label,
                    )
                    for circuit_id, stored in self._ratings.items()
                },
                unacknowledged={
                    circuit_id: _change_row(change)
                    for circuit_id, change in self._unacknowledged.items()
                },
            )
        )


def _store_for(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredRatings]:
    """Return the store holding one panel's ratings and unacknowledged changes."""
    return Store(hass, _STORAGE_VERSION, f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}")


async def async_forget_monitoring_ratings(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the stored ratings when the entry is removed.

    The keys are wire circuit ids, so a store left behind would be loaded by the
    next entry added for the same panel, re-applying monitoring ratings the user
    removed the panel to be rid of. The notice they raised goes with the
    entry's other notices.
    """
    await _store_for(hass, entry).async_remove()


def _change_row(change: RatingChange) -> _StoredChangeRow:
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
