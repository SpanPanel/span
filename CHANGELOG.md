# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.8] - 2025-12-10

### 🔧 Technical Improvements

- **Fix total increasing sensors** against receiving data that is less than previously reported
- **Fix feedthroughh sensor types** now set to TOTAL instead of TOTAL_INCREASING

## [1.2.7] - 2025-11-29

### 🔧 Technical Improvements

- **Offline Listener Fix**: Fixed simulation listener to prevent being called when not in simulation mode
- **Grace Period Restoration**: Fixed grace period algorithm to properly restore previous good values from Home Assistant statistics on
  restart, ensuring energy sensors report accurately after system restarts
- **CI/CD Dependencies**: Updated GitHub Actions checkout action to version 6

## [1.2.6] - 2025-09-XX

### 🔧 Technical Improvements

- **Panel Level Net Energy**: Add net energy sensors for main meter and feed-through (consumed - produced)
- **Net Energy Config Options**: Added separate config options to enable/disable panel, circuit, leg-based net energy. Disabling circuit net energy can help
  resource constrained installations since the sensors are not created or updated
- **Circuit Naming Logic**: Fixed logic for circuit-naming patterns to ensure proper entity ID generation and panel prefixes (fresh installs only)
- **Entity ID naming Choices**: Restored the ability to change entity ID naming patterns in live panels (circuit tab-based sensors only, not panel)
- **Panel Friendly Name Sync**: Fixed regression in panel circuit name synchronization. A new install will sync all friendly names once on the first refresh and
  anytime a user changes a name in the SPAN App changes the name in the mobile/SPAN App.
- **API Optimization**: Removed unnecessary signal updates to improve performance and reduce overhead
- **API Dependencies**: Updated span-panel-api OpenAPI package to version 1.1.13 to remove the cache
- **Resolve Cache Config Entry Defect**: Fixed an issue where a 1.2.5 config entry could attempt to set up a cache window in the underlying OpenAPI library that
  was invalid

## [1.2.5] - 2025-09-XX

### 🔧 Technical Improvements

- **Circuit Based Naming**: Circuit based entity_id naming was not using both tabs in the name. Existing entity_id's are unchnaged except fresh installs.
- **Switches and Selects Naming**: were creating proper ID's but not looking up migration names in 1.2.4

## [1.2.4] - 2025-09-XX

### 🔧 Technical Improvements

- **Performance**: Revert to native sensors (non-synthetic) to avoid calculation engine for simple math. Features like net energy, OpenAPI, simulation are still
  present. We may reintroduce the synthetic engine later in a modified form to allow users to add attributes, etc.
- **Fix sensor circuit-based naming**: For new installations with circuit naming provide consistent behavior where all circuits, other than panel have circuit
  names related to the tab (120V) or tabs (240V). We do not modify entity ID's so if an installation had faulty names from a previous release those must be
  renamed manually
- **Fix Faulty Legacy Single Panel Config**: Provided a repair for a pre-1.0.4 upgraded release where the config entry was missing the device unique ID (serial
  number) causing the new migration for normalized unique keys to fail. This repair only works for single panel installs because we derive the serial number
  from the entities and if more than one serial number is found we cannot determine which config the serial number would match.
- **Fixed Unmapped Tab Behavior for Offline Panel**: Unmapped tab sensors reported erroneous values when the panel was offline

## [1.2.3] - 2025-08-XX Rescinded for performance regression

## [1.2.2] - 2025-06-XX

### Major Upgrade

**Before upgrading to version 1.2.3, please backup your Home Assistant configuration and database.** This version introduces some architectural changes. While
we've implemented migration logic to preserve your existing entities and automations, it's always recommended to have a backup before major upgrades.

### 🚀 Features

- **Grace Period Algorithm**: Developed by @sargonas, keeps statistics from reporting wild spikes and gaps during intermittent outages by providing the previous
  known good value for a grace period
- **Voltage and Amperage Attributes**: Added attributes for voltage and amperage to each power sensor for threshold automations
- **Panel Tabs Attributes**: Added attribute to each sensor to see the specific panel tabs (spaces) associated with sensor
- **Unmapped Tab Sensors**: Added hidden circuits for tabs that are no part of a circuit reported directly by the panel. The user may make these tabs sensors
  visisble.
- **Panel Offline Sensor**: Added a sensor that indicates whether the panel is offline (cannot return data to the integration)
- **State Visibility**: Attributes show you the formula used in the sensor calculation for grace periods and net energy
- **Net Energy Sensors**: New net energy sensors calculate `consumed energy - produced energy` for circuits, panels, and tab-based solar installations,
  providing real-time net energy consumption/generation data
- **Panel Simulation**: You can clone your own panel or set up a simulation for energy usage based on predefined patterns. You can also take the panel offline
  to see how the grace periods for energy respond. We may extend this feature in order to allow modeling of energy usage or integration with other sensors or
  utilities.

### OpenAPI Support

- **OpenAPI Specification**: Integration now uses the OpenAPI specification provided by the SPAN panel for reliable foundation
- **Future Interface Changes**: Provides reliable foundation for future interface changes

### Simulation Support

- **Virtual Panel Templates**: Support for adding configuration entries for virtual panels based on templates that produce typical power and energy
- **Import/Export Profiles**: You can import or export the simulation profile and even clone your existing panel
- **Custom Profile Building**: See the simulation [guide](https://github.com/SpanPanel/span-panel-api/blob/main/docs/simulation.md) on how to build your own
  profile

### Network Configuration

- **Configurable Timeouts and Retries**: Connection options for different network environments
  - **Timeout Settings**: Customize connection and request timeouts for slower networks
  - **Retry Configuration**: Configure automatic retry attempts for transient network issues
- **SSL/TLS Support**: Added SSL support for remote panel access scenarios
  - **Local Access**: Standard HTTP connection for panels on local network
  - **Remote Access**: HTTPS support for accessing panels through secure proxies

### Circuit Management

- **Circuit Name Sync**: Automatic friendly name updates when circuits are renamed in the SPAN panel
- **Custom Name Preservation**: Custom entity friendly names in Home Assistant are preserved and won't be overwritten during sync
- **Re-enable Sync**: Clear custom name in Home Assistant to re-enable sync for customized entities

### Entity Naming Patterns

- **Configurable Entity Naming**: Provides configurable entity naming patterns upon initial setup
- **Friendly Names Pattern**: Entity IDs use descriptive circuit names (e.g., `sensor.span_panel_kitchen_outlets_power`)
- **Circuit Numbers Pattern**: Entity IDs use stable circuit numbers (e.g., `sensor.span_panel_circuit_15_power`)
- **Pattern Selection**: Choose between friendly names (recommended for new installations) or circuit numbers (stable entity IDs)

### 🔧 Technical Improvements

#### Migration Support

- **Legacy Support**: Pre-1.0.4 installations can only migrate forward to friendly names with device prefixes

### ⚠️ Breaking Changes

- **Major Architectural Changes**: Version 1.2.3 introduces significant architectural changes
- **Backup Required**: Users must backup Home Assistant configuration and database before upgrading
- **Migration Required**: Existing installations require migration to new schema

### 📝 Documentation

- **Simulation Guide**: Documentation for building custom simulation profiles
- **Troubleshooting Section**: Enhanced troubleshooting information

### 🔄 HACS Upgrade Process

This integration should handle migrating your entities seamlessly. Any entity ID's or names should be retained. We do migrate all the unique keys by properly
renaming these in the entity registry so the user should not see any difference.

- **Backup Instructions**: Check backup requirements before upgrade
- **Automation Verification**: Check automations for correct entity ID references

### 👥 Acknowledgments

- **@cayossarian**: Developed the synthetic engine, OpenAPI package, simulator
- **@sargonas**: Researched and developed the grace period algorithm that keeps statistics from reporting wild spikes and gaps during intermittent outages

---

## [1.1.0] - Previous Version

### 🚀 Features

- Basic SPAN Panel integration
- Circuit monitoring and control
- Power and energy sensors
- Panel status monitoring

### 🔧 Technical

- Initial integration release
- Basic API communication
- Entity creation and management

---

## [1.0.4] - Legacy Version

### 🔧 Technical

- Legacy entity naming support
- Device prefix requirements for friendly names
- Pre-migration schema support
