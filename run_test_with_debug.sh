#!/bin/bash

# Run test with comprehensive debug logging for synthetic sensors

echo "Running test with debug logging for synthetic sensors..."

# Set environment variables for debug logging
export PYTHONPATH="$PWD:$PYTHONPATH"

# Run pytest with verbose logging
poetry run python -m pytest \
    tests/test_basic_features.py::test_panel_level_sensors \
    -xvs \
    --tb=short \
    --log-cli-level=DEBUG \
    --log-cli-format="%(levelname)8s %(name)s:%(filename)s:%(lineno)d %(message)s" \
    --capture=no \
    2>&1 | grep -E "(SYNTHETIC|ha_synthetic|span_panel|ERROR|FAILED)"

echo "Test completed."
