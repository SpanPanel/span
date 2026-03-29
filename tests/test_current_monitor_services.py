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
        result = schema({"circuit_id": "1", "spike_threshold_pct": 90})
        assert result["circuit_id"] == "1"
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
        result = schema({"leg": "upstream_l1", "spike_threshold_pct": 90})
        assert result["leg"] == "upstream_l1"

    def test_set_mains_threshold_schema_rejects_invalid_leg(self):
        """Service schema rejects invalid mains leg identifier."""
        from custom_components.span_panel.__init__ import (
            _build_set_mains_threshold_schema,
        )

        schema = _build_set_mains_threshold_schema()
        with pytest.raises(vol.MultipleInvalid):
            schema({"leg": "invalid_leg", "spike_threshold_pct": 90})
