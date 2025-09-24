#!/usr/bin/env python3
"""Quick test to verify phase validation implementation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "span-panel-api/src"))

from span_panel_api.phase_validation import (
    are_tabs_opposite_phase,
    get_tab_phase,
    validate_solar_tabs,
)


def test_phase_validation():
    """Test phase validation functionality."""

    print("Testing SPAN Panel Phase Validation:")
    print("====================================")

    # Test phase assignment for various tabs
    test_tabs = [1, 2, 3, 4, 5, 6, 29, 30, 31, 32, 33, 34, 35, 36]
    print("\nTab phase assignments:")
    for tab in test_tabs:
        phase = get_tab_phase(tab)
        print(f"Tab {tab:2d}: {phase}")

    print("\nTesting opposite phase validation:")

    # Test cases from span-panel-api phase validation
    test_pairs = [
        (30, 32),  # L1, L2 - should be valid
        (30, 34),  # L1, L1 - should be invalid
        (1, 2),  # L1, L1 - should be invalid
        (1, 3),  # L1, L2 - should be valid
        (29, 31),  # L1, L2 - should be valid
        (31, 33),  # L2, L1 - should be valid
    ]

    for tab1, tab2 in test_pairs:
        phase1 = get_tab_phase(tab1)
        phase2 = get_tab_phase(tab2)
        opposite = are_tabs_opposite_phase(tab1, tab2)
        is_valid, message = validate_solar_tabs(tab1, tab2)

        print(
            f"Tabs {tab1:2d} ({phase1}) & {tab2:2d} ({phase2}): opposite={opposite}, valid={is_valid}"
        )
        if not is_valid:
            print(f"  → {message}")

    print("\nTesting validation edge cases:")

    # Same tab
    is_valid, message = validate_solar_tabs(30, 30)
    print(f"Same tab (30, 30): {is_valid} - {message}")

    # Invalid tab numbers
    try:
        is_valid, message = validate_solar_tabs(0, 32)
        print(f"Invalid tab (0, 32): {is_valid} - {message}")
    except ValueError as e:
        print(f"Invalid tab (0, 32): ValueError - {e}")


if __name__ == "__main__":
    test_phase_validation()
