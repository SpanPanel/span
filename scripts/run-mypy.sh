#!/bin/bash

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Go to the project root
cd "$SCRIPT_DIR/.." || exit

# Source the existing run-in-env.sh to activate the virtual environment
source "$SCRIPT_DIR/run-in-env.sh"

# Run mypy with explicit module paths and settings for Home Assistant
python -m mypy \
  --namespace-packages \
  --explicit-package-bases \
  --follow-imports=silent \
  --ignore-missing-imports \
  custom_components
