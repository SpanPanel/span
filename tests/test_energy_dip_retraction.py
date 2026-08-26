"""A counter reset is permanent, so a dip the next reading disproves is retracted.

Dip compensation books an offset the moment a counter reads lower than it did,
and that write is irreversible: it lands in Home Assistant's long-term
statistics. The evidence for it is one sample. `SpanPanel/span#259` is what that
costs when the sample is wrong — a transient zero booked the whole lifetime
counter as an offset on every restart, reaching megawatt-hours.

The library no longer fabricates that particular zero (an unreported reading is
`None`, which skips compensation entirely). This is the layer under it: whatever
the source, a reading that drops and then comes back was never a reset, because
a reset is permanent. So the offset is provisional until a later reading either
disproves it — the counter returns to where it was — or corroborates it, by
counting up from the lower base.

**Corroboration needs independent progress, not merely a second low reading.**
Confirming on any reading below the baseline would confirm on a second copy of
the same bad sample, which is exactly the shape a replay produces: snapshots are
dispatched on a debounce, so one burst can deliver the same incomplete state
twice. That is the failure this rule exists to catch, so it must not be the
failure that satisfies it.

**And corroboration is evidence rather than proof.** The same burst that
replays one stale sample can replay two, the second slightly higher than the
first, which is indistinguishable on the tick from a counter climbing away from
a fresh base. So confirming does not close the record: for
`CONFIRMED_RETRACTION_WINDOW` more readings a return to the baseline still
retracts everything. Once the window is spent the record is dropped and the
offset is final, because a compensation that could be undone at any future
reading would be undone by the ordinary growth of a counter that really did
reset.

The cost is that a genuine reset on a circuit drawing nothing stays unconfirmed
until it next moves. Compensation is applied throughout — only the notification
waits — so the sensor reads correctly while the evidence is outstanding.
"""

from __future__ import annotations

import pytest

from custom_components.span_panel.energy_dip import (
    CONFIRMED_RETRACTION_WINDOW,
    DipEvent,
    DipOutcome,
    PendingDip,
    process_energy_dip,
)

BASELINE = 163562.4
"""A plausible lifetime counter, from the report in #259."""


def _book(raw: float = 0.0, offset: float = 0.0) -> tuple[float, PendingDip]:
    """Run the tick that books a dip, returning the new offset and pending record."""
    outcome = process_energy_dip(raw, BASELINE, offset, None)

    assert outcome.event is DipEvent.BOOKED
    assert outcome.pending is not None
    return outcome.offset, outcome.pending


def _feed(readings: list[float], *, after: float) -> list[DipOutcome]:
    """Run a run of readings through the rule from a standing start.

    `after` is what the counter read before the first of them, so the caller
    writes the panel's sequence and gets one outcome per reading back.
    """
    outcomes: list[DipOutcome] = []
    previous = after
    offset = 0.0
    pending: PendingDip | None = None

    for raw in readings:
        outcome = process_energy_dip(raw, previous, offset, pending)
        outcomes.append(outcome)
        offset, pending, previous = outcome.offset, outcome.pending, raw

    return outcomes


class TestBooking:
    """The tick on which the counter first reads low."""

    def test_a_drop_books_an_offset_and_leaves_it_unconfirmed(self) -> None:
        """Compensated at once, believed later."""
        outcome = process_energy_dip(0.0, BASELINE, 0.0, None)

        assert outcome.event is DipEvent.BOOKED
        assert outcome.offset == pytest.approx(BASELINE)
        assert outcome.pending == PendingDip(baseline=BASELINE, delta=BASELINE)

    def test_the_compensated_value_does_not_move_on_the_booking_tick(self) -> None:
        """The dip tick is invisible downstream.

        Which is what makes the spike arrive one sample later and look like real
        consumption.
        """
        outcome = process_energy_dip(0.0, BASELINE, 0.0, None)

        assert outcome.compensated == pytest.approx(BASELINE)

    def test_a_drop_below_the_threshold_books_nothing(self) -> None:
        """Noise is not a dip."""
        outcome = process_energy_dip(BASELINE - 0.5, BASELINE, 0.0, None)

        assert outcome.event is None
        assert outcome.pending is None
        assert outcome.offset == 0.0

    def test_the_first_reading_of_all_books_nothing(self) -> None:
        """Nothing to compare a first reading against."""
        outcome = process_energy_dip(BASELINE, None, 0.0, None)

        assert outcome.event is None
        assert outcome.pending is None


class TestRetraction:
    """The counter comes back, so no reset happened."""

    def test_a_reading_back_at_the_baseline_retracts_the_offset(self) -> None:
        """The counter is where it started, so it never reset."""
        offset, pending = _book()

        outcome = process_energy_dip(BASELINE, 0.0, offset, pending)

        assert outcome.event is DipEvent.RETRACTED
        assert outcome.offset == 0.0
        assert outcome.pending is None

    def test_a_reading_above_the_baseline_retracts_too(self) -> None:
        """The ordinary case: the panel kept counting while the reading was lost."""
        offset, pending = _book()

        outcome = process_energy_dip(BASELINE + 7.6, 0.0, offset, pending)

        assert outcome.event is DipEvent.RETRACTED
        assert outcome.offset == 0.0

    def test_the_compensated_value_returns_to_the_uninflated_reading(self) -> None:
        """The whole point: no step.

        #259 saw the counter's full value added to the sensor in a single sample.
        """
        offset, pending = _book()

        outcome = process_energy_dip(BASELINE + 7.6, 0.0, offset, pending)

        assert outcome.compensated == pytest.approx(BASELINE + 7.6)

    def test_an_earlier_offset_survives_the_retraction(self) -> None:
        """Retraction removes the provisional amount, not the whole offset.

        A real reset compensated last month is not undone by this month's
        artefact.
        """
        offset, pending = _book(offset=500.0)

        outcome = process_energy_dip(BASELINE, 0.0, offset, pending)

        assert outcome.offset == pytest.approx(500.0)

    def test_a_reading_just_under_the_baseline_still_retracts(self) -> None:
        """Symmetric with the booking threshold.

        A difference under 1.0 Wh is not a difference.
        """
        offset, pending = _book()

        outcome = process_energy_dip(BASELINE - 0.5, 0.0, offset, pending)

        assert outcome.event is DipEvent.RETRACTED


class TestConfirmation:
    """The counter counts up from the lower base, so the reset was real."""

    def test_progress_from_the_dipped_reading_confirms(self) -> None:
        """Counting up from the lower base is what a reset looks like."""
        offset, pending = _book()

        outcome = process_energy_dip(5.0, 0.0, offset, pending)

        assert outcome.event is DipEvent.CONFIRMED
        assert outcome.delta == pytest.approx(BASELINE)
        assert outcome.offset == pytest.approx(BASELINE)
        # Believed, but kept: see `TestConfirmThenReturn`.
        assert outcome.pending == PendingDip(
            baseline=BASELINE, delta=BASELINE, confirmed_ticks_left=CONFIRMED_RETRACTION_WINDOW
        )

    def test_the_offset_stays_applied_through_confirmation(self) -> None:
        """Confirming settles the belief, it does not change the arithmetic."""
        offset, pending = _book()

        outcome = process_energy_dip(5.0, 0.0, offset, pending)

        assert outcome.compensated == pytest.approx(BASELINE + 5.0)


class TestHolding:
    """No evidence either way, so the decision waits."""

    def test_a_repeat_of_the_dipped_reading_does_not_confirm(self) -> None:
        """The reason a single-tick confirmation is unsafe.

        Snapshots are dispatched on a debounce, so one replay can deliver the
        same incomplete state more than once. A rule that took any low reading
        as corroboration would be satisfied by the second copy of the very
        sample it exists to catch.
        """
        offset, pending = _book()

        outcome = process_energy_dip(0.0, 0.0, offset, pending)

        assert outcome.event is None
        assert outcome.pending == pending
        assert outcome.offset == pytest.approx(BASELINE)

    def test_holding_keeps_the_sensor_compensated(self) -> None:
        """Waiting for evidence must not make the reading wrong meanwhile."""
        offset, pending = _book()

        outcome = process_energy_dip(0.0, 0.0, offset, pending)

        assert outcome.compensated == pytest.approx(BASELINE)

    def test_a_second_phantom_zero_then_recovery_still_retracts(self) -> None:
        """The #259 sequence end to end, with the burst delivering twice."""
        offset, pending = _book()

        held = process_energy_dip(0.0, 0.0, offset, pending)
        recovered = process_energy_dip(BASELINE + 7.6, 0.0, held.offset, held.pending)

        assert recovered.event is DipEvent.RETRACTED
        assert recovered.offset == 0.0
        assert recovered.compensated == pytest.approx(BASELINE + 7.6)


class TestConfirmThenReturn:
    """A dip corroborated by one tick of progress, and then undone.

    The confirming reading is evidence, not proof: a replay burst can deliver a
    low sample and then a slightly higher low sample before the real reading
    arrives. If confirmation closed the record for good, that shape would leave
    the counter's whole lifetime total in the offset permanently.
    """

    def test_a_return_to_the_baseline_after_confirmation_still_retracts(self) -> None:
        """The review's sequence: 1000 -> 0 (booked) -> 5 (confirmed) -> 1001."""
        offset, pending = _book()

        confirmed = process_energy_dip(5.0, 0.0, offset, pending)
        returned = process_energy_dip(BASELINE + 1.0, 5.0, confirmed.offset, confirmed.pending)

        assert returned.event is DipEvent.RETRACTED
        assert returned.offset == pytest.approx(0.0)
        assert returned.compensated == pytest.approx(BASELINE + 1.0)

    def test_the_last_reading_of_the_window_can_still_retract(self) -> None:
        """The window is three readings, and the third of them is one of them."""
        outcomes = _feed([0.0, 5.0, 6.0, 7.0, BASELINE + 1.0], after=BASELINE)

        assert [outcome.event for outcome in outcomes[1:]] == [
            DipEvent.CONFIRMED,
            None,
            None,
            DipEvent.RETRACTED,
        ]
        assert outcomes[-1].offset == pytest.approx(0.0)
        assert outcomes[-1].compensated == pytest.approx(BASELINE + 1.0)

    def test_the_window_runs_out_and_the_offset_becomes_final(self) -> None:
        """A fourth reading is past it, so a return then is just consumption.

        Which is the cost of the window: a counter that genuinely reset and then
        climbed its whole lifetime total back within three readings would be
        mis-retracted. It is the offset staying put that matters here.
        """
        outcomes = _feed([0.0, 5.0, 6.0, 7.0, 8.0, BASELINE + 1.0], after=BASELINE)

        assert outcomes[-2].pending is None
        assert outcomes[-1].event is None
        assert outcomes[-1].offset == pytest.approx(BASELINE)
        assert outcomes[-1].compensated == pytest.approx(2 * BASELINE + 1.0)

    def test_a_genuine_reset_that_keeps_counting_drops_its_record(self) -> None:
        """Nothing is provisional forever: the record is not carried indefinitely."""
        outcomes = _feed([0.0, 5.0, 6.0, 7.0, 8.0], after=BASELINE)

        assert outcomes[1].pending is not None
        assert outcomes[1].pending.confirmed_ticks_left == CONFIRMED_RETRACTION_WINDOW
        assert [outcome.pending is None for outcome in outcomes[2:]] == [False, False, True]

    def test_a_further_fall_inside_the_window_accumulates_and_still_retracts(self) -> None:
        """A burst can deliver more than one stale sample, in any order.

        The second fall extends the provisional amount against the original
        baseline, so the return gives all of it back at once.
        """
        outcomes = _feed([0.0, 5.0, 2.0, BASELINE + 1.0], after=BASELINE)

        assert outcomes[2].event is DipEvent.BOOKED
        assert outcomes[2].offset == pytest.approx(BASELINE + 3.0)
        assert outcomes[-1].event is DipEvent.RETRACTED
        assert outcomes[-1].delta == pytest.approx(BASELINE + 3.0)
        assert outcomes[-1].offset == pytest.approx(0.0)

    def test_a_fall_inside_the_window_does_not_reopen_it(self) -> None:
        """A fall is not a return, so it spends a reading like any other."""
        outcomes = _feed([0.0, 5.0, 2.0, 3.0, 4.0, BASELINE + 1.0], after=BASELINE)

        assert outcomes[-2].pending is None
        assert outcomes[-1].event is None
        assert outcomes[-1].offset == pytest.approx(BASELINE + 3.0)


class TestASecondDropWhileUnconfirmed:
    """A counter that keeps falling before anything is settled."""

    def test_the_provisional_amount_accumulates(self) -> None:
        """A second fall extends the amount that is still provisional."""
        offset, pending = _book(raw=100.0)

        outcome = process_energy_dip(20.0, 100.0, offset, pending)

        # Two falls, BASELINE -> 100 and 100 -> 20, so the counter is short by
        # everything above its current reading.
        assert outcome.event is DipEvent.BOOKED
        assert outcome.offset == pytest.approx(BASELINE - 20.0)
        assert outcome.pending is not None
        assert outcome.pending.delta == pytest.approx(BASELINE - 20.0)

    def test_the_baseline_stays_the_high_water_mark(self) -> None:
        """Retraction restores what the counter read before any of it.

        So a later, lower reading must not move the mark it is judged against.
        """
        offset, pending = _book(raw=100.0)
        second = process_energy_dip(20.0, 100.0, offset, pending)

        assert second.pending is not None
        assert second.pending.baseline == pytest.approx(BASELINE)

        recovered = process_energy_dip(BASELINE, 20.0, second.offset, second.pending)

        assert recovered.event is DipEvent.RETRACTED
        assert recovered.offset == pytest.approx(0.0)
