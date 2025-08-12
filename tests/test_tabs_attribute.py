#!/usr/bin/env python3
"""Test script for tabs attribute functionality."""

import sys
import os

# Add the custom_components directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components"))

from custom_components.span_panel.helpers import (
    construct_tabs_attribute,
    construct_voltage_attribute,
    parse_tabs_attribute,
    get_circuit_voltage_type,
)
from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit


def test_tabs_attribute_construction():
    """Test tabs attribute construction from circuit data."""
    print("Testing tabs attribute construction...")

    # Test single tab (120V)
    circuit_120v = SpanPanelCircuit(
        circuit_id="test_120v",
        name="Test 120V Circuit",
        relay_state="CLOSED",
        instant_power=100.0,
        instant_power_update_time=1234567890,
        produced_energy=1000.0,
        consumed_energy=500.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[28],
    )

    tabs_attr_120v = construct_tabs_attribute(circuit_120v)
    print(f"120V circuit tabs attribute: {tabs_attr_120v}")
    assert tabs_attr_120v == "tabs [28]"

    # Test two tabs (240V)
    circuit_240v = SpanPanelCircuit(
        circuit_id="test_240v",
        name="Test 240V Circuit",
        relay_state="CLOSED",
        instant_power=200.0,
        instant_power_update_time=1234567890,
        produced_energy=2000.0,
        consumed_energy=1000.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[30, 32],
    )

    tabs_attr_240v = construct_tabs_attribute(circuit_240v)
    print(f"240V circuit tabs attribute: {tabs_attr_240v}")
    assert tabs_attr_240v == "tabs [30:32]"

    # Test circuit with no tabs
    circuit_no_tabs = SpanPanelCircuit(
        circuit_id="test_no_tabs",
        name="Test No Tabs Circuit",
        relay_state="CLOSED",
        instant_power=50.0,
        instant_power_update_time=1234567890,
        produced_energy=500.0,
        consumed_energy=250.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[],
    )

    tabs_attr_no_tabs = construct_tabs_attribute(circuit_no_tabs)
    print(f"No tabs circuit tabs attribute: {tabs_attr_no_tabs}")
    assert tabs_attr_no_tabs is None

    # Test circuit with more than 2 tabs (invalid for US electrical system)
    circuit_invalid = SpanPanelCircuit(
        circuit_id="test_invalid",
        name="Test Invalid Circuit",
        relay_state="CLOSED",
        instant_power=300.0,
        instant_power_update_time=1234567890,
        produced_energy=3000.0,
        consumed_energy=1500.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[1, 3, 5],  # More than 2 tabs - invalid for US electrical system
    )

    tabs_attr_invalid = construct_tabs_attribute(circuit_invalid)
    print(f"Invalid circuit tabs attribute: {tabs_attr_invalid}")
    assert tabs_attr_invalid is None

    print("✓ Tabs attribute construction tests passed!")


def test_tabs_attribute_parsing():
    """Test tabs attribute parsing back to tab numbers."""
    print("\nTesting tabs attribute parsing...")

    # Test parsing single tab (120V)
    tabs_120v = parse_tabs_attribute("tabs [28]")
    print(f"Parsed 120V tabs: {tabs_120v}")
    assert tabs_120v == [28]

    # Test parsing two tabs (240V)
    tabs_240v = parse_tabs_attribute("tabs [30:32]")
    print(f"Parsed 240V tabs: {tabs_240v}")
    assert tabs_240v == [30, 32]

    # Test parsing invalid formats
    invalid_tabs = parse_tabs_attribute("invalid format")
    print(f"Parsed invalid format: {invalid_tabs}")
    assert invalid_tabs is None

    invalid_tabs2 = parse_tabs_attribute("tabs [invalid]")
    print(f"Parsed invalid content: {invalid_tabs2}")
    assert invalid_tabs2 is None

    # Test parsing comma-separated format (not valid for US electrical system)
    tabs_multi = parse_tabs_attribute("tabs [1,3,5]")
    print(f"Parsed multi tabs (should be None for US system): {tabs_multi}")
    assert tabs_multi is None

    print("✓ Tabs attribute parsing tests passed!")


def test_voltage_type_detection():
    """Test voltage type detection from circuit data."""
    print("\nTesting voltage type detection...")

    # Test 120V circuit
    circuit_120v = SpanPanelCircuit(
        circuit_id="test_120v",
        name="Test 120V Circuit",
        relay_state="CLOSED",
        instant_power=100.0,
        instant_power_update_time=1234567890,
        produced_energy=1000.0,
        consumed_energy=500.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[28],
    )

    voltage_type_120v = get_circuit_voltage_type(circuit_120v)
    print(f"120V circuit voltage type: {voltage_type_120v}")
    assert voltage_type_120v == "120V"

    # Test 240V circuit
    circuit_240v = SpanPanelCircuit(
        circuit_id="test_240v",
        name="Test 240V Circuit",
        relay_state="CLOSED",
        instant_power=200.0,
        instant_power_update_time=1234567890,
        produced_energy=2000.0,
        consumed_energy=1000.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[30, 32],
    )

    voltage_type_240v = get_circuit_voltage_type(circuit_240v)
    print(f"240V circuit voltage type: {voltage_type_240v}")
    assert voltage_type_240v == "240V"

    # Test circuit with no tabs
    circuit_no_tabs = SpanPanelCircuit(
        circuit_id="test_no_tabs",
        name="Test No Tabs Circuit",
        relay_state="CLOSED",
        instant_power=50.0,
        instant_power_update_time=1234567890,
        produced_energy=500.0,
        consumed_energy=250.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[],
    )

    voltage_type_no_tabs = get_circuit_voltage_type(circuit_no_tabs)
    print(f"No tabs circuit voltage type: {voltage_type_no_tabs}")
    assert voltage_type_no_tabs == "unknown"

    # Test circuit with more than 2 tabs (invalid for US electrical system)
    circuit_invalid = SpanPanelCircuit(
        circuit_id="test_invalid",
        name="Test Invalid Circuit",
        relay_state="CLOSED",
        instant_power=300.0,
        instant_power_update_time=1234567890,
        produced_energy=3000.0,
        consumed_energy=1500.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[1, 3, 5],  # More than 2 tabs - invalid for US electrical system
    )

    voltage_type_invalid = get_circuit_voltage_type(circuit_invalid)
    print(f"Invalid circuit voltage type: {voltage_type_invalid}")
    assert voltage_type_invalid == "unknown"

    print("✓ Voltage type detection tests passed!")


def test_voltage_attribute_construction():
    """Test voltage attribute construction from circuit data."""
    print("\nTesting voltage attribute construction...")

    # Test single tab (120V)
    circuit_120v = SpanPanelCircuit(
        circuit_id="test_120v",
        name="Test 120V Circuit",
        relay_state="CLOSED",
        instant_power=100.0,
        instant_power_update_time=1234567890,
        produced_energy=1000.0,
        consumed_energy=500.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[28],
    )

    voltage_attr_120v = construct_voltage_attribute(circuit_120v)
    print(f"120V circuit voltage attribute: {voltage_attr_120v}")
    assert voltage_attr_120v == 120

    # Test two tabs (240V)
    circuit_240v = SpanPanelCircuit(
        circuit_id="test_240v",
        name="Test 240V Circuit",
        relay_state="CLOSED",
        instant_power=200.0,
        instant_power_update_time=1234567890,
        produced_energy=2000.0,
        consumed_energy=1000.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[30, 32],
    )

    voltage_attr_240v = construct_voltage_attribute(circuit_240v)
    print(f"240V circuit voltage attribute: {voltage_attr_240v}")
    assert voltage_attr_240v == 240

    # Test circuit with no tabs
    circuit_no_tabs = SpanPanelCircuit(
        circuit_id="test_no_tabs",
        name="Test No Tabs Circuit",
        relay_state="CLOSED",
        instant_power=50.0,
        instant_power_update_time=1234567890,
        produced_energy=500.0,
        consumed_energy=250.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[],
    )

    voltage_attr_no_tabs = construct_voltage_attribute(circuit_no_tabs)
    print(f"No tabs circuit voltage attribute: {voltage_attr_no_tabs}")
    assert voltage_attr_no_tabs is None

    # Test circuit with more than 2 tabs (invalid for US electrical system)
    circuit_invalid = SpanPanelCircuit(
        circuit_id="test_invalid",
        name="Test Invalid Circuit",
        relay_state="CLOSED",
        instant_power=300.0,
        instant_power_update_time=1234567890,
        produced_energy=3000.0,
        consumed_energy=1500.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[1, 3, 5],  # More than 2 tabs - invalid for US electrical system
    )

    voltage_attr_invalid = construct_voltage_attribute(circuit_invalid)
    print(f"Invalid circuit voltage attribute: {voltage_attr_invalid}")
    assert voltage_attr_invalid is None

    print("✓ Voltage attribute construction tests passed!")


def test_end_to_end_tabs_workflow():
    """Test the complete workflow from circuit data to tabs attribute and back."""
    print("\nTesting end-to-end tabs workflow...")

    # Create a 240V circuit
    circuit = SpanPanelCircuit(
        circuit_id="test_240v_workflow",
        name="Test 240V Workflow Circuit",
        relay_state="CLOSED",
        instant_power=200.0,
        instant_power_update_time=1234567890,
        produced_energy=2000.0,
        consumed_energy=1000.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[30, 32],
    )

    # Step 1: Generate tabs attribute
    tabs_attr = construct_tabs_attribute(circuit)
    print(f"Generated tabs attribute: {tabs_attr}")
    assert tabs_attr == "tabs [30:32]"

    # Step 2: Parse tabs attribute back to numbers
    parsed_tabs = parse_tabs_attribute(tabs_attr)
    print(f"Parsed tabs: {parsed_tabs}")
    assert parsed_tabs == [30, 32]

    # Step 3: Verify voltage type
    voltage_type = get_circuit_voltage_type(circuit)
    print(f"Voltage type: {voltage_type}")
    assert voltage_type == "240V"

    # Step 4: Verify voltage attribute
    voltage_attr = construct_voltage_attribute(circuit)
    print(f"Voltage attribute: {voltage_attr}")
    assert voltage_attr == 240

    print("✓ End-to-end tabs workflow test passed!")


def test_amperage_calculation():
    """Test amperage calculation using voltage and power."""
    print("\nTesting amperage calculation...")

    # Test 120V circuit with 1200W power (should be 10A)
    circuit_120v = SpanPanelCircuit(
        circuit_id="test_120v_10a",
        name="Test 120V 10A Circuit",
        relay_state="CLOSED",
        instant_power=1200.0,  # 1200W
        instant_power_update_time=1234567890,
        produced_energy=1000.0,
        consumed_energy=500.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[28],
    )

    voltage_120v = construct_voltage_attribute(circuit_120v)
    expected_amperage_120v = circuit_120v.instant_power / voltage_120v
    print(
        f"120V circuit: {circuit_120v.instant_power}W / {voltage_120v}V = {expected_amperage_120v}A"
    )
    assert expected_amperage_120v == 10.0  # 1200W / 120V = 10A

    # Test 240V circuit with 4800W power (should be 20A)
    circuit_240v = SpanPanelCircuit(
        circuit_id="test_240v_20a",
        name="Test 240V 20A Circuit",
        relay_state="CLOSED",
        instant_power=4800.0,  # 4800W
        instant_power_update_time=1234567890,
        produced_energy=2000.0,
        consumed_energy=1000.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[30, 32],
    )

    voltage_240v = construct_voltage_attribute(circuit_240v)
    expected_amperage_240v = circuit_240v.instant_power / voltage_240v
    print(
        f"240V circuit: {circuit_240v.instant_power}W / {voltage_240v}V = {expected_amperage_240v}A"
    )
    assert expected_amperage_240v == 20.0  # 4800W / 240V = 20A

    # Test edge case: 0W power (should be 0A)
    circuit_zero = SpanPanelCircuit(
        circuit_id="test_zero_power",
        name="Test Zero Power Circuit",
        relay_state="CLOSED",
        instant_power=0.0,  # 0W
        instant_power_update_time=1234567890,
        produced_energy=0.0,
        consumed_energy=0.0,
        energy_accum_update_time=1234567890,
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=True,
        is_never_backup=False,
        tabs=[28],
    )

    voltage_zero = construct_voltage_attribute(circuit_zero)
    expected_amperage_zero = circuit_zero.instant_power / voltage_zero
    print(
        f"Zero power circuit: {circuit_zero.instant_power}W / {voltage_zero}V = {expected_amperage_zero}A"
    )
    assert expected_amperage_zero == 0.0  # 0W / 120V = 0A

    print("✓ Amperage calculation tests passed!")


if __name__ == "__main__":
    print("Running tabs attribute functionality tests...\n")

    try:
        test_tabs_attribute_construction()
        test_tabs_attribute_parsing()
        test_voltage_type_detection()
        test_voltage_attribute_construction()
        test_amperage_calculation()
        test_end_to_end_tabs_workflow()

        print("\n🎉 All tests passed! Tabs attribute functionality is working correctly.")

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
