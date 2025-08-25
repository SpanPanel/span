# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2025-01-XX

⚠️⚠️ **MAJOR UPGRADE WARNING** ⚠️⚠️

**Before upgrading to version 1.2.0, please backup your Home Assistant configuration and database.** This version introduces significant architectural changes.
While we've implemented migration logic to preserve your existing entities and automations, it's always recommended to have a backup before major upgrades.

### 🚀 Features

#### Synthetic Sensors Engine

- **Synthetic Sensors**: Integration now leverages a [synthetic sensor engine](https://github.com/LegoTypes/ha-synthetic-sensors) that allows features beyond
  basic sensors
- **Grace Period Algorithm**: Developed by @sargonas, keeps statistics from reporting wild spikes and gaps during intermittent outages by providing the previous
  known good value for a grace period (developed by @sargonas)
- **Voltage and Amperage Attributes**: Added attributes for voltage and amperage to each power sensor for threshold notifications
- **Panel Tabs Attributes**: Added attribute to each sensor to see the specific panel tabs (spaces) associated with sensor
- **Calculation Visibility**: You can see how the sensor is calculated for grace periods and net energy
- **Net Energy Sensors**: New net energy sensors calculate `consumed energy - produced energy` for circuits, panels, and tab-based solar installations,
  providing real-time net energy consumption/generation data
- **Future Attribute Editor**: Every attribute will soon be capable of being mathematically formulated and tweaked by the user
- **Sensor Configuration Export**: You can export the sensor configuration (import not supported yet)

#### OpenAPI Support

- **OpenAPI Specification**: Integration now uses the OpenAPI specification provided by the SPAN panel for reliable foundation
- **Future Interface Changes**: Provides reliable foundation for future interface changes

#### Simulation Support

- **Virtual Panel Templates**: Support for adding configuration entries for virtual panels based on templates that produce typical power and energy
- **Import/Export Profiles**: You can import or export the simulation profile and even clone your existing panel
- **Custom Profile Building**: See the simulation [guide](https://github.com/SpanPanel/span-panel-api/blob/main/docs/simulation.md) on how to build your own
  profile

#### Network Configuration

- **Configurable Timeouts and Retries**: Connection options for different network environments
  - **Timeout Settings**: Customize connection and request timeouts for slower networks or distant panels
  - **Retry Configuration**: Configure automatic retry attempts for transient network issues
- **SSL/TLS Support**: Added SSL support for remote panel access scenarios
  - **Local Access**: Standard HTTP connection for panels on local network
  - **Remote Access**: HTTPS support for accessing panels through secure proxies

#### Circuit Management

- **Circuit Name Sync**: Automatic friendly name updates when circuits are renamed in the SPAN panel
- **Custom Name Preservation**: Custom entity friendly names in Home Assistant are preserved and won't be overwritten during sync
- **Re-enable Sync**: Clear custom name in Home Assistant to re-enable sync for customized entities

#### Entity Naming Patterns

- **Configurable Entity Naming**: Provides configurable entity naming patterns upon initial setup
- **Friendly Names Pattern**: Entity IDs use descriptive circuit names (e.g., `sensor.span_panel_kitchen_outlets_power`)
- **Circuit Numbers Pattern**: Entity IDs use stable circuit numbers (e.g., `sensor.span_panel_circuit_15_power`)
- **Pattern Selection**: Choose between friendly names (recommended for new installations) or circuit numbers (stable entity IDs)

### 🔧 Technical Improvements

#### Migration Support

- **Schema Migration**: Latest version changes the underlying configuration schema and migrates unique keys
- **Entity ID Preservation**: Existing entity IDs are kept intact during migration
- **Legacy Support**: Pre-1.0.4 installations can only migrate forward to friendly names with device prefixes

#### Solar Configuration

- **Tab-Based Solar**: Solar configuration for solar directly connected to panel tabs
- **Four Solar Sensors**: Power, produced energy, consumed energy, and net energy sensors
- **Dual Circuit Support**: Support for single and dual circuit solar configurations
- **Dynamic Entity Naming**: Entity naming follows configured naming pattern (circuit numbers or friendly names)

### ⚠️ Breaking Changes

- **Major Architectural Changes**: Version 1.2.0 introduces significant architectural changes
- **Backup Required**: Users must backup Home Assistant configuration and database before upgrading
- **Migration Required**: Existing installations require migration to new schema

### 📝 Documentation

- **Updated README**: Comprehensive documentation of new features and migration process
- **Simulation Guide**: Documentation for building custom simulation profiles
- **Troubleshooting Section**: Enhanced troubleshooting information

### 🔄 HACS Upgrade Process

- **Backup Instructions**: Clear backup requirements before upgrade
- **Change Review**: Instructions to review changes in README
- **Automation Verification**: Check automations for correct entity ID references
- **Quiet Period Updates**: Recommendations for update timing
- **Issue Recovery**: Instructions for backup restoration and troubleshooting

### 👥 Acknowledgments

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
