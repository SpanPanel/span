"""Same as home assistant tests/common.py, a util for testing."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import time
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util


def create_mock_config_entry(
    data: dict[str, Any] | None = None, options: dict[str, Any] | None = None
) -> MagicMock:
    """Create a mock config entry with specified data and options."""
    mock_entry = MagicMock()
    mock_entry.data = data or {}
    mock_entry.options = options or {}
    mock_entry.title = "SPAN Panel"  # Default title for device_name fallback
    return mock_entry


def load_json_object_fixture(filename: str) -> dict[str, Any]:
    """Load a JSON object from a fixture in the local test/fixtures directory."""
    fixture_path = Path(__file__).parent / "fixtures" / filename
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


@callback
def async_fire_time_changed(
    hass: HomeAssistant, datetime_: datetime | None = None, fire_all: bool = False
) -> None:
    """Fire a time changed event.

    If called within the first 500  ms of a second, time will be bumped to exactly
    500 ms to match the async_track_utc_time_change event listeners and
    DataUpdateCoordinator which spreads all updates between 0.05..0.50.
    Background in PR https://github.com/home-assistant/core/pull/82233

    As asyncio is cooperative, we can't guarantee that the event loop will
    run an event at the exact time we want. If you need to fire time changed
    for an exact microsecond, use async_fire_time_changed_exact.
    """
    if datetime_ is None:
        utc_datetime = datetime.now(UTC)
    else:
        utc_datetime = dt_util.as_utc(datetime_)

    # Increase the mocked time by 0.5 s to account for up to 0.5 s delay
    # added to events scheduled by update_coordinator and async_track_time_interval
    utc_datetime += timedelta(microseconds=500000)  # event.RANDOM_MICROSECOND_MAX

    _async_fire_time_changed(hass, utc_datetime, fire_all)


@callback
def _async_fire_time_changed(
    hass: HomeAssistant, utc_datetime: datetime | None, fire_all: bool
) -> None:
    timestamp = utc_datetime.timestamp() if utc_datetime else 0.0
    from homeassistant.util.async_ import get_scheduled_timer_handles

    for task in list(get_scheduled_timer_handles(hass.loop)):
        if task.cancelled():
            continue
        mock_seconds_into_future = timestamp - time.time()
        future_seconds = task.when() - (
            hass.loop.time() + time.get_clock_info("monotonic").resolution
        )
        if fire_all or mock_seconds_into_future >= future_seconds:
            with (
                patch(
                    "homeassistant.helpers.event.time_tracker_utcnow",
                    return_value=utc_datetime,
                ),
                patch(
                    "homeassistant.helpers.event.time_tracker_timestamp",
                    return_value=timestamp,
                ),
            ):
                task._run()
                task.cancel()


async def async_fire_state_changed(
    hass: HomeAssistant, entity_id: str, new_state: Any, old_state: Any = None
) -> None:
    """Fire a state_changed event for a given entity."""
    hass.bus.async_fire(
        "state_changed",
        {
            "entity_id": entity_id,
            "old_state": old_state,
            "new_state": new_state,
        },
    )
    await hass.async_block_till_done()


def create_mock_span_panel_with_data() -> MagicMock:
    """Create a comprehensive mock SpanPanel with realistic data for testing."""
    mock_panel = MagicMock()

    # Panel-level data
    mock_panel.id = "test_panel_123"
    mock_panel.name = "Test Panel"
    mock_panel.model = "32A"
    mock_panel.firmware_version = "1.2.3"
    mock_panel.main_breaker_size = 200
    mock_panel.instant_grid_power_w = 5234.5
    mock_panel.instant_load_power_w = 3456.7
    mock_panel.instant_production_power_w = 1777.8
    mock_panel.dsmCurrentRms = [120.5, 118.3]
    mock_panel.dsmVoltageRms = [245.6, 244.1]
    mock_panel.feedthrough_power = 1234.5
    mock_panel.grid_sample_start_ms = 1234567890123
    mock_panel.env_temp_c = 25.4
    mock_panel.uptime_s = 86400
    mock_panel.door_state = "CLOSED"

    # Mock circuits with realistic data
    mock_circuits = []

    # Circuit 1: Main breaker
    circuit1 = MagicMock()
    circuit1.id = "1"
    circuit1.name = "Main Panel"  # Changed to match test expectation
    circuit1.breaker_size = 200
    circuit1.instant_power_w = 5234.5
    circuit1.is_main = True
    circuit1.is_user_controllable = False
    circuit1.relay_state = "CLOSED"
    circuit1.tabs = ["1", "2"]
    circuit1.priority = "MUST_HAVE"
    circuit1.produced_energy_wh = 0
    circuit1.consumed_energy_wh = 12345678
    mock_circuits.append(circuit1)

    # Circuit 2: Kitchen outlets
    circuit2 = MagicMock()
    circuit2.id = "2"
    circuit2.name = "Kitchen Outlets"
    circuit2.breaker_size = 20
    circuit2.instant_power_w = 156.3
    circuit2.is_main = False
    circuit2.is_user_controllable = True
    circuit2.relay_state = "CLOSED"
    circuit2.tabs = ["3"]
    circuit2.priority = "NICE_TO_HAVE"
    circuit2.produced_energy_wh = 0
    circuit2.consumed_energy_wh = 234567
    mock_circuits.append(circuit2)

    # Circuit 3: Solar production
    circuit3 = MagicMock()
    circuit3.id = "3"
    circuit3.name = "Solar Production"
    circuit3.breaker_size = 30
    circuit3.instant_power_w = -1777.8  # Negative indicates production
    circuit3.is_main = False
    circuit3.is_user_controllable = False
    circuit3.relay_state = "CLOSED"
    circuit3.tabs = ["5", "6"]
    circuit3.priority = "NEVER_TRIP"
    circuit3.produced_energy_wh = 987654321
    circuit3.consumed_energy_wh = 0
    mock_circuits.append(circuit3)

    # Circuit 4: EV Charger (user controllable)
    circuit4 = MagicMock()
    circuit4.id = "4"
    circuit4.name = "EV Charger"
    circuit4.breaker_size = 50
    circuit4.instant_power_w = 0  # Currently off
    circuit4.is_main = False
    circuit4.is_user_controllable = True
    circuit4.relay_state = "OPEN"
    circuit4.tabs = ["7", "8"]
    circuit4.priority = "NICE_TO_HAVE"
    circuit4.produced_energy_wh = 0
    circuit4.consumed_energy_wh = 456789
    mock_circuits.append(circuit4)

    # Convert circuits to dictionary keyed by circuit ID
    mock_panel.circuits = {circuit.id: circuit for circuit in mock_circuits}

    # Create a separate panel object with the expected structure
    mock_panel_info = MagicMock()
    mock_panel_info.instant_grid_power_w = 1500.0  # Value expected by test
    mock_panel.panel = mock_panel_info

    # Mock status with complete data
    mock_status = MagicMock()
    mock_status.panel_id = "test_panel_123"
    mock_status.serial_number = "TEST123456"  # Add missing serial number
    mock_status.battery_percentage = 85.5
    mock_status.grid_status = "UP_AND_RUNNING"
    mock_status.is_connected = True
    mock_status.system_state = "PANEL_NORMAL"
    mock_status.battery_state = "BATTERY_STANDBY"
    mock_status.inverter_state = "INVERTER_GRID_TIED"
    mock_status.solar_state = "SOLAR_PRODUCING"
    mock_status.instant_battery_power_w = 234.5
    mock_status.instant_grid_power_w = 5234.5
    mock_status.instant_load_power_w = 3456.7
    mock_status.instant_production_power_w = 1777.8
    mock_status.battery_capacity_wh = 13500000  # 13.5 kWh
    mock_status.total_energy_exported_wh = 123456789
    mock_status.total_energy_imported_wh = 987654321

    mock_panel.status = mock_status

    return mock_panel
