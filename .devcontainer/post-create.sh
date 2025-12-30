#!/bin/bash
# Post-create setup script for devcontainer
# This script runs automatically when the devcontainer is created

set -e

echo "=== Setting up devcontainer ==="

# Fix DNS order to prioritize Tailscale MagicDNS
# (Also runs via postStartCommand on every container start)
echo "Checking DNS configuration..."
bash "$(dirname "$0")/fix-dns-order.sh"

# Compute repository root (parent of .devcontainer) so script works
# regardless of the local workspace folder name.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing Poetry..."
pip install --user poetry

# Add ~/.local/bin to PATH for this session (where pip --user installs binaries)
export PATH="$HOME/.local/bin:$PATH"

# Configure Poetry to install packages system-wide (no virtualenv)
# The container itself provides isolation, so a venv is redundant
poetry config virtualenvs.create false

echo "Setting up Python environment... (repo root: $REPO_ROOT)"
cd "$REPO_ROOT"

# Clone span-panel-api dependency (sibling repo required by pyproject.toml)
if [ ! -d "/workspaces/span-panel-api" ]; then
    echo "Cloning span-panel-api dependency..."
    sudo mkdir -p /workspaces/span-panel-api
    sudo chown vscode:vscode /workspaces/span-panel-api
    git clone https://github.com/SpanPanel/span-panel-api.git /workspaces/span-panel-api
fi

# Git hooks (this also runs poetry install)
echo "Installing git hooks..."
if [ -x "$REPO_ROOT/setup-hooks.sh" ] || [ -f "$REPO_ROOT/setup-hooks.sh" ]; then
    bash "$REPO_ROOT/setup-hooks.sh"
else
    echo "Warning: setup-hooks.sh not found; running poetry install directly."
    poetry install
fi

echo "=== Devcontainer setup complete ==="
