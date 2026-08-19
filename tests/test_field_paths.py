"""Every entity that reads one snapshot field must say which field."""

from __future__ import annotations

from custom_components.span_panel.binary_sensor import (
    BESS_CONNECTED_SENSOR,
    BINARY_SENSORS,
    EVSE_BINARY_SENSORS,
    GRID_ISLANDABLE_SENSOR,
)
from custom_components.span_panel.field_paths import (
    RESIDUAL_EXEMPT_PATHS,
    declared_field_paths,
    residual_field_paths,
)
from custom_components.span_panel.sensor_definitions import (
    CIRCUIT_SENSORS,
    all_sensor_descriptions,
)


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


def test_every_description_declares_exactly_one() -> None:
    """A description inheriting the mixin but setting neither field is invisible.

    The `TypeError` guard in `declared_field_paths` only catches a description
    that lacks the mixin entirely. Every new sensor inherits it automatically,
    so the likelier mistake is inheriting it and declaring nothing — which
    drops the entity from every gate with no signal. This is that signal.

    Must enumerate exactly what `declared_field_paths` iterates.
    """
    for description in (
        *all_sensor_descriptions(),
        *BINARY_SENSORS,
        *EVSE_BINARY_SENSORS,
        GRID_ISLANDABLE_SENSOR,
        BESS_CONNECTED_SENSOR,
    ):
        declares_path = description.field_path is not None
        assert declares_path != description.derived, (
            f"{description.key} must declare exactly one of field_path / derived=True"
        )


def test_residual_buckets_are_disjoint() -> None:
    """A residual path is either producible or exempt, never both."""
    assert not (residual_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())
    assert not (declared_field_paths() & RESIDUAL_EXEMPT_PATHS.keys())
