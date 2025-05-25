#!/usr/bin/env python3
"""Test script to debug the options flow flag handling."""

import sys
import os

# Add the custom_components path to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components"))

from custom_components.span_panel.const import (
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
    ENTITY_NAMING_PATTERN,
)


def test_new_installation_flow():
    """Test the options flow for a new installation changing from circuit numbers to friendly names."""

    class MockEntry:
        def __init__(self, options):
            self.options = options

    class MockOptionsFlowHandler:
        def __init__(self, entry):
            self.entry = entry

        def _get_current_naming_pattern(self) -> str:
            """Determine the current entity naming pattern from configuration flags."""
            use_circuit_numbers = self.entry.options.get(USE_CIRCUIT_NUMBERS, False)
            use_device_prefix = self.entry.options.get(USE_DEVICE_PREFIX, False)

            if use_circuit_numbers:
                return EntityNamingPattern.CIRCUIT_NUMBERS.value
            elif use_device_prefix:
                return EntityNamingPattern.FRIENDLY_NAMES.value
            else:
                # Pre-1.0.4 installation - no device prefix
                return EntityNamingPattern.LEGACY_NAMES.value

        def simulate_user_input(self, user_input):
            """Simulate the options flow logic."""
            current_pattern = self._get_current_naming_pattern()
            new_pattern = user_input.get(ENTITY_NAMING_PATTERN, current_pattern)

            print(f"Current pattern: {current_pattern}")
            print(f"User selected pattern: {new_pattern}")
            print(f"Pattern changed: {new_pattern != current_pattern}")

            if new_pattern != current_pattern:
                print("Processing pattern change...")
                # Entity naming pattern changed - update the configuration flags
                if new_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value:
                    user_input[USE_CIRCUIT_NUMBERS] = True
                    user_input[USE_DEVICE_PREFIX] = True
                    print(
                        "Set flags for CIRCUIT_NUMBERS: circuit_numbers=True, device_prefix=True"
                    )
                elif new_pattern == EntityNamingPattern.FRIENDLY_NAMES.value:
                    user_input[USE_CIRCUIT_NUMBERS] = False
                    user_input[USE_DEVICE_PREFIX] = True
                    print(
                        "Set flags for FRIENDLY_NAMES: circuit_numbers=False, device_prefix=True"
                    )
                # Note: LEGACY_NAMES is read-only, users can't select it

                # Remove the pattern selector from saved options (it's derived from the flags)
                user_input.pop(ENTITY_NAMING_PATTERN, None)
            else:
                print(
                    "No pattern change - preserving existing flags (including False values)..."
                )
                # No pattern change - preserve existing flags (including False values)
                use_prefix = self.entry.options.get(USE_DEVICE_PREFIX, False)
                user_input[USE_DEVICE_PREFIX] = use_prefix
                print(f"Preserved device_prefix: {use_prefix}")

                use_circuit_numbers = self.entry.options.get(USE_CIRCUIT_NUMBERS, False)
                user_input[USE_CIRCUIT_NUMBERS] = use_circuit_numbers
                print(f"Preserved circuit_numbers: {use_circuit_numbers}")

                # Remove the pattern selector from saved options
                user_input.pop(ENTITY_NAMING_PATTERN, None)

            return user_input

    print("=" * 60)
    print("Testing new installation options flow")
    print("=" * 60)

    # Simulate new installation (as created by create_new_entry)
    new_installation_options = {
        USE_DEVICE_PREFIX: True,
        USE_CIRCUIT_NUMBERS: True,
    }

    entry = MockEntry(new_installation_options)
    handler = MockOptionsFlowHandler(entry)

    print(f"Initial installation options: {new_installation_options}")
    print()

    # Test 1: User selects "Friendly Names" (should change from circuit numbers to friendly names)
    print("Test 1: User selects 'Friendly Names'")
    print("-" * 40)
    user_input = {
        ENTITY_NAMING_PATTERN: EntityNamingPattern.FRIENDLY_NAMES.value,
        # ... other options would be here too
    }

    result = handler.simulate_user_input(user_input.copy())
    print(f"Final user_input after processing: {result}")
    print()

    # Test 2: User selects "Circuit Numbers" (should be no change)
    print("Test 2: User selects 'Circuit Numbers' (no change)")
    print("-" * 40)
    user_input = {
        ENTITY_NAMING_PATTERN: EntityNamingPattern.CIRCUIT_NUMBERS.value,
    }

    result = handler.simulate_user_input(user_input.copy())
    print(f"Final user_input after processing: {result}")
    print()

    print("=" * 60)
    print("Expected behavior:")
    print("- Test 1 should set USE_CIRCUIT_NUMBERS=False, USE_DEVICE_PREFIX=True")
    print("- Test 2 should preserve existing flags (no change)")
    print("=" * 60)


if __name__ == "__main__":
    test_new_installation_flow()
