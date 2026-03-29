"""Tests for current monitoring options flow integration."""

from unittest.mock import MagicMock

from custom_components.span_panel.config_flow_options import (
    build_general_options_schema,
    get_general_options_defaults,
)
from custom_components.span_panel.const import (
    DEFAULT_CONTINUOUS_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_DURATION_M,
    DEFAULT_SPIKE_THRESHOLD_PCT,
    DEFAULT_WINDOW_DURATION_M,
    ENABLE_CURRENT_MONITORING,
)
from custom_components.span_panel.options import (
    CONTINUOUS_THRESHOLD_PCT,
    COOLDOWN_DURATION_M,
    ENABLE_EVENT_BUS,
    ENABLE_PERSISTENT_NOTIFICATIONS,
    NOTIFY_TARGETS,
    SPIKE_THRESHOLD_PCT,
    WINDOW_DURATION_M,
)


def _make_entry(options=None):
    entry = MagicMock()
    entry.options = options or {}
    return entry


class TestMonitoringOptionsSchema:
    """Tests that monitoring fields appear in the options schema."""

    def test_schema_includes_monitoring_toggle(self):
        """Options schema includes enable_current_monitoring."""
        entry = _make_entry()
        schema = build_general_options_schema(entry)
        keys = [str(k) for k in schema.schema]
        assert ENABLE_CURRENT_MONITORING in keys

    def test_schema_includes_threshold_fields(self):
        """Options schema includes threshold configuration fields."""
        entry = _make_entry()
        schema = build_general_options_schema(entry)
        keys = [str(k) for k in schema.schema]
        assert CONTINUOUS_THRESHOLD_PCT in keys
        assert SPIKE_THRESHOLD_PCT in keys
        assert WINDOW_DURATION_M in keys
        assert COOLDOWN_DURATION_M in keys

    def test_schema_includes_notification_toggles(self):
        """Options schema includes notification channel toggles."""
        entry = _make_entry()
        schema = build_general_options_schema(entry)
        keys = [str(k) for k in schema.schema]
        assert ENABLE_PERSISTENT_NOTIFICATIONS in keys
        assert ENABLE_EVENT_BUS in keys


class TestMonitoringOptionsDefaults:
    """Tests that monitoring defaults are correct."""

    def test_defaults_monitoring_disabled(self):
        """Monitoring is disabled by default."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[ENABLE_CURRENT_MONITORING] is False

    def test_defaults_threshold_values(self):
        """Threshold defaults match NEC standards."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[CONTINUOUS_THRESHOLD_PCT] == DEFAULT_CONTINUOUS_THRESHOLD_PCT
        assert defaults[SPIKE_THRESHOLD_PCT] == DEFAULT_SPIKE_THRESHOLD_PCT
        assert defaults[WINDOW_DURATION_M] == DEFAULT_WINDOW_DURATION_M
        assert defaults[COOLDOWN_DURATION_M] == DEFAULT_COOLDOWN_DURATION_M

    def test_defaults_notification_channels_enabled(self):
        """Notification channels enabled by default."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[ENABLE_PERSISTENT_NOTIFICATIONS] is True
        assert defaults[ENABLE_EVENT_BUS] is True

    def test_existing_options_preserved(self):
        """Stored options override defaults."""
        entry = _make_entry({
            ENABLE_CURRENT_MONITORING: True,
            CONTINUOUS_THRESHOLD_PCT: 70,
        })
        defaults = get_general_options_defaults(entry)
        assert defaults[ENABLE_CURRENT_MONITORING] is True
        assert defaults[CONTINUOUS_THRESHOLD_PCT] == 70


class TestNotifyTargetsOptions:
    """Tests for notify targets configuration."""

    def test_defaults_include_notify_targets(self):
        """Default notify targets is notify.notify."""
        entry = _make_entry()
        defaults = get_general_options_defaults(entry)
        assert defaults[NOTIFY_TARGETS] == "notify.notify"

    def test_stored_notify_targets_preserved(self):
        """Stored notify targets override defaults."""
        entry = _make_entry({
            NOTIFY_TARGETS: "notify.mobile_app_phone",
        })
        defaults = get_general_options_defaults(entry)
        assert defaults[NOTIFY_TARGETS] == "notify.mobile_app_phone"
