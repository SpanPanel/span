#!/bin/bash

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Go to the project root
cd "$SCRIPT_DIR/.." || exit

# Source the existing run-in-env.sh to activate the virtual environment
source "$SCRIPT_DIR/run-in-env.sh"

# Run mypy with explicit module paths and settings for Home Assistant
if [ $# -eq 0 ]; then
  # If no files were passed, check the entire directory
  cd custom_components && python -m mypy \
    --follow-imports=silent \
    --ignore-missing-imports \
    span_panel
else
  # If files were passed, check those specific files
  cd custom_components && python -m mypy \
    --follow-imports=silent \
    --ignore-missing-imports \
    $(echo "$@" | sed 's|custom_components/||g')
fi
