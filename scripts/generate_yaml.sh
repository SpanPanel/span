#!/bin/bash

# Generate complete SPAN Panel YAML configuration
# This script generates the full sensor configuration using the integration's code path

set -e

echo "🚀 Generating complete SPAN Panel YAML configuration..."

# Get the script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# Check if virtual environment exists and activate it
if [ -d ".venv" ]; then
    echo "📦 Activating virtual environment..."
    source .venv/bin/activate
elif [ -d "venv" ]; then
    echo "📦 Activating virtual environment..."
    source venv/bin/activate
else
    echo "⚠️  No virtual environment found, using system Python"
fi

# Run the Python script
echo "🔧 Running YAML generation script..."
python scripts/generate_complete_config.py

# Check if generation was successful
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ YAML generation completed successfully!"
    echo "📁 Complete configuration: /tmp/span_simulator_complete_config.yaml"
    echo "📄 Configuration summary: /tmp/span_simulator_config_summary.txt"
    echo ""
    echo "📊 Quick summary:"
    if [ -f "/tmp/span_simulator_config_summary.txt" ]; then
        echo "   Sensor counts:"
        grep -A 3 "Sensor Counts:" /tmp/span_simulator_config_summary.txt | tail -3
    fi
else
    echo ""
    echo "❌ YAML generation failed!"
    exit 1
fi
