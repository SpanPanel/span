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


class PendingDip(NamedTuple):
    """A booked but unsettled dip.

    `baseline` is what the counter read before any of it — the high-water mark
    a recovery is judged against, which does not move if the counter falls
    again. `delta` is how much has been added to the offset provisionally, and
    is what gets removed if the dip turns out not to have happened.
    """

    baseline: float
    delta: float


class DipEvent(StrEnum):
    """What a reading did to the pending state, when it did anything."""

    BOOKED = "booked"
    """A drop was seen and compensated. Not yet believed."""

    CONFIRMED = "confirmed"
    """The counter counted up from the lower base, so the reset was real."""

    RETRACTED = "retracted"
    """The counter came back, so no reset happened and the offset is removed."""


class DipOutcome(NamedTuple):
    """The result of feeding one raw reading through the rule."""

    offset: float
    pending: PendingDip | None
    compensated: float
    event: DipEvent | None
    delta: float | None
    """The amount the event concerns, or None when nothing happened."""


def process_energy_dip(
    raw_value: float,
    last_panel_reading: float | None,
    current_offset: float,
    pending: PendingDip | None,
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
      confirms it: the counter is demonstrably counting up from the lower base,
      which only a reset produces.
    - **A repeat of the dipped reading** settles nothing and is held.

    That last case is why confirmation needs *progress* rather than just a
    second low reading. Snapshots are dispatched on a debounce, so one replay
    can deliver the same incomplete state twice; a rule that accepted any low
    reading as corroboration would be satisfied by a second copy of the very
    sample it exists to reject.

    A counter that falls again before anything is settled accumulates into the
    same pending record and keeps the original baseline, because a recovery has
    to be judged against what the counter read before any of the fall.

    The cost is that a genuine reset on a circuit drawing nothing stays
    unconfirmed until it next moves, deferring only the notification —
    compensation is applied throughout, so the sensor reads correctly while the
    evidence is outstanding.

    Args:
        raw_value: The current raw energy reading from the panel.
        last_panel_reading: The previous raw reading, or None if this is the first.
        current_offset: The cumulative compensation offset so far.
        pending: An unsettled dip from an earlier reading, if there is one.

    Returns:
        A `DipOutcome`. `event` names what happened, if anything; `delta` is the
        amount it concerns; `pending` is the state to carry into the next call.

    """
    if last_panel_reading is not None and last_panel_reading - raw_value >= DIP_THRESHOLD_WH:
        dip = last_panel_reading - raw_value
        offset = current_offset + dip
        # The baseline is the high-water mark, so a second fall extends the
        # provisional amount without lowering the mark it will be judged against.
        booked = (
            PendingDip(baseline=last_panel_reading, delta=dip)
            if pending is None
            else PendingDip(baseline=pending.baseline, delta=pending.delta + dip)
        )
        return DipOutcome(offset, booked, raw_value + offset, DipEvent.BOOKED, dip)

    if pending is not None:
        if raw_value >= pending.baseline - DIP_THRESHOLD_WH:
            offset = current_offset - pending.delta
            return DipOutcome(offset, None, raw_value + offset, DipEvent.RETRACTED, pending.delta)

        if last_panel_reading is not None and raw_value > last_panel_reading:
            return DipOutcome(
                current_offset,
                None,
                raw_value + current_offset,
                DipEvent.CONFIRMED,
                pending.delta,
            )

    return DipOutcome(current_offset, pending, raw_value + current_offset, None, None)


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
