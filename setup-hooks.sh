#!/bin/bash

# Ensure dependencies are installed first
if [[ ! -f ".deps-installed" ]] || [[ "pyproject.toml" -nt ".deps-installed" ]]; then
    echo "Installing/updating dependencies..."

    uv sync

    if [[ $? -ne 0 ]]; then
        echo "Failed to install dependencies. Please check the output above."
        exit 1
    fi
    touch .deps-installed
fi

# Install pre-commit hooks (only if not already installed)
if [[ ! -f ".git/hooks/pre-commit" ]]; then
    prek install
    echo "Git hooks installed successfully!"
fi
