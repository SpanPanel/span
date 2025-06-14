"""SPAN Panel Electrical Phase Validation Utilities.

This module provides utilities for validating electrical phase relationships
in SPAN panel configurations, particularly useful for solar inverter setup
and 240V appliance validation.
"""

from typing import Any


def get_valid_tabs_from_panel_data(panel_state: dict[str, Any]) -> list[int]:
    """Extract valid tab numbers from SPAN panel state data.

    Args:
        panel_state: Panel state dictionary containing branches data

    Returns:
        List of valid tab numbers from panel branch data

    Raises:
        TypeError: If panel_state is invalid or missing branches data

    """
    if not isinstance(panel_state, dict):
        raise TypeError("panel_state must be a dictionary")

    branches = panel_state.get("branches", [])
    if not isinstance(branches, list):
        raise TypeError("Invalid branches data in panel state")

    valid_tabs = []
    for branch in branches:
        if isinstance(branch, dict) and "id" in branch:
            tab_id = branch["id"]
            if isinstance(tab_id, int) and tab_id > 0:
                valid_tabs.append(tab_id)

    return sorted(valid_tabs)


def get_valid_tabs_from_branches(branches: list[Any]) -> list[int]:
    """Extract valid tab numbers from SPAN panel branches list.

    Args:
        branches: List of Branch objects or dictionaries with id field

    Returns:
        List of valid tab numbers from branch data

    """
    valid_tabs = []
    for branch in branches:
        # Handle both Branch objects and dictionaries
        if hasattr(branch, "id"):
            tab_id = branch.id
        elif isinstance(branch, dict) and "id" in branch:
            tab_id = branch["id"]
        else:
            continue

        if isinstance(tab_id, int) and tab_id > 0:
            valid_tabs.append(tab_id)

    return sorted(valid_tabs)


def get_tab_phase(tab_number: int, valid_tabs: list[int] | None = None) -> str:
    """Determine which phase (L1 or L2) a tab is connected to.

    SPAN panels have an alternating phase pattern vertically on each side:
    - Left side (odd tabs): 1(L1), 3(L2), 5(L1), 7(L2), etc.
    - Right side (even tabs): 2(L1), 4(L2), 6(L1), 8(L2), etc.

    Args:
        tab_number: Tab position (1-based indexing)
        valid_tabs: Optional list of valid tab numbers from panel data.
                   If provided, tab_number must be in this list.

    Returns:
        "L1" or "L2" phase designation

    Raises:
        ValueError: If tab_number is invalid or not in valid_tabs

    """
    if tab_number < 1:
        raise ValueError(f"Tab number {tab_number} must be >= 1")

    if valid_tabs is not None and tab_number not in valid_tabs:
        raise ValueError(f"Tab number {tab_number} not found in panel branch data")

    # Calculate position within the side (0-indexed)
    # Each side has positions for odd tabs (1,3,5...) on left, even tabs (2,4,6...) on right
    position_in_side = (tab_number - 1) // 2

    # Phases alternate vertically: even positions = L1, odd positions = L2
    return "L1" if position_in_side % 2 == 0 else "L2"


def are_tabs_opposite_phase(
    tab1: int, tab2: int, valid_tabs: list[int] | None = None
) -> bool:
    """Check if two tabs are on opposite phases (suitable for 240V).

    Args:
        tab1: First tab position to check
        tab2: Second tab position to check
        valid_tabs: Optional list of valid tab numbers from panel data

    Returns:
        True if tabs are on opposite phases (L1 + L2), suitable for 240V

    Examples:
        >>> are_tabs_opposite_phase(30, 32)  # 30=L1, 32=L2
        True
        >>> are_tabs_opposite_phase(1, 5)    # 1=L1, 5=L1
        False

    """
    try:
        phase1 = get_tab_phase(tab1, valid_tabs)
        phase2 = get_tab_phase(tab2, valid_tabs)
        return phase1 != phase2
    except ValueError:
        return False


def validate_solar_tabs(
    tab1: int, tab2: int, valid_tabs: list[int] | None = None
) -> tuple[bool, str]:
    """Validate that solar tab configuration provides proper 240V measurement.

    Args:
        tab1: First solar tab number
        tab2: Second solar tab number
        valid_tabs: Optional list of valid tab numbers from panel data

    Returns:
        tuple of (is_valid, message) where:
        - is_valid: True if configuration is electrically sound for 240V
        - message: Descriptive validation result

    """
    try:
        if tab1 == tab2:
            return False, f"Solar tabs cannot be the same tab ({tab1})"

        phase1 = get_tab_phase(tab1, valid_tabs)
        phase2 = get_tab_phase(tab2, valid_tabs)

        if phase1 == phase2:
            return False, (
                f"Solar tabs {tab1} and {tab2} are both on {phase1}. "
                f"For proper 240V measurement, tabs must be on opposite phases."
            )

        return True, (
            f"✓ Valid 240V configuration: tab {tab1} ({phase1}) + tab {tab2} ({phase2})"
        )

    except ValueError as e:
        return False, f"Invalid tab configuration: {e}"


def get_phase_distribution(
    tabs: list[int], valid_tabs: list[int] | None = None
) -> dict:
    """Analyze phase distribution across a list of tabs.

    Args:
        tabs: list of tab numbers to analyze
        valid_tabs: Optional list of valid tab numbers from panel data

    Returns:
        Dictionary with phase distribution analysis:
        {
            "L1_count": int,
            "L2_count": int,
            "L1_tabs": list[int],
            "L2_tabs": list[int],
            "is_balanced": bool,
            "balance_difference": int
        }

    """
    l1_tabs = []
    l2_tabs = []

    for tab in tabs:
        try:
            phase = get_tab_phase(tab, valid_tabs)
            if phase == "L1":
                l1_tabs.append(tab)
            else:
                l2_tabs.append(tab)
        except ValueError:
            continue  # Skip invalid tab numbers

    l1_count = len(l1_tabs)
    l2_count = len(l2_tabs)
    balance_difference = abs(l1_count - l2_count)

    return {
        "L1_count": l1_count,
        "L2_count": l2_count,
        "L1_tabs": sorted(l1_tabs),
        "L2_tabs": sorted(l2_tabs),
        "is_balanced": balance_difference <= 1,  # Allow 1-tab difference
        "balance_difference": balance_difference,
    }


def suggest_balanced_pairing(
    available_tabs: list[int], valid_tabs: list[int] | None = None
) -> list[tuple[int, int]]:
    """Suggest balanced tab pairings for 240V circuits.

    Args:
        available_tabs: list of available tab positions
        valid_tabs: Optional list of valid tab numbers from panel data

    Returns:
        list of (tab1, tab2) tuples representing suggested opposite-phase pairs

    """
    distribution = get_phase_distribution(available_tabs, valid_tabs)
    l1_tabs = distribution["L1_tabs"]
    l2_tabs = distribution["L2_tabs"]

    # Create pairs from opposite phases
    pairs = []
    min_count = min(len(l1_tabs), len(l2_tabs))

    for i in range(min_count):
        pairs.append((l1_tabs[i], l2_tabs[i]))

    return pairs
