"""Tests for set_global_monitoring service schema."""

import pytest
import voluptuous as vol


class TestSetGlobalMonitoringSchema:
    """Tests for service schema validation."""

    def test_schema_accepts_valid_input(self):
        """Service schema accepts valid global monitoring input."""
        from custom_components.span_panel.__init__ import (
            _build_set_global_monitoring_schema,
        )

        schema = _build_set_global_monitoring_schema()
        result = schema({
            "continuous_threshold_pct": 75,
            "spike_threshold_pct": 95,
            "window_duration_m": 20,
            "cooldown_duration_m": 30,
            "notify_targets": "notify.mobile_app, event_bus",
        })
        assert result["continuous_threshold_pct"] == 75

    def test_schema_accepts_partial_input(self):
        """Service schema accepts partial input (only some fields)."""
        from custom_components.span_panel.__init__ import (
            _build_set_global_monitoring_schema,
        )

        schema = _build_set_global_monitoring_schema()
        result = schema({"continuous_threshold_pct": 70})
        assert result["continuous_threshold_pct"] == 70
        assert "spike_threshold_pct" not in result

    def test_schema_rejects_out_of_range(self):
        """Schema rejects values outside allowed range."""
        from custom_components.span_panel.__init__ import (
            _build_set_global_monitoring_schema,
        )

        schema = _build_set_global_monitoring_schema()
        with pytest.raises(vol.MultipleInvalid):
            schema({"continuous_threshold_pct": 0})

    def test_schema_accepts_empty(self):
        """Schema accepts empty dict (no-op update)."""
        from custom_components.span_panel.__init__ import (
            _build_set_global_monitoring_schema,
        )

        schema = _build_set_global_monitoring_schema()
        result = schema({})
        assert result == {}
