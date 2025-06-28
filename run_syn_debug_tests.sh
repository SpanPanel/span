#!/bin/bash

poetry run pytest \
  tests/test_basic_features.py::test_integration_setup_and_unload \
  tests/test_basic_features.py::test_circuit_sensor_creation_and_values \
  -v -s \
  --log-cli-level=DEBUG \
  --log-cli-format="%(asctime)s %(levelname)s %(name)s %(message)s" \
  --log-cli-date-format="%Y-%m-%d %H:%M:%S"
