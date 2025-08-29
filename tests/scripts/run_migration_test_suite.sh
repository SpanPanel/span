#!/bin/bash

# SPAN Panel Migration Test Suite
# This script runs the complete migration test workflow for v1.0.4, v1.0.10, and legacy:
# 1. Generate migration test configuration
# 2. Run migration validation tests for all versions

set -e  # Exit on any error

echo "SPAN Panel Migration Test Suite"
echo "=================================="
echo ""

# Check if we're in the right directory
if [ ! -f "tests/test_migration_1_0_10_to_1_2_0.py" ] || [ ! -f "tests/test_migration_1_0_4_to_1_2_0.py" ] || [ ! -f "tests/test_migration_legacy_to_1_2_0.py" ] || [ ! -f "tests/test_migration_comprehensive.py" ]; then
    echo "Error: Must run from project root directory"
    echo "   Expected files: tests/test_migration_1_0_10_to_1_2_0.py, tests/test_migration_1_0_4_to_1_2_0.py, tests/test_migration_legacy_to_1_2_0.py, tests/test_migration_comprehensive.py"
    exit 1
fi

echo "Step 1: Running migration validation tests..."
echo "   This validates the migration logic against real registry data for all versions"
echo ""

# Test v1.0.10 migration
echo ""
echo "Testing v1.0.10 → v1.2.0 migration..."
echo "----------------------------------------"
if python tests/test_migration_1_0_10_to_1_2_0.py; then
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
if python tests/test_migration_1_0_4_to_1_2_0.py; then
    echo ""
    echo "v1.0.4 migration test completed successfully"
    echo ""
else
    echo ""
    echo "v1.0.4 migration test failed"
    echo "   Check the output above for details"
    exit 1
fi

# Test legacy migration
echo ""
echo "Testing Legacy → v1.2.0 migration..."
echo "----------------------------------------"
if python tests/test_migration_legacy_to_1_2_0.py; then
    echo ""
    echo "Legacy migration test completed successfully"
    echo ""
else
    echo ""
    echo "Legacy migration test failed"
    echo "   Check the output above for details"
    exit 1
fi

echo "MIGRATION TEST SUITE COMPLETED SUCCESSFULLY!"
echo "==============================================="
echo ""
# Test comprehensive migration
echo ""
echo "Testing Comprehensive Migration (ALL unique IDs)..."
echo "----------------------------------------"
if python tests/test_migration_comprehensive.py; then
    echo ""
    echo "Comprehensive migration test completed successfully"
    echo ""
else
    echo ""
    echo "Comprehensive migration test failed"
    echo "   Check the output above for details"
    exit 1
fi

echo "All migration processes (v1.0.4 → v1.2.0, v1.0.10 → v1.2.0, Legacy → v1.2.0, and Comprehensive) are working correctly!"
echo "Ready for production deployment!"
