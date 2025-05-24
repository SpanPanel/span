#!/bin/bash

# Ensures the script runs in the correct Python environment
# Handles pyenv/virtualenv/poetry activation if needed

# Find the project root directory (where .git is)
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT" || exit 1

# Load environment variables from .env if it exists
if [ -f ".env" ]; then
  # shellcheck disable=SC1091
  source .env
fi

# Try to detect and activate the virtual environment
VENV_PATHS=(
  ".venv"
  "venv"
  ".env"
  "env"
  "$(poetry env info --path 2>/dev/null)" # Try to get Poetry's venv path
)

for venv_path in "${VENV_PATHS[@]}"; do
  if [ -n "$venv_path" ] && [ -f "$venv_path/bin/activate" ]; then
    # shellcheck disable=SC1090
    source "$venv_path/bin/activate"
    echo "Activated virtual environment at $venv_path"
    break
  fi
done

# If poetry is available, ensure dependencies
if command -v poetry &> /dev/null && [ -f "pyproject.toml" ]; then
  # Check if pylint is missing
  if ! command -v pylint &> /dev/null; then
    echo "pylint not found, installing dependencies with poetry..."
    poetry install --only dev
  fi
fi

# Execute the requested command
if [[ $# -gt 0 && $1 =~ \.py$ ]]; then
  # If first arg is a .py file, run mypy on the files
  python -m mypy --follow-imports=silent --ignore-missing-imports "$@"
else
  # Otherwise run the command normally
  "$@"
fi
