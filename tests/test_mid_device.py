"""The Microgrid Interconnect Device, surfaced as its own device.

v1.0 publishes a MID and puts the `grid` capability on it rather than on the enclosure.
Not every panel declares one: a panel without a MID gets no MID device, and nothing
that reads the MID may assume it is there.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from span_panel_api import SpanMidSnapshot, SpanPanelSnapshot

from custom_components.span_panel.helpers import detect_capabilities, has_mid
from custom_components.span_panel.sensor import create_mid_sensors
from custom_components.span_panel.sensor_definitions import MID_SENSORS
from custom_components.span_panel.sensor_panel import _grid_forming_device_name
from custom_components.span_panel.util import mid_device_info

from .adapter_fixtures import schema_one_snapshot, schema_one_tree
from .factories import SpanPanelSnapshotFactory

MID_DEVICE = "bess-mid"
"""The capture's MID, which it declares as a child of the BESS."""


def _without_a_mid() -> SpanPanelSnapshot:
    """The capture with its MID gone, as a panel that declares none publishes it."""
    return schema_one_snapshot(schema_one_tree(without=MID_DEVICE))


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


def test_a_panel_without_a_mid_gets_no_mid_device() -> None:
    """The gate reads what the snapshot carries, because a panel can declare no MID.

    The capture with its MID removed is that panel, built through the real
    adapter, so the absence is the adapter's `None` rather than a value a test
    wrote down.
    """
    assert has_mid(schema_one_snapshot()) is True, "the capture's MID is the premise"

    snapshot = _without_a_mid()

    assert has_mid(snapshot) is False
    assert create_mid_sensors(_coordinator(), snapshot) == []


def test_presence_needs_no_sentinel() -> None:
    """`snapshot.mid is not None`, unlike `has_bess`, which infers from soe_percentage
    because the battery field is always present.
    """
    assert has_mid(SpanPanelSnapshotFactory.create(mid=_mid())) is True


def test_the_mid_becomes_its_own_device_hung_off_the_panel() -> None:
    """Identity renders on a device card rather than being folded onto the panel.

    Linked to the panel even though the wire tree makes the MID a child of the
    BESS: Home Assistant's device graph is what a user navigates, and every SPAN
    sub-device hangs off the panel there.
    """
    info = mid_device_info(
        "sim-40t-001", _mid(), "SPAN Panel", panel_device_id="panel-device-id"
    )

    assert info["identifiers"] == {("span_panel", "sim-40t-001_mid")}
    assert info["name"] == "SPAN Panel Microgrid Interconnect"
    assert info["manufacturer"] == "Span"
    assert info["serial_number"] == "SIM-BESS-40T-001-mid"
    assert info["via_device_id"] == "panel-device-id"
    # A panel that publishes no MID model still needs a legible card. This was written
    # when no producer published one at all; panelbench does now, so this is the
    # fallback path rather than the only path — `test_the_mid_card_carries_the_identity
    # _a_producer_publishes` covers the other.
    assert info["model"] == "Microgrid Interconnect Device"
    assert "sw_version" not in info or info["sw_version"] is None
    assert "hw_version" not in info or info["hw_version"] is None


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


def test_no_dual_schema_mark_remains() -> None:
    """The integration serves one schema, so nothing in it may branch on which one.

    `DUAL-SCHEMA` marked every branch that differed between the flat and the
    parent/child schema while both were served. A mark left behind would claim a
    second schema this integration no longer serves. If two schemas are ever
    served side by side again, the marks return with that decision, and this
    guard changes with it.
    """
    component = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"
    marked = sorted(
        str(path.relative_to(component))
        for path in component.rglob("*.py")
        if "DUAL-SCHEMA" in path.read_text(encoding="utf-8")
    )

    assert marked == [], f"DUAL-SCHEMA marks remain in {marked}"


def test_nothing_reads_the_mid_without_checking_it_is_there() -> None:
    """A snapshot without a MID must survive every MID code path untouched.

    This is the constraint that matters more than any single feature: a panel that
    declares no MID is an ordinary panel, so the integration has to keep working on
    it.
    """
    snapshot = _without_a_mid()

    assert snapshot.mid is None
    assert has_mid(snapshot) is False
    assert create_mid_sensors(_coordinator(), snapshot) == []
    assert _grid_forming_device_name(snapshot) is None
    assert "mid" not in detect_capabilities(snapshot)


def test_the_mid_card_carries_the_identity_a_producer_publishes() -> None:
    """Model, firmware and hardware revision reach the device card when published.

    r202633 documents all three on the MID's `info` node. Until the library carried the
    latter two, `mid_device_info` could set a model and a serial and nothing else, so a
    user saw a Microgrid Interconnect card with no firmware row beside a battery that
    had one — the battery's identical property having been mapped from the start.

    Not gated. `has_mid` keeps every caller of this builder off a panel that declares
    no MID, so a guard here would be unreachable code implying a case that cannot
    arise.
    """
    info = mid_device_info(
        "sim-40t-001",
        _mid(model="SPAN MID", software_version="sim-mid/v0.1.0", hardware_version="rev1"),
        "SPAN Panel",
        panel_device_id="panel-device-id",
    )

    assert info["model"] == "SPAN MID"
    assert info["sw_version"] == "sim-mid/v0.1.0"
    assert info["hw_version"] == "rev1"


def test_an_unpublished_revision_omits_the_row_rather_than_blanking_it() -> None:
    """`None` and `""` are different to a user, so the library's distinction is kept.

    `DeviceInfo` omits a `None` field and renders an empty string as a present-but-blank
    row. Defaulting with `or ""` here would invent a firmware row reading empty for a
    panel that published nothing, which is worse than no row: it asserts the panel
    answered and the answer was nothing.
    """
    info = mid_device_info(
        "sim-40t-001",
        _mid(software_version=None, hardware_version=None),
        "SPAN Panel",
        panel_device_id="panel-device-id",
    )

    assert info.get("sw_version") is None
    assert info.get("hw_version") is None
