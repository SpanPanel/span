#!/bin/bash

# SPAN Panel Migration Test Suite
# This script runs the complete migration test workflow:
# 1. Generate migration test configuration
# 2. Run migration validation test

set -e  # Exit on any error

echo "SPAN Panel Migration Test Suite"
echo "=================================="
echo ""

# Check if we're in the right directory
if [ ! -f "generate_migration_test_config.py" ] || [ ! -f "test_migration_1_0_10_to_1_2_0.py" ]; then
    echo "Error: Must run from tests/scripts directory"
    echo "   Expected files: generate_migration_test_config.py, test_migration_1_0_10_to_1_2_0.py"
    exit 1
fi

echo "Step 1: Generating migration test configuration..."
echo "   This creates the test YAML that will be validated in step 2"
echo ""

# Run the generate script
if python3 generate_migration_test_config.py; then
    echo ""
    echo "Step 1 completed successfully"
    echo "   Generated YAML saved to: /tmp/span_migration_test_config.yaml"
    echo ""
else
    echo ""
    echo "Step 1 failed - generation script encountered an error"
    echo "   Check the output above for details"
    exit 1
fi

echo "Step 2: Running migration validation test..."
echo "   This validates the generated YAML against expected migration results"
echo ""

# Run the test script
if python3 test_migration_1_0_10_to_1_2_0.py; then
    echo ""
    echo "Step 2 completed successfully"
    echo ""
else
    echo ""
    echo "Step 2 failed - validation test encountered an error"
    echo "   Check the output above for details"
    exit 1
fi

echo "MIGRATION TEST SUITE COMPLETED SUCCESSFULLY!"
echo "==============================================="
echo ""
echo "Generated files:"
echo "   • /tmp/span_migration_test_config.yaml - Complete migration YAML"
echo "   • /tmp/span_migration_test_summary.txt - Detailed test summary"
echo ""
echo "The v1.0.10 → v1.2.0 migration process is working correctly!"
echo "Ready for production deployment!"
