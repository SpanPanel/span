#!/bin/bash

# Check if pre-commit is installed
if ! command -v pre-commit &> /dev/null; then
    echo "Error: pre-commit is not installed."
    echo "Please install pre-commit using: pip install pre-commit"
    exit 1
fi

# Install the pre-commit hooks
pre-commit install

echo "Git hooks installed successfully!"
