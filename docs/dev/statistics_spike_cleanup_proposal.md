# Proposal: Statistics Spike Cleanup Service

## Status

**Implemented** - See PR for details

## Problem Statement

When the SPAN panel undergoes a firmware update, it sometimes resets or loses energy counter data, causing:

1. **Negative Spikes in Energy Dashboard**
   - Panel value drops from 8,913,289 Wh to 8,551,863 Wh
   - HA statistics calculates: `-361,426 Wh` consumption
   - Massive negative spike appears in Energy Dashboard charts

2. **Current User Workaround is Tedious**
   - Developer Tools → Statistics
   - Find each affected entity
   - Locate problematic timestamp
   - Delete or adjust entry
   - Repeat for 20+ circuits
   - **Takes 20+ minutes per firmware update**

## Proposed Solution

### Statistics Spike Cleanup Service

Provide a service that **detects and removes** negative energy spikes from Home Assistant's statistics database.

**Key Principle:** Simple tool to cleanup spikes. User triggers when needed (e.g., after firmware update). No automation, no history tracking - KISS.

## Architecture

### Service: `span_panel.cleanup_energy_spikes`

```yaml
service: span_panel.cleanup_energy_spikes
data:
  # Optional: target specific entities (omit to process all SPAN energy sensors)
  entity_id:
    - sensor.span_panel_solar_produced_energy
    - sensor.span_panel_main_meter_consumed_energy
  # Optional: how many days in past to scan
  days_back: 1 # Default: 1 day (last 24 hours)
  # Optional: dry run mode - defaults to true for safety
  dry_run: true # Set false to actually delete entries
```

### Detection Algorithm

The service detects **firmware reset spikes** using the main meter as an indicator, then cleans up all affected sensors.

**Simple Rule:**

1. Find timestamps where main meter decreased (firmware reset)
2. Delete ALL SPAN energy sensor entries at those timestamps
3. Firmware reset affects all sensors simultaneously

**Why Main Meter is the Indicator:**

- Main meter always affected by firmware reset
- Single sensor to check vs. 32+ circuits
- Timestamp of main meter reset = timestamp for all sensors
- No need to check each sensor individually

```python
async def cleanup_energy_spikes(
    days_back: int = 1,
    dry_run: bool = False
) -> dict:
    """Detect and remove firmware reset spikes from all SPAN energy sensors.

    Uses main meter to detect reset timestamps, then deletes all
    SPAN TOTAL_INCREASING sensor entries at those timestamps.

    Args:
        days_back: How many days to scan (default: 1)
        dry_run: Preview mode without making changes

    Returns summary of spikes found and removed.
    """

    # 1. Get main meter statistics
    # 2. Find timestamps where main meter decreased (delta < 0)
    # 3. Get all SPAN energy sensors (TOTAL_INCREASING)
    # 4. Delete entries for ALL sensors at reset timestamps
    # 5. Return summary report
```

### Implementation Steps

1. **Query Main Meter Statistics**

   ```python
   from homeassistant.components.recorder.statistics import (
       get_instance,
       statistics_during_period,
   )

   # Get main meter statistics
   main_meter_entity = "sensor.span_panel_main_meter_consumed_energy"
   stats = await statistics_during_period(
       hass, start_time, end_time, {main_meter_entity}, "hour"
   )
   ```

2. **Detect Reset Timestamps**

   ```python
   def find_reset_timestamps(stats: list) -> list[datetime]:
       """Find timestamps where main meter decreased.

       These timestamps indicate firmware resets affecting all sensors.
       """
       reset_timestamps = []

       for i in range(1, len(stats)):
           delta = stats[i]['sum'] - stats[i-1]['sum']

           # Any negative delta = firmware reset
           if delta < 0:
               reset_timestamps.append(stats[i]['start'])

       return reset_timestamps
   ```

3. **Get All SPAN Energy Sensors**

   ```python
   # Get all SPAN energy sensors with TOTAL_INCREASING state class
   span_energy_sensors = [
       entity_id
       for entity_id in hass.states.async_entity_ids('sensor')
       if entity_id.startswith('sensor.span_panel_')
       and hass.states.get(entity_id).attributes.get('state_class') == 'total_increasing'
   ]
   ```

4. **Delete Entries at Reset Timestamps**

   ```python
   from homeassistant.components.recorder import get_instance
   from homeassistant.components.recorder.models import StatisticsShortTerm

   # Delete statistics entries
   recorder = get_instance(hass)
   with recorder.session_scope() as session:
       for spike in spikes:
           session.query(StatisticsShortTerm).filter(
               StatisticsShortTerm.metadata_id == metadata_id,
               StatisticsShortTerm.start == spike['timestamp']
           ).delete()
   ```

5. **Delete Entries at Reset Timestamps**

   ```python
   from homeassistant.components.recorder import get_instance
   from homeassistant.components.recorder.models import StatisticsShortTerm

   # Delete statistics entries for ALL sensors at reset timestamps
   recorder = get_instance(hass)
   with recorder.session_scope() as session:
       for timestamp in reset_timestamps:
           for entity_id in span_energy_sensors:
               # Get metadata_id for entity
               metadata_id = get_metadata_id(session, entity_id)

               # Delete entry at this timestamp
               session.query(StatisticsShortTerm).filter(
                   StatisticsShortTerm.metadata_id == metadata_id,
                   StatisticsShortTerm.start == timestamp
               ).delete()
   ```

6. **Return Summary Report**

   ```python
   return {
       'reset_timestamps': reset_timestamps,
       'entities_affected': len(span_energy_sensors),
       'entries_deleted': total_deleted,
       'dry_run': dry_run
   }
   ```

## User Experience

### Manual Trigger After Firmware Update

```yaml
service: span_panel.cleanup_energy_spikes
# No parameters = scan all SPAN energy entities for last 24 hours
```

### Automated Cleanup via Automation

```yaml
automation:
  - alias: "SPAN: Auto-cleanup after firmware update"
    trigger:
      - platform: state
        entity_id: sensor.span_panel_software_version
    action:
      - delay: "00:05:00" # Wait for panel to stabilize
      - service: span_panel.cleanup_energy_spikes
        data:
          start_time: "{{ now() - timedelta(hours=1) }}"
      - service: persistent_notification.create
        data:
          title: "SPAN Energy Statistics Cleaned"
          message: "Removed firmware update spikes from Energy Dashboard"
```

### Dry Run Mode for Safety

```yaml
# Preview what would be deleted without actually deleting
service: span_panel.cleanup_energy_spikes
data:
  dry_run: true
```

**Dry run returns detailed results via:**

1. **Service Response** (visible in Developer Tools → Services):

   ```json
   {
     "dry_run": true,
     "entities_processed": 3,
     "spikes_found": 5,
     "entries_deleted": 0,
     "details": [
       {
         "entity_id": "sensor.span_panel_solar_produced_energy",
         "spikes": [
           {
             "timestamp": "2025-12-09T13:35:00+00:00",
             "current_value": 8551863.5,
             "previous_value": 8913289.5,
             "delta": -361426.0
           }
         ]
       },
       {
         "entity_id": "sensor.span_panel_kitchen_consumed_energy",
         "spikes": [
           {
             "timestamp": "2025-12-09T13:35:00+00:00",
             "current_value": 33137.19,
             "previous_value": 37087.67,
             "delta": -3950.48
           }
         ]
       }
     ]
   }
   ```

   **Why Flagged:** All entries shown have negative delta (counter decreased), which violates TOTAL_INCREASING contract.

2. **Persistent Notification** (appears in HA notification bell):

   ```text
   Title: SPAN Energy Spike Cleanup (Dry Run)
   Message:
   Found 5 spikes that would be deleted:

   • solar_produced_energy: -361,426 Wh at 13:35
   • kitchen_consumed_energy: -3,950 Wh at 13:35
   • main_meter_consumed_energy: -474,642 Wh at 13:35

   Run without dry_run to delete these entries.
   ```

3. **Detailed Logs** (for debugging):

   ```text
   INFO: Dry run - would delete spike at 2025-12-09 13:35:00 for sensor.span_panel_solar_produced_energy (delta: -361426.0 Wh)
   ```

### Targeted Cleanup

```yaml
# Scan longer period if firmware update was days ago
service: span_panel.cleanup_energy_spikes
data:
  days_back: 7 # Scan last week
  dry_run: true
```

## Service Definition

```yaml
# services.yaml
cleanup_energy_spikes:
  name: Cleanup Energy Spikes
  description:
    Detect and remove negative energy spikes from all SPAN energy sensors caused by panel firmware updates. Uses main meter to detect reset timestamps.
  fields:
    days_back:
      name: Days to Scan
      description: Number of days in the past to scan for spikes. Defaults to 1 (last 24 hours).
      required: false
      default: 1
      selector:
        number:
          min: 1
          max: 30
          mode: box
          unit_of_measurement: "days"
    dry_run:
      name: Dry Run
      description: Preview spikes without deleting. Returns list of what would be deleted. Defaults to true for safety.
      required: false
      default: true
      selector:
        boolean:
```

## Implementation Plan

1. **Statistics query helper**
   - Query recorder database for entity statistics
   - Calculate deltas between consecutive entries
   - Identify negative spikes using 3-sigma rule

2. **Statistics deletion helper**
   - Delete specific statistics entries
   - Handle both short-term and long-term statistics
   - Transaction safety

3. **Service implementation**
   - Register cleanup service
   - Parameter validation
   - Call detection and cleanup helpers
   - Return simple report

4. **Dry run mode**
   - Preview mode without actual deletion
   - Return structured data via service response
   - Create persistent notification with summary
   - Detailed logging for debugging

5. **Main meter monitoring** (optional diagnostic)
   - Check main meter energy sensor on state change
   - Detect negative delta (potential firmware reset)
   - Send persistent notification alerting user
   - User decides whether to run cleanup service
   - Simple, non-intrusive awareness feature

6. **Result formatting**

   ```python
   # Service returns simple structure
   result = {
       "dry_run": True,
       "entities_processed": 3,
       "spikes_found": 5,
       "entries_deleted": 0,  # 0 if dry_run, count if executed
       "details": [
           {
               "entity_id": "sensor.span_panel_solar_produced_energy",
               "spikes": [
                   {
                       "timestamp": "2025-12-09T13:35:00+00:00",
                       "current_value": 8551863.5,
                       "previous_value": 8913289.5,
                       "delta": -361426.0
                   }
               ]
           }
       ]
   }

   # Create persistent notification
   await hass.components.persistent_notification.async_create(
       title="SPAN Energy Spike Cleanup" + (" (Dry Run)" if dry_run else ""),
       message=format_notification_message(result),
       notification_id="span_panel_spike_cleanup"
   )
   ```

7. **User feedback**
   - Persistent notification with summary
   - Logging at INFO level
   - Simple error messages

8. **Main meter spike monitoring** (implementation)

   ```python
   async def _async_main_meter_state_changed(event):
       """Monitor main meter for firmware resets (any decrease in TOTAL_INCREASING)."""
       new_state = event.data.get("new_state")
       old_state = event.data.get("old_state")

       if not new_state or not old_state:
           return

       try:
           new_value = float(new_state.state)
           old_value = float(old_state.state)
           delta = new_value - old_value

           # ANY decrease in TOTAL_INCREASING sensor = firmware reset
           if delta < 0:
               await hass.components.persistent_notification.async_create(
                   title="⚠️ SPAN Panel Firmware Reset Detected",
                   message=(
                       f"Main meter energy decreased by {abs(delta):,.0f} Wh. "
                       f"This indicates a panel firmware update.\n\n"
                       f"To clean up negative spikes in Energy Dashboard:\n"
                       f"1. Open Developer Tools → Services\n"
                       f"2. Run 'SPAN Panel: Cleanup Energy Spikes' with dry_run: true\n"
                       f"3. Review the results\n"
                       f"4. Run again with dry_run: false to apply cleanup"
                   ),
                   notification_id="span_panel_firmware_reset_detected"
               )
       except (ValueError, TypeError):
           pass

   # Register listener on integration setup
   async_track_state_change_event(
       hass,
       ["sensor.span_panel_main_meter_consumed_energy"],
       _async_main_meter_state_changed
   )
   ```

9. **Documentation**
   - Service usage guide in README
   - Example service calls
   - Troubleshooting tips
   - Explanation of main meter monitoring

## Testing Strategy

1. **Unit tests**
   - Negative delta detection (delta < 0)
   - Statistics deletion logic
   - Parameter validation
   - Main meter monitoring trigger

2. **Integration tests**
   - Query recorder database
   - Delete statistics entries
   - Dry run mode behavior
   - Notification creation

3. **Manual testing**
   - Simulate firmware update spike
   - Run cleanup service (dry run first)
   - Verify Energy Dashboard correction
   - Test main meter decrease detection

## Implementation Notes

- **Database access** - Use HA's recorder API, not direct SQL
- **Transaction safety** - Proper session management
- **Input validation** - Sanitize entity IDs and timestamps
- **Query optimization** - Use indexed queries on recorder DB
- **Time range limits** - Default 1 day scan (configurable)

## Success Criteria

✅ Service detects all negative deltas in TOTAL_INCREASING sensors ✅ Cleanup removes invalid entries without affecting normal data ✅ Dry run mode accurately
previews changes ✅ Main meter monitoring alerts user immediately on any decrease ✅ Energy Dashboard shows correct data after cleanup ✅ User experience:
seconds to cleanup vs. 20+ minutes manual editing

## References

- [GitHub Issue #87: wild numbers after a panel reset](https://github.com/SpanPanel/span/issues/87)
- [HA Recorder Component](https://www.home-assistant.io/integrations/recorder/)
- [HA Statistics Documentation](https://developers.home-assistant.io/docs/core/entity/sensor#long-term-statistics)
