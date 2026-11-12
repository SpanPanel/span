"""Package ratings copied from the SPAN app for one panel, and the notices their removal raises.

`package_ratings` is the rule: whether an entry applies, and why one is removed.
This module is what holds the entries and acts on that rule. It stores them per
config entry, reconciles them against each complete snapshot, fires an event for
every change so a card can re-read its ratings, and keeps one notice per panel
listing the removals the user has not yet seen.

Nothing here decides whether an entry applies. Every comparison is a call into
`package_ratings`, so every reader of a rating agrees.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import logging
from typing import TYPE_CHECKING, Final, Literal, TypedDict

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from span_panel_api import FieldMetadata, SpanPanelSnapshot

from . import notices
from .const import DOMAIN, EVENT_PACKAGE_RATINGS_CHANGED
from .helpers import construct_circuit_label
from .package_ratings import (
    ChangeReason,
    Declaration,
    JudgedState,
    MonitoringRating,
    PackageRatings,
    RatingChange,
    Refused,
    StoredPackage,
    admit,
    is_change_reason,
    panel_describes_breakers,
    reconcile as reconcile_packages,
    resolve as resolve_rating,
    service_rating_a,
    service_rating_declaration,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION: Final = 1
_STORAGE_KEY_PREFIX: Final = "span_panel_package_ratings"

_NOTICE_ID: Final = "package_ratings_changed"

type FieldMetadataSource = Callable[[], Mapping[str, FieldMetadata] | None]
"""Reads the adapter's field metadata: the client's `field_metadata`, read on demand."""


class _StoredPackageRow(TypedDict):
    package_ratings_a: list[int]
    space: int
    label: str


class _StoredChangeRow(TypedDict):
    circuit_id: str
    label: str
    reason: ChangeReason
    package_ratings_a: list[int]
    panel_after_a: float | None
    service_a: float | None


class StoredPackages(TypedDict):
    """What is written to disk for one panel."""

    packages: dict[str, _StoredPackageRow]
    unacknowledged: dict[str, _StoredChangeRow]


class PackageDiagnosticsRow(TypedDict):
    """One stored entry, as the diagnostics download shows it.

    `state` is what the rule makes of it against the current snapshot: it
    `applies`, or it is `pending`, kept while a declared panel or service rating,
    or the circuit's space, has not arrived, or while the service rating's
    declaration is not known.
    """

    label: str
    package_ratings_a: list[int]
    space: int
    state: Literal["applies", "pending"]


class PackageRatingsDiagnostics(TypedDict):
    """The diagnostics download's `package_ratings` row, keyed by wire circuit id."""

    panel_describes_breakers: bool
    service_rating_declaration: Declaration
    packages: dict[str, PackageDiagnosticsRow]
    unacknowledged: dict[str, _StoredChangeRow]


def _integer(value: object) -> int | None:
    """Return a JSON integer; None for anything else, a bool included."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: object) -> float | None:
    """Return a JSON number as a float; None for anything else, a bool included."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_number(row: Mapping[object, object], key: str) -> tuple[bool, float | None]:
    """Read a number that may be null: whether the row holds one as written, and its value."""
    raw = row.get(key)
    value = _number(raw)
    return (value is not None) == (raw is not None), value


def _package(value: object) -> PackageRatings | None:
    """Read a package's two ratings, each a whole number of at least 1, or None where it is not that."""
    if not isinstance(value, list) or len(value) != 2:
        return None
    first, other = _integer(value[0]), _integer(value[1])
    if first is None or other is None or first < 1 or other < 1:
        return None
    return (first, other)


def _load_package(row: object) -> StoredPackage | None:
    """Read one entry off disk, or None where the row is not what was written."""
    if not isinstance(row, dict):
        return None
    ratings = _package(row.get("package_ratings_a"))
    space = _integer(row.get("space"))
    label: object = row.get("label")
    if ratings is None or space is None or not isinstance(label, str):
        return None
    return StoredPackage(ratings, space, label)


def _load_packages(stored: object) -> dict[str, StoredPackage]:
    """Read the entries off disk, dropping any row that is not what was written."""
    rows = stored.get("packages") if isinstance(stored, dict) else None
    if not isinstance(rows, dict):
        return {}
    kept: dict[str, StoredPackage] = {}
    for circuit_id, row in rows.items():
        package = _load_package(row)
        if package is None:
            _LOGGER.warning("Dropping malformed package ratings for %s", circuit_id)
        else:
            kept[str(circuit_id)] = package
    return kept


def _load_change(circuit_id: str, row: object) -> RatingChange | None:
    """Read one removal off disk, or None where the row is not what was written."""
    if not isinstance(row, dict):
        return None
    reason: object = row.get("reason")
    ratings = _package(row.get("package_ratings_a"))
    label: object = row.get("label")
    after_ok, after = _optional_number(row, "panel_after_a")
    service_ok, service = _optional_number(row, "service_a")
    if not is_change_reason(reason) or ratings is None or not isinstance(label, str):
        return None
    if not after_ok or not service_ok:
        return None
    return RatingChange(circuit_id, label, reason, ratings, after, service)


def _load_unacknowledged(stored: object) -> dict[str, RatingChange]:
    """Read the removals the user has not yet seen, dropping any row that is not what was written."""
    rows = stored.get("unacknowledged") if isinstance(stored, dict) else None
    if not isinstance(rows, dict):
        return {}
    kept: dict[str, RatingChange] = {}
    for circuit_id, row in rows.items():
        change = _load_change(str(circuit_id), row)
        if change is None:
            _LOGGER.warning("Dropping a malformed package ratings removal for %s", circuit_id)
        else:
            kept[str(circuit_id)] = change
    return kept


def _figure(value: float | None) -> str:
    """Print a rating as the notice does: 20, not 20.0."""
    return "" if value is None else f"{value:g}"


def _package_text(ratings: PackageRatings) -> str:
    """Print a package as the SPAN app shows it, without its unit: 20-30."""
    return "-".join(str(rating) for rating in ratings)


class PackageRatingManager:
    """Holds one panel's package ratings and tells the user when the rule removes them."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager with nothing stored and no first reconcile run."""
        self._hass = hass
        self._entry = entry
        self._packages: dict[str, StoredPackage] = {}
        # What each entry was at the last reconcile or set, so a change to it,
        # as well as a removal, is announced on the bus.
        self._states: dict[str, JudgedState] = {}
        self._unacknowledged: dict[str, RatingChange] = {}
        self._service: Declaration = "unknown"
        self._begun = False
        self._announce_pending = False
        self._store = _store_for(hass, entry)

    async def async_load(self) -> None:
        """Load the entries and the unacknowledged removals from HA Storage."""
        data = await self._store.async_load()
        self._packages = _load_packages(data)
        self._unacknowledged = _load_unacknowledged(data)

    def resolve(self, circuit_id: str, snapshot: SpanPanelSnapshot) -> MonitoringRating:
        """Return the rating a reader should use for one circuit.

        Judged against the panel's service rating declaration, which is
        "unknown" until `begin`, so before then a circuit with an entry is
        pending and has no judged rating.
        """
        return resolve_rating(
            snapshot.circuits.get(circuit_id),
            self._packages.get(circuit_id),
            service_rating_a(snapshot),
            self._service,
            describes_breakers=panel_describes_breakers(snapshot),
        )

    def diagnostics(self, snapshot: SpanPanelSnapshot) -> PackageRatingsDiagnostics:
        """Every stored entry and every removal the notice still lists.

        Each entry's state comes from `resolve`, the call every reader makes, so
        the download says what monitoring is doing with it. After a reconcile a
        stored entry either applies or waits on a value that has not arrived;
        any other outcome has already removed it. The removals are the notice's
        lines while it is on screen (`_unseen`), and none once it is dismissed.
        """
        return PackageRatingsDiagnostics(
            panel_describes_breakers=panel_describes_breakers(snapshot),
            service_rating_declaration=self._service,
            packages={
                circuit_id: PackageDiagnosticsRow(
                    label=stored.label,
                    package_ratings_a=list(stored.ratings_a),
                    space=stored.space,
                    state="applies" if self.resolve(circuit_id, snapshot).user_set else "pending",
                )
                for circuit_id, stored in self._packages.items()
            },
            unacknowledged={
                circuit_id: _change_row(change) for circuit_id, change in self._unseen().items()
            },
        )

    def _on_screen(self) -> bool:
        """Whether the notice is on screen: standing, or waiting on its text to be raised.

        Standing is the notices module's to say, so this asks it rather than
        tracking dismissals here. A notice still waiting to be raised counts as on
        screen, or two removals in a row would drop the first.
        """
        return self._announce_pending or notices.is_standing(self._hass, self._entry, _NOTICE_ID)

    def _unseen(self) -> dict[str, RatingChange]:
        """Return the removals the notice lists while it is on screen; none once the user has dismissed it."""
        return self._unacknowledged if self._on_screen() else {}

    def begin(
        self,
        snapshot: SpanPanelSnapshot,
        field_metadata: Mapping[str, FieldMetadata] | None,
    ) -> None:
        """Run the first reconcile of a setup, and let later ones run.

        Until this is called `reconcile` does nothing: an entry must not be
        judged against a snapshot that is not yet the panel's complete state.

        The service rating declaration is taken from `field_metadata` here, and
        once it is "declared" or "undeclared" it holds for the session. The
        declaration is static within one: a schema change reloads the entry,
        which starts a new session with a new manager. Taking it again on every
        snapshot would cost a schema walk each time, and would read the None a
        rebuild hands out as "unknown", leaving every entry unrated for that
        snapshot.
        """
        self._begun = True
        self._service = service_rating_declaration(field_metadata)
        self._reconcile(snapshot)

    def reconcile(self, snapshot: SpanPanelSnapshot, field_metadata: FieldMetadataSource) -> None:
        """Apply the rule to every stored entry and announce what it removed.

        `field_metadata` is read only while the service rating declaration is
        still unknown, as when the metadata was not ready at `begin`.
        """
        if not self._begun:
            return
        if self._service == "unknown":
            self._service = service_rating_declaration(field_metadata())
        self._reconcile(snapshot)

    def _reconcile(self, snapshot: SpanPanelSnapshot) -> None:
        """Apply the rule, then tell readers of every entry removed or changed in what it is.

        An entry that goes pending, or applies again, changes what its circuit is
        judged against without anything being removed, so it fires the event and
        raises no notice. An entry first judged here, at `begin`, sets what it is
        without firing: no reader has the topology before setup completes, and
        the runtime data readers go through is set after this.
        """
        result = reconcile_packages(
            self._packages, snapshot, self._service, construct_circuit_label
        )
        stored_changed = result.packages != self._packages
        moved = {
            circuit_id
            for circuit_id, state in result.states.items()
            if self._states.get(circuit_id, state) != state
        }
        self._packages = result.packages
        self._states = dict(result.states)
        if result.changes:
            self._record(result.changes)
        if stored_changed:
            self._save()
        if result.changes or moved:
            self._fire([*(change.circuit_id for change in result.changes), *moved])
        if result.changes:
            self._announce()

    def set_package_ratings(
        self, circuit_id: str, snapshot: SpanPanelSnapshot, ratings_a: Sequence[int]
    ) -> None:
        """Store the ratings a user copied from the SPAN app for one circuit, in the app's order.

        What the rule makes of them is `admit`'s to say: an entry, stored only
        where it applies; a clear, for the panel's rating alone; or a refusal,
        raised as a ServiceValidationError with its own key.
        """
        admission = admit(snapshot, circuit_id, ratings_a, self._service, construct_circuit_label)
        if isinstance(admission, Refused):
            raise _refusal(f"package_ratings_{admission.reason}", **_placeholders(admission))
        if not isinstance(admission, StoredPackage):
            self._remove(circuit_id)
            return
        if self._packages.get(circuit_id) == admission:
            return
        self._packages[circuit_id] = admission
        self._states[circuit_id] = "applies"  # admit stores only what applies
        self._forget(circuit_id)
        self._save()
        self._fire([circuit_id])

    def clear_package_ratings(self, circuit_id: str, snapshot: SpanPanelSnapshot) -> None:
        """Remove a circuit's entry; raises where the circuit is not on the panel."""
        if circuit_id not in snapshot.circuits:
            raise _refusal("package_ratings_circuit_unknown")
        self._remove(circuit_id)

    def _remove(self, circuit_id: str) -> None:
        self._states.pop(circuit_id, None)
        if self._packages.pop(circuit_id, None) is None:
            return
        self._forget(circuit_id)
        self._save()
        self._fire([circuit_id])

    def _forget(self, circuit_id: str) -> None:
        """Drop a circuit the user has just changed from what the notice says.

        The notice reports an entry the rule removed, and the user has since
        copied the package again or cleared it, so its line would be stale. A
        notice left with no line is not dismissed on the user's behalf; one with
        others is raised again without it.
        """
        if self._unacknowledged.pop(circuit_id, None) is None or not self._unacknowledged:
            return
        if self._on_screen():
            self._announce_pending = True
            self._hass.async_create_task(self._async_announce())

    def _record(self, changes: tuple[RatingChange, ...]) -> None:
        """Add removals to those the user has not seen, the latest for each circuit.

        What the user has seen is what was on screen (`_on_screen`): once the
        notice has been dismissed the earlier removals are spent.
        """
        if not self._on_screen():
            self._unacknowledged = {}
        for change in changes:
            self._unacknowledged[change.circuit_id] = change

    def _announce(self) -> None:
        """Raise the notice again as it now stands."""
        self._announce_pending = True
        self._hass.async_create_task(self._async_announce())

    async def _async_announce(self) -> None:
        """Raise the notice listing every unacknowledged removal.

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
            # Emptied by `_forget` while the text was read: the user changed the
            # only circuit listed, so there is nothing left to say.
            return
        lines = [
            "- "
            + text.get(change.reason, "**{circuit}**").format(
                circuit=change.label,
                package=_package_text(change.package_ratings_a),
                before=_figure(change.package_ratings_a[0]),
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
            title=text.get("title") or "SPAN Panel ratings copied from the SPAN app removed",
            message=message,
        )

    def _fire(self, circuit_ids: Iterable[str]) -> None:
        self._hass.bus.async_fire(
            EVENT_PACKAGE_RATINGS_CHANGED,
            {"config_entry_id": self._entry.entry_id, "circuit_ids": sorted(set(circuit_ids))},
        )

    def _save(self) -> None:
        self._hass.async_create_task(self.async_save())

    async def async_save(self) -> None:
        """Persist the entries and the unacknowledged removals to HA Storage."""
        await self._store.async_save(
            StoredPackages(
                packages={
                    circuit_id: _StoredPackageRow(
                        package_ratings_a=list(stored.ratings_a),
                        space=stored.space,
                        label=stored.label,
                    )
                    for circuit_id, stored in self._packages.items()
                },
                unacknowledged={
                    circuit_id: _change_row(change)
                    for circuit_id, change in self._unacknowledged.items()
                },
            )
        )


def _store_for(hass: HomeAssistant, entry: ConfigEntry) -> Store[StoredPackages]:
    """Return the store holding one panel's entries and unacknowledged removals."""
    return Store(hass, _STORAGE_VERSION, f"{_STORAGE_KEY_PREFIX}.{entry.entry_id}")


async def async_forget_package_ratings(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the stored entries when the config entry is removed.

    The keys are wire circuit ids, so a store left behind would be loaded by the
    next config entry added for the same panel, re-applying package ratings the
    user removed the panel to be rid of. The notice they raised goes with the
    entry's other notices.
    """
    await _store_for(hass, entry).async_remove()


def _change_row(change: RatingChange) -> _StoredChangeRow:
    return _StoredChangeRow(
        circuit_id=change.circuit_id,
        label=change.label,
        reason=change.reason,
        package_ratings_a=list(change.package_ratings_a),
        panel_after_a=change.panel_after_a,
        service_a=change.service_a,
    )


def _placeholders(refused: Refused) -> dict[str, str]:
    """Return what a refusal's message names: the panel's rating, or the service rating."""
    if refused.reason == "first_not_panel":
        return {"panel_rating": _figure(refused.panel_a)}
    if refused.reason == "above_service_rating":
        return {"service_rating": _figure(refused.service_a)}
    return {}


def _refusal(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )
