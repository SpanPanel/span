#!/bin/bash

echo "Setting up Git hooks..."
git config core.hooksPath .hooks
chmod +x .hooks/pre-commit
echo "Hooks configured successfully!"

