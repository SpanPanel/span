#!/bin/bash

# SPAN Panel Migration Test Suite
# This script runs the complete migration test workflow for both v1.0.4 and v1.0.10:
# 1. Generate migration test configuration
# 2. Run migration validation tests for both versions

set -e  # Exit on any error

echo "SPAN Panel Migration Test Suite"
echo "=================================="
echo ""

# Check if we're in the right directory
if [ ! -f "tests/test_migration_1_0_10_to_1_2_0.py" ] || [ ! -f "tests/test_migration_1_0_4_to_1_2_0.py" ]; then
    echo "Error: Must run from project root directory"
    echo "   Expected files: tests/test_migration_1_0_10_to_1_2_0.py, tests/test_migration_1_0_4_to_1_2_0.py"
    exit 1
fi

echo "Step 1: Running migration validation tests..."
echo "   This validates the migration logic against real registry data for both versions"
echo ""

# Test v1.0.10 migration
echo ""
echo "Testing v1.0.10 → v1.2.0 migration..."
echo "----------------------------------------"
if python3 -m pytest tests/test_migration_1_0_10_to_1_2_0.py -v; then
    echo ""
    echo "v1.0.10 migration test completed successfully"
    echo ""
else
    echo ""
    echo "v1.0.10 migration test failed"
    echo "   Check the output above for details"
    exit 1
fi

# Test v1.0.4 migration
echo ""
echo "Testing v1.0.4 → v1.2.0 migration..."
echo "----------------------------------------"
if python3 -m pytest tests/test_migration_1_0_4_to_1_2_0.py -v; then
    echo ""
    echo "v1.0.4 migration test completed successfully"
    echo ""
else
    echo ""
    echo "v1.0.4 migration test failed"
    echo "   Check the output above for details"
    exit 1
fi

echo "MIGRATION TEST SUITE COMPLETED SUCCESSFULLY!"
echo "==============================================="
echo ""
echo "Both v1.0.4 → v1.2.0 and v1.0.10 → v1.2.0 migration processes are working correctly!"
echo "Ready for production deployment!"
