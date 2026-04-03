"""Pure threshold evaluation logic for current monitoring.

Functions receive state as parameters and return results (AlertEvent or None)
instead of dispatching notifications directly. This decouples threshold
evaluation from notification delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
)
from .options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)

if TYPE_CHECKING:
    from .current_monitor import MonitoredPointState


@dataclass
class AlertEvent:
    """Describes an alert to be dispatched."""

    alert_type: str  # "spike" or "continuous_overload"
    current_a: float
    breaker_rating_a: float
    threshold_pct: int
    utilization_pct: float
    window_duration_s: int | None = None
    over_threshold_since: str | None = None


def resolve_thresholds(
    override: dict[str, Any],
    global_settings: dict[str, Any],
) -> tuple[int, int, int, int]:
    """Return (continuous_pct, spike_pct, window_m, cooldown_m) for a monitored point.

    Merges per-point overrides with global settings, falling back to built-in
    defaults when neither layer provides a value.
    """
    return (
        override.get(
            CONTINUOUS_THRESHOLD_PCT,
            global_settings.get(CONTINUOUS_THRESHOLD_PCT, DEFAULT_CONTINUOUS_THRESHOLD_PCT),
        ),
        override.get(
            SPIKE_THRESHOLD_PCT,
            global_settings.get(SPIKE_THRESHOLD_PCT, DEFAULT_SPIKE_THRESHOLD_PCT),
        ),
        override.get(
            WINDOW_DURATION_M,
            global_settings.get(WINDOW_DURATION_M, DEFAULT_WINDOW_DURATION_M),
        ),
        override.get(
            COOLDOWN_DURATION_M,
            global_settings.get(COOLDOWN_DURATION_M, DEFAULT_COOLDOWN_DURATION_M),
        ),
    )


def is_monitoring_disabled(override: dict[str, Any]) -> bool:
    """Check if monitoring is disabled via per-point override."""
    return override.get("monitoring_enabled") is False


def check_spike(
    state: MonitoredPointState,
    current: float,
    rating: float,
    threshold_pct: int,
    cooldown_m: int,
) -> AlertEvent | None:
    """Check for instantaneous spike condition.

    Returns an AlertEvent if the spike threshold is exceeded and the cooldown
    period has elapsed, otherwise None. Updates state.last_spike_alert on alert.
    """
    limit = rating * threshold_pct / 100.0
    if current < limit:
        return None

    now = datetime.now(UTC)
    if state.last_spike_alert is not None and now - state.last_spike_alert < timedelta(
        minutes=cooldown_m
    ):
        return None

    state.last_spike_alert = now
    utilization = round(current / rating * 100, 1)

    return AlertEvent(
        alert_type="spike",
        current_a=current,
        breaker_rating_a=rating,
        threshold_pct=threshold_pct,
        utilization_pct=utilization,
    )


def check_continuous(
    state: MonitoredPointState,
    current: float,
    rating: float,
    threshold_pct: int,
    window_m: int,
    cooldown_m: int,
) -> AlertEvent | None:
    """Check for sustained continuous overload condition.

    Returns an AlertEvent if the continuous threshold has been exceeded for
    longer than the configured window and the cooldown period has elapsed,
    otherwise None. Updates state tracking fields on transitions.
    """
    limit = rating * threshold_pct / 100.0
    now = datetime.now(UTC)

    if current < limit:
        state.over_threshold_since = None
        return None

    if state.over_threshold_since is None:
        state.over_threshold_since = now

    elapsed = now - state.over_threshold_since
    if elapsed < timedelta(minutes=window_m):
        return None

    if state.last_continuous_alert is not None and now - state.last_continuous_alert < timedelta(
        minutes=cooldown_m
    ):
        return None

    state.last_continuous_alert = now
    utilization = round(current / rating * 100, 1)

    return AlertEvent(
        alert_type="continuous_overload",
        current_a=current,
        breaker_rating_a=rating,
        threshold_pct=threshold_pct,
        utilization_pct=utilization,
        window_duration_s=int(elapsed.total_seconds()),
        over_threshold_since=state.over_threshold_since.isoformat(),
    )
