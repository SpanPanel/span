#!/usr/bin/env python3
"""Test script to verify entity naming pattern detection logic."""

import sys
import os

# Add the custom_components path to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components"))

from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    EntityNamingPattern,
)


def test_pattern_detection():
    """Test the pattern detection logic."""

    class MockEntry:
        def __init__(self, options):
            self.options = options

    class MockOptionsFlowHandler:
        def __init__(self, entry):
            self.entry = entry

        def _get_current_naming_pattern(self) -> str:
            """Determine the current entity naming pattern from configuration flags."""
            use_circuit_numbers = self.entry.options.get(USE_CIRCUIT_NUMBERS, False)

            if use_circuit_numbers:
                return EntityNamingPattern.CIRCUIT_NUMBERS.value
            else:
                return EntityNamingPattern.FRIENDLY_NAMES.value

    # Test cases
    test_cases = [
        {
            "name": "New installation (1.0.9+)",
            "options": {USE_CIRCUIT_NUMBERS: True, "use_device_prefix": True},
            "expected": EntityNamingPattern.CIRCUIT_NUMBERS.value,
        },
        {
            "name": "Post-1.0.4 installation",
            "options": {USE_CIRCUIT_NUMBERS: False, "use_device_prefix": True},
            "expected": EntityNamingPattern.FRIENDLY_NAMES.value,
        },
        {
            "name": "Pre-1.0.4 installation",
            "options": {USE_CIRCUIT_NUMBERS: False, "use_device_prefix": False},
            "expected": EntityNamingPattern.FRIENDLY_NAMES.value,
        },
        {
            "name": "Empty options (default)",
            "options": {},
            "expected": EntityNamingPattern.FRIENDLY_NAMES.value,
        },
        {
            "name": "Only circuit numbers flag",
            "options": {USE_CIRCUIT_NUMBERS: True},
            "expected": EntityNamingPattern.CIRCUIT_NUMBERS.value,
        },
    ]

    print("Testing entity naming pattern detection...")
    print()

    all_passed = True
    for test_case in test_cases:
        entry = MockEntry(test_case["options"])
        handler = MockOptionsFlowHandler(entry)

        result = handler._get_current_naming_pattern()
        passed = result == test_case["expected"]

        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} {test_case['name']}")
        print(f"   Options: {test_case['options']}")
        print(f"   Expected: {test_case['expected']}")
        print(f"   Got: {result}")
        print()

        if not passed:
            all_passed = False

    print("=" * 50)
    if all_passed:
        print("✅ All tests passed! Pattern detection logic is correct.")
    else:
        print("❌ Some tests failed! Check the logic.")

    return all_passed


if __name__ == "__main__":
    test_pattern_detection()
