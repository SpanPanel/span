#!/bin/bash
# Shared markdown formatting script for all projects
# Usage: ./scripts/fix-markdown.sh <workspace_root>

set -e

if [ $# -eq 0 ]; then
    echo "❌ Error: Workspace root path is required"
    echo "Usage: $0 <workspace_root>"
    exit 1
fi

WORKSPACE_ROOT="$1"
cd "$WORKSPACE_ROOT"
# If a Python venv activation script exists in repo root, source it (best-effort)
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate || true
fi

echo "🔧 Fixing markdown files in: $(pwd)"

# Check if npx is available
if ! command -v npx &> /dev/null; then
    echo "❌ Error: npx is not available"
    exit 1
fi

# Run Prettier for line wrapping
echo "📝 Step 1: Running Prettier..."
# First, explicitly reflow Markdown prose to a consistent width regardless of repo config quirks
npx prettier --ignore-path .prettierignore --parser markdown --print-width 160 --prose-wrap always \
  "*.md" "docs/**/*.md" "tests/**/*.md" "scripts/**/*.md" ".github/**/*.md" --write 2>/dev/null || true

# Then, apply repo-specific Prettier config for any additional rules (indentation, quotes, etc.)
if [ -f ".prettierrc" ]; then
    npx prettier --ignore-path .prettierignore --config .prettierrc "*.md" "docs/**/*.md" "tests/**/*.md" "scripts/**/*.md" ".github/**/*.md" --write 2>/dev/null || true
else
    npx prettier --ignore-path .prettierignore --print-width 160 --prose-wrap always --parser markdown "*.md" "docs/**/*.md" "tests/**/*.md" "scripts/**/*.md" ".github/**/*.md" --write 2>/dev/null || true
fi

# Run markdownlint for markdown-specific fixes
echo "📋 Step 2: Running markdownlint..."
if [ -f ".markdownlint-cli2.jsonc" ]; then
    npx markdownlint-cli2 --fix --config .markdownlint-cli2.jsonc "*.md" "docs/**/*.md" "tests/**/*.md" "scripts/**/*.md" ".github/**/*.md" 2>/dev/null || true
else
    npx markdownlint-cli2 --fix "*.md" "docs/**/*.md" "tests/**/*.md" "scripts/**/*.md" ".github/**/*.md" 2>/dev/null || true
fi

echo "🎉 Markdown formatting completed successfully!"
