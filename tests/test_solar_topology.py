"""The topology command's `solar` block: which inverter a PV device shows and what its power is."""

from __future__ import annotations

from custom_components.span_panel.util import PvDeviceRef, pv_device_ref

PANEL = "sp3-solar-001"


def test_the_solar_device_is_the_panels_pv_identifier() -> None:
    assert pv_device_ref(f"{PANEL}_pv", PANEL) == PvDeviceRef(None)


def test_an_inverter_device_carries_its_key() -> None:
    assert pv_device_ref(f"{PANEL}_pv_5be1d2c3a4f5061728394a5b6c7d8e9f", PANEL) == PvDeviceRef(
        "5be1d2c3a4f5061728394a5b6c7d8e9f"
    )
    assert pv_device_ref(f"{PANEL}_pv_panel-se7600h-us-1", PANEL) == PvDeviceRef(
        "panel-se7600h-us-1"
    )


def test_nothing_else_is_a_pv_device() -> None:
    assert pv_device_ref(f"{PANEL}_bess", PANEL) is None
    assert pv_device_ref(f"{PANEL}_evse_node_pv_1", PANEL) is None
    assert pv_device_ref(f"{PANEL}_adopted_pv", PANEL) is None
    assert pv_device_ref(f"{PANEL}_pv_", PANEL) is None
    assert pv_device_ref("other-panel_pv", PANEL) is None
