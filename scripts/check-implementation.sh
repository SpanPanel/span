#!/bin/bash
# Check implementation code quality (ruff + mypy) for custom_components only

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMPLEMENTATION_DIR="$PROJECT_ROOT/custom_components/span_panel"

echo -e "${YELLOW}Checking implementation code quality...${NC}"
echo "Implementation directory: $IMPLEMENTATION_DIR"
echo

# Check if implementation directory exists
if [ ! -d "$IMPLEMENTATION_DIR" ]; then
    echo -e "${RED}Error: Implementation directory not found: $IMPLEMENTATION_DIR${NC}"
    exit 1
fi

# Change to project root for correct environment
cd "$PROJECT_ROOT"

# Run ruff check on implementation only
echo -e "${YELLOW}Running ruff check on implementation...${NC}"
if "$SCRIPT_DIR/run-in-env.sh" ruff check "$IMPLEMENTATION_DIR"; then
    echo -e "${GREEN}✓ Ruff check passed${NC}"
else
    echo -e "${RED}✗ Ruff check failed${NC}"
    exit 1
fi

echo

# Run mypy on implementation only
echo -e "${YELLOW}Running mypy check on implementation...${NC}"
if "$SCRIPT_DIR/run-in-env.sh" mypy "$IMPLEMENTATION_DIR"; then
    echo -e "${GREEN}✓ MyPy check passed${NC}"
else
    echo -e "${RED}✗ MyPy check failed${NC}"
    exit 1
fi

echo
echo -e "${GREEN}All implementation checks passed!${NC}"
