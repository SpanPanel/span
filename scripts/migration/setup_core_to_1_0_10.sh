#!/bin/bash
# If your core directory is up one from the root it willl copy the 1.0.10 migration storage to the core config directory
echo "Shutdown HA First"
read -p "Press any key to continue..."
echo "Copying 1.0.10 migration storage to core config directory"

# Check if core/config directory exists
if [ ! -d "../core/config" ]; then
    echo "Error: ../core/config directory does not exist"
    exit 1
fi

rm -rf ../core/config/.storage/core.entity_registry && rm -rf ../core/config/.storage/core.device_registry && rm -rf ../core/config/.storage/core.config_entries
cp tests/migration_storage/1_0_10/* ../core/config/.storage
