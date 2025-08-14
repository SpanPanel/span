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
    """Migrate config entry for synthetic sensor YAML generation."""
    _LOGGER.error(
        "MIGRATION FUNCTION CALLED! Entry ID: %s, Version: %s, Target: %s",
        config_entry.entry_id,
        config_entry.version,
        CURRENT_CONFIG_VERSION,
    )

    if config_entry.version < CURRENT_CONFIG_VERSION:
        # Perform migration logic
        success = await migrate_config_entry_to_synthetic_sensors(hass, config_entry)
        if not success:
            return False

        # Update config entry version
        hass.config_entries.async_update_entry(
            config_entry,
            data=config_entry.data,
            options=config_entry.options,
            title=config_entry.title,
        )
        try:
            object.__setattr__(config_entry, "version", CURRENT_CONFIG_VERSION)
        except Exception:
            # Fallback to documented API
            hass.config_entries._async_update_entry(
                config_entry,
                {"version": CURRENT_CONFIG_VERSION},
            )
    return True
```

**Current Migration Strategy**: The migration normalizes unique IDs in the entity registry to helper format and sets a per-entry migration flag for YAML
generation during the first normal boot.

### 3. Core Setup Process (`async_setup_entry`)

The main setup function follows this sequence:

#### 3.1 Initial Configuration and Dependencies

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Span Panel from a config entry."""
    _LOGGER.debug("SETUP ENTRY CALLED! Entry ID: %s, Version: %s", entry.entry_id, entry.version)

    async def ha_compatible_delay(seconds: float) -> None:
        """HA-compatible delay function that works well with HA's event loop."""
        await asyncio.sleep(seconds)

    # Configure external dependencies
    set_async_delay_func(ha_compatible_delay)
    ha_synthetic_sensors.configure_logging(integration_level)

    # Extract configuration from entry
    config = entry.data
    host = config[CONF_HOST]
    use_ssl_value = config.get(CONF_USE_SSL, False)

    # Get scan interval from options with coercion and clamping
    raw_scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds)
    try:
        scan_interval = int(float(raw_scan_interval))
    except (TypeError, ValueError):
        scan_interval = int(DEFAULT_SCAN_INTERVAL.total_seconds())

    if scan_interval < 5:
        scan_interval = 5
```

#### 3.2 Simulation Mode Detection

```python
# Determine simulation config path if in simulation mode
simulation_mode = config.get("simulation_mode", False)
simulation_config_path = None
simulation_start_time = None

if simulation_mode:
    selected_config = config.get(CONF_SIMULATION_CONFIG, "simulation_config_32_circuit")
    current_dir = os.path.dirname(__file__)
    simulation_config_path = os.path.join(current_dir, "simulation_configs", f"{selected_config}.yaml")
    simulation_start_time_str = config.get(CONF_SIMULATION_START_TIME)
    if not simulation_start_time_str:
        simulation_start_time_str = entry.options.get(CONF_SIMULATION_START_TIME)

    if simulation_start_time_str:
        try:
            simulation_start_time = datetime.fromisoformat(simulation_start_time_str)
        except (ValueError, TypeError) as e:
            _LOGGER.warning("Invalid simulation start time format '%s': %s", simulation_start_time_str, e)
            simulation_start_time = None
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
    try:
        auth_test_success = await span_panel.api.ping_with_auth()
        if not auth_test_success:
            raise ConnectionError("Failed to authenticate with SPAN Panel")
    except SpanPanelAuthError as e:
        _LOGGER.error("Authentication error during setup: %s", e)
        raise ConnectionError(f"Authentication failed: {e}. Please reconfigure with a new access token.") from e
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

# Check existing config entries to avoid conflicts
existing_entries = hass.config_entries.async_entries(DOMAIN)
existing_titles = {
    e.title for e in existing_entries
    if e.title and e.title != serial_number and e.entry_id != entry.entry_id
}

# Find unique name
smart_device_name = base_name
counter = 2
while smart_device_name in existing_titles:
    smart_device_name = f"{base_name} {counter}"
    counter += 1

# Update config entry title if it's currently the serial number
if entry.title == serial_number:
    hass.config_entries.async_update_entry(entry, title=smart_device_name)

# Ensure device is registered BEFORE synthetic sensors are created
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

    # Check migration mode early to pass to all functions that need it
    migration_mode = _get_migration_mode(hass, config_entry)

    # Create native sensors
    entities = _create_native_sensors(coordinator, span_panel, config_entry)

    # Add native sensor entities
    async_add_entities(entities)

    # Enable unmapped tab entities if they were disabled
    _enable_unmapped_tab_entities(hass, entities)

    # Set up synthetic sensor configuration
    storage_manager = StorageManager(hass, DOMAIN, integration_domain=DOMAIN)
    await storage_manager.async_load()

    # Check if sensor sets already exist (from migration)
    sensor_sets = storage_manager.list_sensor_sets()

    if sensor_sets:
        # Use existing sensor configuration from migration
        device_name = config_entry.data.get("device_name", config_entry.title)
        synthetic_coord = SyntheticSensorCoordinator(hass, coordinator, device_name)
        _synthetic_coordinators[config_entry.entry_id] = synthetic_coord

        # Determine the correct sensor_set_id for THIS entry
        current_identifier = get_device_identifier_for_entry(coordinator, coordinator.data, device_name)
        current_sensor_set_id = construct_sensor_set_id(current_identifier)

        if not storage_manager.sensor_set_exists(current_sensor_set_id):
            storage_manager = await setup_synthetic_configuration(
                hass, config_entry, coordinator, migration_mode
            )

        # Initialize the synthetic coordinator configuration
        await synthetic_coord.setup_configuration(config_entry, migration_mode)
        synthetic_coord.sensor_set_id = current_sensor_set_id
        synthetic_coord.device_identifier = current_identifier
    else:
        # Fresh install - generate new configuration
        storage_manager = await setup_synthetic_configuration(
            hass, config_entry, coordinator, migration_mode
        )

    # Set up synthetic sensors
    sensor_manager = await async_setup_synthetic_sensors(
        hass=hass,
        config_entry=config_entry,
        async_add_entities=async_add_entities,
        coordinator=coordinator,
        storage_manager=storage_manager,
    )

    # Store managers and sensor set for potential reload functionality
    data["sensor_manager"] = sensor_manager
    data[STORAGE_MANAGER] = storage_manager
    data[SENSOR_SET] = sensor_set

    # Handle initial solar sensor setup if solar is enabled
    await _handle_initial_solar_setup(hass, config_entry, coordinator, sensor_set, migration_mode)

    # Clear migration flags if this was a migration boot
    _clear_migration_flags(hass, config_entry, migration_mode)

    # Force immediate coordinator refresh
    await coordinator.async_request_refresh()
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
    export_service_schema = vol.Schema({
        vol.Required("directory"): str,
        vol.Required("sensor_set_id"): str,
    })

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
    "migration_mode": True,  # Set during migration
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
        "sensor_manager": SensorManager,  # Added during synthetic setup
        "migration_mode": True,  # Set during migration
    }
}
```

## Boot Process Summary

1. **Migration**: Check and migrate config entry if needed (normalize unique IDs, set migration flag)
2. **API Setup**: Create and test SPAN Panel API connection
3. **Coordinator**: Create data coordinator and perform initial refresh
4. **Device Registration**: Register device in device registry with smart naming
5. **Platform Setup**: Set up all platforms (sensor, binary_sensor, select, switch)
6. **Synthetic Setup**: Set up synthetic sensors (using existing YAML from migration or generating new)
7. **Solar Setup**: Handle initial solar sensor setup if enabled
8. **Service Registration**: Register integration services
9. **Update Listener**: Register configuration change handler
10. **Migration Cleanup**: Clear migration flags after successful setup

## Current Migration Strategy

The current migration approach follows the Version 2 Migration Strategy:

1. **Unique ID Normalization**: Normalize existing sensor unique IDs in the entity registry to helper format
2. **Migration Flag**: Set a per-entry migration flag in both hass.data and config entry options
3. **YAML Generation**: During the first normal boot after migration, generate YAML configurations using registry lookups to preserve entity IDs
4. **Solar Handling**: Perform solar CRUD operations during first normal boot if solar is enabled
5. **Flag Cleanup**: Clear migration flags after successful setup

This ensures existing installations boot up seamlessly while gaining synthetic sensor capabilities with preserved entity IDs and configurations.
