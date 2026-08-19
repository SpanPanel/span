"""Every entity that reads one snapshot field must say which field."""

from __future__ import annotations

from custom_components.span_panel.field_paths import declared_field_paths
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS


def test_circuit_power_declares_its_field_path() -> None:
    power = next(d for d in CIRCUIT_SENSORS if d.key == "circuit_power")
    assert power.field_path == "circuit.instant_power_w"
    assert power.derived is False


def test_derived_sensor_declares_no_path() -> None:
    """dsm_state is a multi-signal derivation with no single source field."""
    from custom_components.span_panel.sensor_definitions import PANEL_DATA_STATUS_SENSORS

    dsm = next(d for d in PANEL_DATA_STATUS_SENSORS if d.key == "dsm_state")
    assert dsm.derived is True
    assert dsm.field_path is None


def test_declared_field_paths_includes_residuals() -> None:
    """Readers that live in entity code rather than on a description still count."""
    paths = declared_field_paths()
    assert "circuit.relay_state" in paths
    assert "circuit.priority" in paths
