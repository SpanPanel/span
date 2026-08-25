"""Option configurations."""

BATTERY_ENABLE = "enable_battery_percentage"
POWER_DISPLAY_PRECISION = "power_display_precision"
ENERGY_DISPLAY_PRECISION = "energy_display_precision"
ENERGY_REPORTING_GRACE_PERIOD = "energy_reporting_grace_period"
SNAPSHOT_UPDATE_INTERVAL = "snapshot_update_interval"

CONTINUOUS_THRESHOLD_PCT = "continuous_threshold_pct"
SPIKE_THRESHOLD_PCT = "spike_threshold_pct"
WINDOW_DURATION_M = "window_duration_m"
COOLDOWN_DURATION_M = "cooldown_duration_m"
NOTIFY_TARGETS = "notify_targets"
NOTIFICATION_TITLE_TEMPLATE = "notification_title_template"
NOTIFICATION_MESSAGE_TEMPLATE = "notification_message_template"
NOTIFICATION_PRIORITY = "notification_priority"

# Control authorization and flap protection. See `control_gate`.
CONTROL_MODE = "control_mode"
ALLOW_CONTEXTLESS_CONTROL = "allow_contextless_control"
CONTROL_LOCK_TIMEOUT = "control_lock_timeout"
RELAY_DEBOUNCE_SECONDS = "relay_debounce_seconds"
