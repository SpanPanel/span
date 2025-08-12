#!/usr/bin/env python3
"""Test sensor mapping functionality."""

import pytest
from span_panel_api.phase_validation import get_tab_phase, are_tabs_opposite_phase


def test_tab_phase_determination():
    """Test that tab phases are determined correctly."""
    # Test some known phase assignments
    assert get_tab_phase(1) == "L1"  # Left side, position 0
    assert get_tab_phase(2) == "L1"  # Right side, position 0
    assert get_tab_phase(3) == "L2"  # Left side, position 1
    assert get_tab_phase(4) == "L2"  # Right side, position 1
    assert get_tab_phase(5) == "L1"  # Left side, position 2
    assert get_tab_phase(6) == "L1"  # Right side, position 2


def test_opposite_phase_validation():
    """Test that opposite phase validation works correctly."""
    # Test opposite phase combinations (should be valid)
    assert are_tabs_opposite_phase(1, 3) is True   # L1 + L2
    assert are_tabs_opposite_phase(2, 4) is True   # L1 + L2
    assert are_tabs_opposite_phase(1, 4) is True   # L1 + L2
    assert are_tabs_opposite_phase(3, 6) is True   # L2 + L1

    # Test same phase combinations (should be invalid)
    assert are_tabs_opposite_phase(1, 2) is False  # L1 + L1
    assert are_tabs_opposite_phase(3, 4) is False  # L2 + L2
    assert are_tabs_opposite_phase(1, 5) is False  # L1 + L1
    assert are_tabs_opposite_phase(2, 6) is False  # L1 + L1


def test_filtered_tab_options():
    """Test the filtered tab options function."""
    from custom_components.span_panel.config_flow import get_filtered_tab_options

    available_tabs = [1, 2, 3, 4, 5, 6, 7, 8]

    # Test with no selection (should show all tabs)
    all_options = get_filtered_tab_options(0, available_tabs)
    assert 0 in all_options  # None option
    assert all(tab in all_options for tab in available_tabs)

    # Test with tab 1 selected (L1) - should show only L2 tabs
    leg1_options = get_filtered_tab_options(1, available_tabs)
    assert 0 in leg1_options  # None option always included
    assert 1 not in leg1_options  # Selected tab not in options
    assert 2 not in leg1_options  # Same phase (L1)
    assert 3 in leg1_options   # Opposite phase (L2)
    assert 4 in leg1_options   # Opposite phase (L2)
    assert 5 not in leg1_options  # Same phase (L1)
    assert 6 not in leg1_options  # Same phase (L1)
    assert 7 in leg1_options   # Opposite phase (L2)
    assert 8 in leg1_options   # Opposite phase (L2)

    # Test with tab 3 selected (L2) - should show only L1 tabs
    leg2_options = get_filtered_tab_options(3, available_tabs)
    assert 0 in leg2_options  # None option always included
    assert 3 not in leg2_options  # Selected tab not in options
    assert 1 in leg2_options   # Opposite phase (L1)
    assert 2 in leg2_options   # Opposite phase (L1)
    assert 4 not in leg2_options  # Same phase (L2)
    assert 5 in leg2_options   # Opposite phase (L1)
    assert 6 in leg2_options   # Opposite phase (L1)
    assert 7 not in leg2_options  # Same phase (L2)
    assert 8 not in leg2_options  # Same phase (L2)


if __name__ == "__main__":
    pytest.main([__file__])
