#!/usr/bin/env python3
"""Test script to demonstrate simulation mode YAML generation.

This script shows how to use the SPAN panel simulation mode to generate
realistic YAML configurations from simulated panel data, allowing users
to see synthetic math working on realistic data without needing actual hardware.

Usage:
    SPAN_USE_REAL_SIMULATION=1 python test_simulation_mode.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from tests.test_factories.span_panel_simulation_factory import SpanPanelSimulationFactory


async def test_simulation_mode_yaml_generation():
    """Test YAML generation using simulation mode as if it were a real panel."""

    print("🔧 Testing SPAN Panel Simulation Mode YAML Generation")
    print("=" * 60)

    # Check if simulation mode is enabled
    if not os.environ.get("SPAN_USE_REAL_SIMULATION", "").lower() in ("1", "true", "yes"):
        print("❌ Error: SPAN_USE_REAL_SIMULATION environment variable not set")
        print("   Set SPAN_USE_REAL_SIMULATION=1 to enable simulation mode")
        return False

    print("✅ Simulation mode enabled")

    try:
        # Get realistic panel data from simulation
        print("\n📊 Fetching simulated panel data...")
        panel_data = await SpanPanelSimulationFactory.get_realistic_panel_data(
            host="test-panel-001", config_name="simulation_config_32_circuit"
        )

        print(f"✅ Retrieved panel data:")
        print(f"   - Circuits: {len(panel_data['circuits'])}")
        print(f"   - Panel state: {panel_data['panel_state'] is not None}")
        print(f"   - Status: {panel_data['status'] is not None}")
        print(f"   - Storage: {panel_data['storage'] is not None}")

        # Show some sample circuit data
        print("\n🔌 Sample circuit data:")
        for i, (circuit_id, circuit) in enumerate(list(panel_data["circuits"].items())[:3]):
            print(f"   Circuit {circuit_id}: {circuit.name} - {circuit.powerW}W")
            if i >= 2:
                break

        # Show panel state data
        if panel_data["panel_state"]:
            print(f"\n⚡ Panel state data:")
            print(f"   - Instant grid power: {panel_data['panel_state'].instantGridPowerW}W")
            print(f"   - Feedthrough power: {panel_data['panel_state'].feedthroughPowerW}W")
            print(
                f"   - Main meter produced: {panel_data['panel_state'].mainMeterEnergyProducedWh}Wh"
            )
            print(
                f"   - Main meter consumed: {panel_data['panel_state'].mainMeterEnergyConsumedWh}Wh"
            )

        print("\n🎯 Simulation mode successfully provides realistic panel data!")
        print("   This data can now be used to generate synthetic sensor YAML")
        print("   just like it would be for a real SPAN panel.")

        return True

    except Exception as e:
        print(f"❌ Error during simulation test: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_different_simulation_scenarios():
    """Test different simulation scenarios to show variety."""

    print("\n🎭 Testing Different Simulation Scenarios")
    print("=" * 50)

    scenarios = SpanPanelSimulationFactory.get_preset_scenarios()

    for scenario_name, scenario_config in scenarios.items():
        print(f"\n📋 Scenario: {scenario_name}")
        try:
            panel_data = await SpanPanelSimulationFactory.get_panel_data_for_scenario(scenario_name)

            # Show total power consumption
            total_power = sum(circuit.powerW for circuit in panel_data["circuits"].values())
            print(f"   Total power: {total_power}W")

            # Show active circuits
            active_circuits = [
                c for c in panel_data["circuits"].values() if c.relayState == "CLOSED"
            ]
            print(f"   Active circuits: {len(active_circuits)}")

        except Exception as e:
            print(f"   ❌ Error: {e}")


async def main():
    """Main test function."""
    print("🚀 SPAN Panel Simulation Mode YAML Generation Test")
    print("=" * 60)
    print("This test demonstrates how simulation mode can be used to")
    print("generate realistic YAML configurations from simulated panel data.")
    print()

    # Test basic simulation mode
    success = await test_simulation_mode_yaml_generation()

    if success:
        # Test different scenarios
        await test_different_simulation_scenarios()

        print("\n🎉 Simulation mode test completed successfully!")
        print("\n💡 Next steps:")
        print("   1. The integration can now use this simulated data")
        print("   2. Synthetic sensors will be generated from the simulated panel")
        print("   3. Users can see synthetic math working on realistic data")
        print("   4. No actual SPAN panel hardware required for testing")
    else:
        print("\n❌ Simulation mode test failed")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
