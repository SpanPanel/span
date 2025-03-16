#!/bin/bash

# Ensures the script runs in the correct Python environment
# Handles pyenv/virtualenv/poetry activation if needed

# Find the project root directory (where .git is)
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT" || exit 1

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
exec "$@"
