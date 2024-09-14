#!/bin/bash

if [ -z "${HA_CORE_PATH}" ]; then
    echo "HA_CORE_PATH is not set. Please set it to your Home Assistant core directory."
    echo "Example: export HA_CORE_PATH=/path/to/your/homeassistant/core"
    exit 1
else
    echo "Using Home Assistant core from: ${HA_CORE_PATH}"
fi

# Add the HA_CORE_PATH to PYTHONPATH
export PYTHONPATH="${HA_CORE_PATH}:${PYTHONPATH}"
