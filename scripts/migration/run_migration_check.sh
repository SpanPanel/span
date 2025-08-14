#!/bin/bash

# Default paths relative to workspace root
DEFAULT_ENTITY_REG="tests/migration_storage/1_0_10/core.entity_registry"
DEFAULT_YAML="span_panel_sensor_config.yaml"

# Check if entity registry path is provided, otherwise use default
if [ $# -eq 0 ]; then
    ENTITY_REG_PATH="$DEFAULT_ENTITY_REG"
    YAML_PATH=""
    echo "Using default entity registry: $ENTITY_REG_PATH"
elif [ $# -eq 1 ]; then
    ENTITY_REG_PATH="$1"
    YAML_PATH=""
elif [ $# -eq 2 ]; then
    ENTITY_REG_PATH="$1"
    YAML_PATH="$2"
else
    echo "Usage: $0 [entity_registry_path] [yaml_config_path]"
    echo "Examples:"
    echo "  $0                                    # Use default migration storage"
    echo "  $0 ../core/config/.storage/core.entity_registry"
    echo "  $0 ../core/config/.storage/core.entity_registry ../span_panel_sensor_config.yaml"
    exit 1
fi

# Check if entity registry file exists
if [ ! -f "$ENTITY_REG_PATH" ]; then
    echo "Error: Entity registry file not found: $ENTITY_REG_PATH"
    exit 1
fi

# Build the command
CMD="python3 scripts/migration/check_migration_map.py --entity-reg \"$ENTITY_REG_PATH\""

# Add YAML path if provided
if [ -n "$YAML_PATH" ]; then
    if [ ! -f "$YAML_PATH" ]; then
        echo "Warning: YAML config file not found: $YAML_PATH"
    else
        CMD="$CMD --yaml \"$YAML_PATH\""
    fi
fi

echo "Running migration check..."
echo "Command: $CMD"
echo ""

# Run the migration checker
eval $CMD
