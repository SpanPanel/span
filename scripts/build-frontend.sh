#!/usr/bin/env bash
# Build the span-card frontend and copy dist files into the integration.
#
# Usage:
#   ./scripts/build-frontend.sh /path/to/span-card
#   SPAN_CARD_DIR=/path/to/span-card ./scripts/build-frontend.sh
#
# Set SPAN_CARD_DIR in your .envrc (direnv) so you don't have to pass it every time.
#
# After running, the updated JS files are in custom_components/span_panel/frontend/dist/.
# Stage and commit them normally — no submodule dance required.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST_DIR="$REPO_ROOT/custom_components/span_panel/frontend/dist"

# Resolve span-card repo location (argument takes precedence over env var)
CARD_DIR="${1:-${SPAN_CARD_DIR:-}}"

if [ -z "$CARD_DIR" ]; then
  echo "Error: span-card repo path not specified."
  echo ""
  echo "Either:"
  echo "  1. Pass the path:       ./scripts/build-frontend.sh /path/to/span-card"
  echo "  2. Set env var:         export SPAN_CARD_DIR=/path/to/span-card"
  echo "  3. Add to .envrc:       echo 'export SPAN_CARD_DIR=/path/to/span-card' >> .envrc"
  exit 1
fi

CARD_DIR="$(cd "$CARD_DIR" 2>/dev/null && pwd)" || {
  echo "Error: span-card repo not found at: $CARD_DIR"
  exit 1
}

echo "span-card repo: $CARD_DIR"
echo "Destination:    $DEST_DIR"

# Install deps if needed
if [ ! -d "$CARD_DIR/node_modules" ]; then
  echo "Installing dependencies..."
  (cd "$CARD_DIR" && npm install)
fi

# Build
echo "Building..."
(cd "$CARD_DIR" && npm run build)

# Copy
mkdir -p "$DEST_DIR"
cp "$CARD_DIR/dist/span-panel.js" "$DEST_DIR/"
cp "$CARD_DIR/dist/span-panel-card.js" "$DEST_DIR/"

echo ""
echo "Done. Files updated:"
ls -la "$DEST_DIR"
echo ""
echo "Next: git add custom_components/span_panel/frontend/dist/ && git commit"
