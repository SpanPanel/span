"""The Microgrid Interconnect Device, surfaced as its own device.

v1.0 publishes a MID and puts the `grid` capability on it rather than on the enclosure.
Everything here is additive: no flat panel publishes a MID, so `has_mid` is false on
every existing install and nothing a user has today changes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from span_panel_api import SpanMidSnapshot

from custom_components.span_panel.helpers import detect_capabilities, has_mid
from custom_components.span_panel.sensor import create_mid_sensors
from custom_components.span_panel.sensor_definitions import MID_SENSORS
from custom_components.span_panel.sensor_panel import _grid_forming_device_name
from custom_components.span_panel.util import mid_device_info

from .factories import SpanPanelSnapshotFactory


def _mid(**overrides: str | None) -> SpanMidSnapshot:
    defaults: dict[str, str | None] = {
        "node_id": "SIM-BESS-40T-001-mid",
        "serial_number": "SIM-BESS-40T-001-mid",
        "vendor_name": "Span",
        "model": None,
        "islanding_state": "ON_GRID",
        "grid_state": "UP",
        "grid_forming_entity": "GRID",
    }
    defaults.update(overrides)
    return SpanMidSnapshot(**defaults)  # type: ignore[arg-type]


def _coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    return coordinator


def test_a_flat_panel_has_no_mid() -> None:
    """The whole reason this is additive rather than a migration risk."""
    assert has_mid(SpanPanelSnapshotFactory.create()) is False
    assert create_mid_sensors(_coordinator(), SpanPanelSnapshotFactory.create()) == []


def test_presence_needs_no_sentinel() -> None:
    """`snapshot.mid is not None`, unlike `has_bess`, which infers from soe_percentage
    because the battery field is always present.
    """
    assert has_mid(SpanPanelSnapshotFactory.create(mid=_mid())) is True


def test_the_mid_becomes_its_own_device_hung_off_the_panel() -> None:
    """Identity renders on a device card rather than being folded onto the panel.

    `via_device` is the panel even though the wire tree makes the MID a child of the
    BESS: Home Assistant's device graph is what a user navigates, and every SPAN
    sub-device hangs off the panel there.
    """
    info = mid_device_info("sim-40t-001", _mid(), "SPAN Panel")

    assert info["identifiers"] == {("span_panel", "sim-40t-001_mid")}
    assert info["name"] == "SPAN Panel Microgrid Interconnect"
    assert info["manufacturer"] == "Span"
    assert info["serial_number"] == "SIM-BESS-40T-001-mid"
    assert info["via_device"] == ("span_panel", "sim-40t-001")
    # The producer publishes no MID model today; the card still needs a legible one.
    assert info["model"] == "Microgrid Interconnect Device"


def test_the_mid_carries_grid_state_and_nothing_already_surfaced() -> None:
    """Utility-supply health is genuinely new; islanding state is not.

    `dsm_state` and `grid_forming_entity` already reach a user from the panel and must
    keep their ids and history. Duplicating them here would show the same fact twice,
    which is not the benign cell of the absorb-or-surface policy — adding a fact nobody
    had is.
    """
    keys = {desc.key for desc in MID_SENSORS}

    assert keys == {"mid_grid_state"}


def test_grid_state_is_lowercased_into_its_enum_options() -> None:
    """Home Assistant validates an ENUM sensor against `options`, and the wire sends
    `UP` / `DOWN` / `DEGRADED`.
    """
    (grid_state,) = MID_SENSORS

    assert grid_state.value_fn(_mid(grid_state="UP")) == "up"
    assert grid_state.value_fn(_mid(grid_state="DEGRADED")) == "degraded"
    # A MID mid-discovery has a description and no values yet.
    assert grid_state.value_fn(_mid(grid_state=None)) == "unknown"
    assert set(grid_state.options or []) >= {"up", "down", "degraded", "unknown"}


def test_a_mid_appearing_is_a_capability_change() -> None:
    """The coordinator reloads on a new capability, which is how the device and its
    sensors get created on a panel that gains a MID mid-life.
    """
    without = detect_capabilities(SpanPanelSnapshotFactory.create())
    with_mid = detect_capabilities(SpanPanelSnapshotFactory.create(mid=_mid()))

    assert "mid" not in without
    assert "mid" in with_mid


def test_every_schema_conditional_is_findable() -> None:
    """The integration serves flat and parent/child side by side until every panel has
    hot-loaded v1.0, and the branches that make that work have to be findable when the
    flat path is finally retired.

    Asserted rather than left to a convention, because the failure mode is silent: a
    later addition that assumes parent/child would work on the developer's panel and
    break on everyone else's, and nothing would say so.
    """
    component = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"
    marked = sorted(
        path.name for path in component.rglob("*.py") if "DUAL-SCHEMA" in path.read_text()
    )

    assert marked == ["binary_sensor.py", "helpers.py", "sensor.py", "sensor_panel.py"], (
        "the set of schema-conditional modules moved. If a conditional was added, mark it "
        "DUAL-SCHEMA so it can be found when flat support is dropped; if one was removed, "
        "update this list."
    )


def test_nothing_reads_the_mid_without_checking_it_is_there() -> None:
    """A flat snapshot must survive every MID code path untouched.

    This is the constraint that matters more than any single feature: the integration
    has to keep working on a panel that will never publish a MID, for as long as such
    panels exist.
    """
    flat = SpanPanelSnapshotFactory.create()

    assert flat.mid is None
    assert has_mid(flat) is False
    assert create_mid_sensors(_coordinator(), flat) == []
    assert _grid_forming_device_name(flat) is None
    assert "mid" not in detect_capabilities(flat)
