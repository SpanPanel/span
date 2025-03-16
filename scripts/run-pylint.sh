#!/bin/bash

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Go to the project root
cd "$SCRIPT_DIR/.." || exit

# Source the existing run-in-env.sh to activate the virtual environment
source "$SCRIPT_DIR/run-in-env.sh"

# Run pylint with specific settings
python -m pylint custom_components "$@" --recursive=true
