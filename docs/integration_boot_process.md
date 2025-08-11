# SPAN Panel Integration Boot Process

## Overview

This document explains how the SPAN Panel integration boots up for existing installations, focusing on the core boot process and config entry handling before
synthetic sensors are involved.

## Home Assistant Integration Boot Sequence

When Home Assistant starts up, it follows this sequence for existing integrations:

1. **Integration Discovery**: Home Assistant scans for installed integrations
2. **Config Entry Loading**: For each integration, it loads existing config entries from storage
3. **Migration Check**: Calls `async_migrate_entry()` for each config entry if needed
4. **Setup Execution**: Calls `async_setup_entry()` for each config entry
5. **Platform Setup**: Sets up individual platforms (sensor, binary_sensor, etc.)

## SPAN Panel Boot Process for Existing Installations

### 1. Config Entry Discovery

Home Assistant finds existing SPAN Panel config entries in its storage and calls the integration's setup functions:

```python
# Home Assistant calls this for each existing config entry
await async_setup_entry(hass, config_entry)
```

### 2. Migration Check (if needed)

Before setup, Home Assistant checks if migration is needed:

```python
async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry for unique ID consistency."""
    _LOGGER.debug("Checking config entry version: %s", config_entry.version)

    if config_entry.version < CURRENT_CONFIG_VERSION:
        # Perform migration logic
        await migrate_unique_ids_for_consistency(hass, config_entry)
        config_entry.version = CURRENT_CONFIG_VERSION

    return True
```

**Current Issue**: This migration attempts to normalize unique IDs, which is unnecessary and potentially disruptive.

### 3. Core Setup Process (`async_setup_entry`)

The main setup function follows this sequence:

#### 3.1 Initial Configuration

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Configure external dependencies
    set_async_delay_func(ha_compatible_delay)
    ha_synthetic_sensors.configure_logging(integration_level)

    # Extract configuration from entry
    config = entry.data
    host = config[CONF_HOST]
    use_ssl_value = config.get(CONF_USE_SSL, False)

    # Get scan interval from options
    raw_scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds)
    scan_interval = int(float(raw_scan_interval))
```

#### 3.2 Simulation Mode Detection

```python
# Determine simulation config path if in simulation mode
simulation_mode = config.get("simulation_mode", False)
simulation_config_path = None
simulation_start_time = None

if simulation_mode:
    selected_config = config.get(CONF_SIMULATION_CONFIG, "simulation_config_32_circuit")
    simulation_config_path = os.path.join(current_dir, "simulation_configs", f"{selected_config}.yaml")
    simulation_start_time_str = config.get(CONF_SIMULATION_START_TIME)
```

#### 3.3 SPAN Panel API Client Creation

```python
span_panel = SpanPanel(
    host=config[CONF_HOST],
    access_token=config[CONF_ACCESS_TOKEN],
    options=Options(entry),
    use_ssl=use_ssl_value,
    scan_interval=scan_interval,
    simulation_mode=simulation_mode,
    simulation_config_path=simulation_config_path,
    simulation_start_time=simulation_start_time,
)
```

#### 3.4 API Connection Testing

```python
# Initialize the API client
await span_panel.api.setup()

# Test basic connection
test_success = await span_panel.api.ping()
if not test_success:
    raise ConnectionError("Failed to establish connection to SPAN Panel")

# Test authenticated connection if token exists
if span_panel.api.access_token:
    auth_test_success = await span_panel.api.ping_with_auth()
    if not auth_test_success:
        raise ConnectionError("Failed to authenticate with SPAN Panel")
```

#### 3.5 Coordinator Creation and Initialization

```python
coordinator = SpanPanelCoordinator(hass, span_panel, entry)
await coordinator.async_config_entry_first_refresh()

# Store coordinator in hass.data
hass.data.setdefault(DOMAIN, {})
hass.data[DOMAIN][entry.entry_id] = {
    COORDINATOR: coordinator,
    NAME: name,
}
```

#### 3.6 Device Registration

```python
# Generate smart device name
serial_number = span_panel.status.serial_number
is_simulator = any([
    "sim" in serial_number.lower(),
    serial_number.lower().startswith("myserial"),
    serial_number.lower().startswith("span-sim"),
    entry.data.get(CONF_SIMULATION_CONFIG) is not None,
])

base_name = "SPAN Simulator" if is_simulator else "SPAN Panel"
smart_device_name = base_name

# Ensure device is registered in device registry
await ensure_device_registered(hass, entry, span_panel, smart_device_name)
```

#### 3.7 Platform Setup

```python
# Set up all platforms (sensor, binary_sensor, select, switch)
await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
```

### 4. Platform-Specific Setup

Each platform (sensor, binary_sensor, etc.) has its own `async_setup_entry` function that gets called:

#### 4.1 Sensor Platform Setup

```python
# In sensor.py
async def async_setup_entry(hass, config_entry, async_add_entities):
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data[COORDINATOR]
    span_panel = coordinator.data

    # Create native sensors
    entities = []

    # Add panel data status sensors
    for description in PANEL_DATA_STATUS_SENSORS:
        entities.append(SpanPanelPanelStatus(coordinator, description, span_panel))

    # Add unmapped circuit sensors
    unmapped_circuits = [cid for cid in span_panel.circuits if cid.startswith("unmapped_tab_")]
    for circuit_id in unmapped_circuits:
        for unmapped_description in UNMAPPED_SENSORS:
            entities.append(SpanUnmappedCircuitSensor(coordinator, unmapped_description, span_panel, circuit_id))

    # Add hardware status sensors
    for description in STATUS_SENSORS:
        entities.append(SpanPanelStatus(coordinator, description, span_panel))

    # Add battery sensor if enabled
    battery_enabled = config_entry.options.get(BATTERY_ENABLE, False)
    if battery_enabled:
        entities.append(SpanPanelBattery(coordinator, BATTERY_SENSOR, span_panel))

    # Register entities
    async_add_entities(entities)

    # Set up synthetic sensors (if enabled)
    storage_manager = await setup_synthetic_configuration(hass, config_entry, coordinator)
    sensor_manager = await setup_synthetic_sensors(hass, config_entry, async_add_entities, coordinator, storage_manager)
```

#### 4.2 Other Platform Setups

Similar setup functions exist for:

- **binary_sensor.py**: Circuit state sensors
- **select.py**: Circuit selection controls
- **switch.py**: Circuit control switches

### 5. Update Listener Registration

```python
# Register update listener for configuration changes
entry.async_on_unload(entry.add_update_listener(update_listener))
```

### 6. Service Registration

```python
# Register export synthetic config service
if not hass.services.has_service(DOMAIN, "export_synthetic_config"):
    hass.services.async_register(
        DOMAIN,
        "export_synthetic_config",
        async_export_synthetic_config_service,
        schema=export_service_schema,
    )
```

## Key Data Flow

### Config Entry Data Structure

```python
# Config entry contains:
entry.data = {
    CONF_HOST: "192.168.1.100",
    CONF_ACCESS_TOKEN: "abc123...",
    CONF_USE_SSL: False,
    "simulation_mode": False,
    # ... other configuration
}

entry.options = {
    CONF_SCAN_INTERVAL: 30,
    USE_DEVICE_PREFIX: True,
    BATTERY_ENABLE: False,
    # ... other options
}
```

### Coordinator Data Structure

```python
# Coordinator provides access to:
coordinator.data = SpanPanel(
    status=SpanPanelStatus(...),
    circuits=dict[str, SpanPanelCircuit],
    data=SpanPanelData(...),
    # ... other panel data
)
```

### Hass Data Storage

```python
# Integration data stored in hass.data
hass.data[DOMAIN] = {
    entry.entry_id: {
        COORDINATOR: SpanPanelCoordinator,
        NAME: "SpanPanel",
        STORAGE_MANAGER: StorageManager,  # Added during synthetic setup
        SENSOR_SET: SensorSet,  # Added during synthetic setup
    }
}
```

## Boot Process Summary

1. **Migration**: Check and migrate config entry if needed (currently problematic)
2. **API Setup**: Create and test SPAN Panel API connection
3. **Coordinator**: Create data coordinator and perform initial refresh
4. **Device Registration**: Register device in device registry
5. **Platform Setup**: Set up all platforms (sensor, binary_sensor, select, switch)
6. **Synthetic Setup**: Set up synthetic sensors (if enabled)
7. **Service Registration**: Register integration services
8. **Update Listener**: Register configuration change handler

## Current Issues with Boot Process

1. **Unnecessary Migration**: The unique ID migration provides no benefit and risks breaking existing installations
2. **Migration Timing**: Migration happens before synthetic sensors are set up, but should generate YAML configurations
3. **Missing YAML Generation**: No mechanism to generate synthetic sensor YAML from existing entities

## Proposed Migration Strategy Integration

The migration should be modified to:

1. **Remove unique ID changes**: Don't modify existing unique IDs
2. **Generate YAML**: Create synthetic sensor YAML from existing entity registry data
3. **Preserve entity IDs**: Keep all existing entity IDs unchanged
4. **Version-aware handling**: Different behavior for pre-1.0.4 vs post-1.0.4 installations

This ensures existing installations boot up seamlessly while gaining synthetic sensor capabilities.
