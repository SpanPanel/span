"""Energy dip detection and compensation logic for Span Panel energy sensors.

Pure functions for detecting energy value dips (typically caused by panel firmware
resets) and computing the compensated value to maintain monotonic TOTAL_INCREASING
sensor readings.

A dip is compensated as soon as it is seen, because the sensor must stay correct
in the meantime, but the *conclusion* that a reset happened is held open until a
later reading settles it. That is the difference between this and a plain
"lower reading means reset" rule, and the reason is that the write is
irreversible: the offset lands in Home Assistant's long-term statistics, where
undoing it is a manual statistics adjustment per affected hour.

Corroboration is evidence rather than proof, so it is not the end of the record
either. A burst of stale samples can deliver a low reading and then a slightly
higher low reading before the true value arrives, which looks exactly like a
counter starting to climb from a fresh base. A corroborated dip is therefore
kept for `CONFIRMED_RETRACTION_WINDOW` further readings, during which a return
to its baseline still retracts it. When the window closes the dip *settles*:
only then is the offset final, and only then is the user told a reset happened.

The two records are kept apart. A dip that occurs inside another dip's window is
its own dip, with its own baseline — the reading it fell from, not the older
high-water mark — because that is what it has to return to in order to be
undone. Folding them together would leave a small artefact judged against a
baseline it can never reach, and so compensated for good.

See `process_energy_dip` for the rule and `tests/test_energy_dip_retraction.py`
for the sequences it is written against.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, NamedTuple

DIP_THRESHOLD_WH = 1.0
"""How far a counter must fall before the drop counts as a dip.

Also how far it must return before the drop counts as undone, so the two
decisions are made against the same tolerance rather than one being exact.
"""


CONFIRMED_RETRACTION_WINDOW = 3
"""How many readings a corroborated dip stays retractable for.

Three is the shortest window that covers the failure it exists for: a debounced
snapshot burst replaying two or three stale samples before the true reading
lands. What it costs is a genuine reset that, within its next three readings,
climbs the whole way back to the counter's old lifetime total — which would mean
a circuit consuming its entire recorded history in a few polling intervals, so
the exchange is a physically implausible mis-retraction for a plausible
mis-confirmation. Longer would be safer against replays and costlier here; the
bound exists so the offset does become final rather than staying open forever.
"""


class PendingDip(NamedTuple):
    """A booked dip that is still, in some degree, provisional.

    `baseline` is what the counter read before it — the mark a recovery is
    judged against, which does not move if the counter falls again. `delta` is
    how much has been added to the offset provisionally, and is what gets
    removed if the dip turns out not to have happened.

    `confirmed_ticks_left` is `None` for an unsettled dip, which waits
    open-endedly for evidence either way. On a corroborated dip it holds how
    many further readings may still retract it; when that runs out the dip
    settles and its offset is final.
    """

    baseline: float
    delta: float
    confirmed_ticks_left: int | None = None


def _count_down(record: PendingDip | None) -> PendingDip | None:
    """Spend one reading of a corroborated record's retraction window.

    Nothing, and an unsettled record, come back as they are — neither has a
    window to spend. A corroborated record whose window runs out here is
    dropped, which is what settling is.
    """
    if record is None or record.confirmed_ticks_left is None:
        return record

    remaining = record.confirmed_ticks_left - 1
    return record._replace(confirmed_ticks_left=remaining) if remaining > 0 else None


def _settled_by(before: PendingDip | None, after: PendingDip | None) -> float | None:
    """Return what a record made final by running out of window, if it did."""
    return before.delta if before is not None and after is None else None


def _has_returned(raw_value: float, record: PendingDip) -> bool:
    """Whether a reading is back at a record's own baseline, within tolerance."""
    return raw_value >= record.baseline - DIP_THRESHOLD_WH


class DipEvent(StrEnum):
    """What a reading did to the provisional state, when it did anything."""

    BOOKED = "booked"
    """A drop was seen and compensated. Not yet believed."""

    CONFIRMED = "confirmed"
    """The counter counted up from the lower base, so the reset is believed.

    Believed, not closed: the dip stays retractable for
    `CONFIRMED_RETRACTION_WINDOW` more readings, and is reported when it
    settles rather than here.
    """

    RETRACTED = "retracted"
    """The counter came back, so no reset happened and the offset is removed."""

    SETTLED = "settled"
    """A corroborated dip ran out of window, so its offset is now final.

    This is the point at which the reset is worth telling the user about,
    because it is the first point at which nothing can take it back.
    """


class DipOutcome(NamedTuple):
    """The result of feeding one raw reading through the rule."""

    offset: float
    pending: PendingDip | None
    compensated: float
    event: DipEvent | None
    delta: float | None
    """The amount the event concerns, or None when nothing happened."""

    recently_confirmed: PendingDip | None = None
    """A corroborated dip still inside its retraction window, if there is one."""

    settled: float | None = None
    """What settled on this reading, if anything.

    Carried separately from `event` because a dip can settle on the same
    reading that books or retracts another one, and the caller must report it
    either way.
    """


def _book(
    raw_value: float,
    last_panel_reading: float,
    current_offset: float,
    pending: PendingDip | None,
    recently_confirmed: PendingDip | None,
) -> DipOutcome:
    """Compensate a fall, and age whatever was already provisional.

    A fall cannot also be a return: every reading since either record was booked
    has been below that record's baseline, and this one is below the last of
    them. So a corroborated dip only ages here, and the fall is a dip in its own
    right — booked against the reading it fell from, or accumulated into an
    unsettled dip that is still waiting on the same question.
    """
    dip = last_panel_reading - raw_value
    offset = current_offset + dip
    booked = (
        PendingDip(baseline=last_panel_reading, delta=dip)
        if pending is None
        else PendingDip(baseline=pending.baseline, delta=pending.delta + dip)
    )
    aged = _count_down(recently_confirmed)
    return DipOutcome(
        offset,
        booked,
        raw_value + offset,
        DipEvent.BOOKED,
        dip,
        aged,
        _settled_by(recently_confirmed, aged),
    )


def process_energy_dip(
    raw_value: float,
    last_panel_reading: float | None,
    current_offset: float,
    pending: PendingDip | None,
    recently_confirmed: PendingDip | None = None,
) -> DipOutcome:
    """Advance dip compensation by one reading.

    A firmware counter reset is permanent: the counter restarts low and counts
    up from there. So a reading that drops and then returns to where it was is
    not a reset — it is a transport artefact, a stale sample, or a reading the
    panel never actually made. Both look identical on the tick they arrive, and
    that single sample used to be enough to add a circuit's whole lifetime
    counter to its offset for good (`SpanPanel/span#259`).

    The rule keeps the compensation and defers the belief:

    - **A drop of at least `DIP_THRESHOLD_WH`** is compensated immediately and
      recorded as pending. The compensated value does not move on this tick,
      which is why the spike shows up one sample later.
    - **A later reading back at the baseline** (within the same tolerance)
      retracts it: the counter never reset, so the offset is removed.
    - **A later reading above the dipped one but still below the baseline**
      corroborates it: the counter is demonstrably counting up from the lower
      base, which only a reset produces.
    - **A repeat of the dipped reading** settles nothing and is held.
    - **A return to the baseline in the `CONFIRMED_RETRACTION_WINDOW` readings
      after that** retracts it after all, because a replayed burst can produce
      corroborating progress out of two stale samples.
    - **The reading that closes the window** settles the dip. The offset is
      final and the reset is reported.

    That fourth case is why corroboration needs *progress* rather than just a
    second low reading. Snapshots are dispatched on a debounce, so one replay
    can deliver the same incomplete state twice; a rule that accepted any low
    reading as corroboration would be satisfied by a second copy of the very
    sample it exists to reject. The window exists because the same burst can
    just as easily deliver two *different* stale samples, the later one higher,
    which is corroboration by coincidence.

    A counter that falls again while a dip is unsettled accumulates into it and
    keeps that dip's baseline, because a recovery has to be judged against what
    the counter read before any of the fall. A fall inside a *corroborated*
    dip's window is a separate dip instead, judged against the reading it fell
    from: the two are undone by different readings, and merging them would leave
    a small artefact needing the counter's whole lifetime total back before it
    could ever be retracted. The older dip ages either way — a fall is not a
    return, so it does not buy a window more readings — and if an unsettled dip
    is corroborated while an older one is still open, the older one settles then
    and there rather than merging, so the bound stays three readings from the
    most recent corroboration.

    The cost is that a genuine reset on a circuit drawing nothing stays
    unsettled until it next moves, deferring only the notification —
    compensation is applied throughout, so the sensor reads correctly while the
    evidence is outstanding.

    Args:
        raw_value: The current raw energy reading from the panel.
        last_panel_reading: The previous raw reading, or None if this is the first.
        current_offset: The cumulative compensation offset so far.
        pending: An unsettled dip from an earlier reading, if there is one.
        recently_confirmed: A corroborated dip still inside its retraction
            window, if there is one.

    Returns:
        A `DipOutcome`. `event` names what happened, if anything; `delta` is the
        amount it concerns; `settled` is what became final on this reading;
        `pending` and `recently_confirmed` are the state to carry into the next
        call.

    """
    if last_panel_reading is not None and last_panel_reading - raw_value >= DIP_THRESHOLD_WH:
        return _book(raw_value, last_panel_reading, current_offset, pending, recently_confirmed)

    confirmed = recently_confirmed
    settled: float | None = None
    removed = 0.0

    if confirmed is not None:
        if _has_returned(raw_value, confirmed):
            removed = confirmed.delta
            confirmed = None
        else:
            aged = _count_down(confirmed)
            settled = _settled_by(confirmed, aged)
            confirmed = aged

    if pending is not None and _has_returned(raw_value, pending):
        # A corroborated dip's baseline is the higher of the two, so a reading
        # that reached it has reached this one as well: both come off together.
        removed += pending.delta
        pending = None

    if removed:
        offset = current_offset - removed
        return DipOutcome(
            offset, pending, raw_value + offset, DipEvent.RETRACTED, removed, confirmed, settled
        )

    if pending is not None and last_panel_reading is not None and raw_value > last_panel_reading:
        return DipOutcome(
            current_offset,
            None,
            raw_value + current_offset,
            DipEvent.CONFIRMED,
            pending.delta,
            PendingDip(
                baseline=pending.baseline,
                delta=pending.delta,
                confirmed_ticks_left=CONFIRMED_RETRACTION_WINDOW,
            ),
            # An older corroborated dip is superseded rather than merged, so it
            # is final as of now.
            confirmed.delta if confirmed is not None else settled,
        )

    return DipOutcome(
        current_offset,
        pending,
        raw_value + current_offset,
        DipEvent.SETTLED if settled is not None else None,
        settled,
        confirmed,
        settled,
    )


def build_dip_attributes(
    energy_offset: float,
    last_dip_delta: float | None,
    is_total_increasing: bool,
    dip_enabled: bool,
) -> dict[str, Any]:
    """Build extra_state_attributes dict for energy dip compensation diagnostics.

    Returns an empty dict when dip compensation is disabled or the sensor is not
    TOTAL_INCREASING.

    Args:
        energy_offset: Cumulative dip compensation offset.
        last_dip_delta: Size of the most recent dip, or None if none observed.
        is_total_increasing: Whether the sensor uses TOTAL_INCREASING state class.
        dip_enabled: Whether energy dip compensation is enabled in options.

    Returns:
        A dict of attribute key/value pairs (may be empty).

    """
    if not dip_enabled or not is_total_increasing:
        return {}

    attrs: dict[str, Any] = {}
    if energy_offset > 0:
        attrs["energy_offset"] = round(energy_offset, 1)
    if last_dip_delta is not None:
        attrs["last_dip_delta"] = round(last_dip_delta, 1)
    return attrs
