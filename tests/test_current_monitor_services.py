"""Tests for current monitoring service registration and handling."""

import pytest
import voluptuous as vol


class TestServiceRegistration:
    """Tests for monitoring service registration."""

    def test_set_circuit_threshold_service_schema_validates(self):
        """Service schema accepts valid circuit threshold input."""
        from custom_components.span_panel.__init__ import (
            _build_set_circuit_threshold_schema,
        )

        schema = _build_set_circuit_threshold_schema()
        result = schema({"circuit_id": "sensor.span_panel_kitchen_power", "spike_threshold_pct": 90})
        assert result["circuit_id"] == "sensor.span_panel_kitchen_power"
        assert result["spike_threshold_pct"] == 90

    def test_set_circuit_threshold_schema_rejects_missing_circuit_id(self):
        """Service schema rejects input without circuit_id."""
        from custom_components.span_panel.__init__ import (
            _build_set_circuit_threshold_schema,
        )

        schema = _build_set_circuit_threshold_schema()
        with pytest.raises(vol.MultipleInvalid):
            schema({"spike_threshold_pct": 90})

    def test_set_mains_threshold_service_schema_validates(self):
        """Service schema accepts valid mains threshold input."""
        from custom_components.span_panel.__init__ import (
            _build_set_mains_threshold_schema,
        )

        schema = _build_set_mains_threshold_schema()
        result = schema({"leg": "sensor.span_panel_upstream_l1_current", "spike_threshold_pct": 90})
        assert result["leg"] == "sensor.span_panel_upstream_l1_current"

    def test_set_mains_threshold_schema_accepts_legacy_leg_name(self):
        """Service schema accepts legacy leg name for backwards compatibility."""
        from custom_components.span_panel.__init__ import (
            _build_set_mains_threshold_schema,
        )

        schema = _build_set_mains_threshold_schema()
        result = schema({"leg": "upstream_l1", "spike_threshold_pct": 90})
        assert result["leg"] == "upstream_l1"
