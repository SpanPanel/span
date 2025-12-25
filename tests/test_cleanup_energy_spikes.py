"""Tests for the cleanup energy spikes service."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.services.cleanup_energy_spikes import (
    SERVICE_CLEANUP_ENERGY_SPIKES,
    _find_main_meter_sensor,
    _get_span_energy_sensors,
    async_setup_cleanup_energy_spikes_service,
    cleanup_energy_spikes,
)

TEST_CONFIG_ENTRY_ID = "test_config_entry_1"


def _get_test_time_range():
    """Get a test time range that covers the test data timestamps."""
    # Test timestamps are around Dec 9, 2024
    # Create a range that covers 24 hours around that time
    base_time = dt_util.utc_from_timestamp(1733760000)  # Dec 9, 2024 00:00 UTC
    start_time = dt_util.as_local(base_time - timedelta(hours=12))
    end_time = dt_util.as_local(base_time + timedelta(hours=12))
    return start_time, end_time


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_register = MagicMock()
    hass.services.async_call = AsyncMock()
    # Add data dict for entity registry
    hass.data = {}
    # Mock config_entries to validate config entry ID
    mock_entry = MagicMock()
    mock_entry.entry_id = TEST_CONFIG_ENTRY_ID
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[mock_entry])
    return hass


@pytest.fixture
def mock_entity_registry():
    """Create a mock entity registry with SPAN entities."""

    def _create_registry(entity_ids: list[str], config_entry_id: str = TEST_CONFIG_ENTRY_ID):
        """Create registry entries for given entity IDs."""
        registry = MagicMock(spec=er.EntityRegistry)
        entries = {}
        for entity_id in entity_ids:
            entry = MagicMock()
            entry.entity_id = entity_id
            entry.config_entry_id = config_entry_id
            entries[entity_id] = entry
        registry.entities = MagicMock()
        registry.entities.values.return_value = list(entries.values())
        registry.async_get = lambda eid: entries.get(eid)
        return registry

    return _create_registry


@pytest.fixture
def mock_span_energy_sensors():
    """Create mock SPAN energy sensor states."""
    sensors = {
        "sensor.span_panel_main_meter_consumed_energy": State(
            "sensor.span_panel_main_meter_consumed_energy",
            "5688566.75",
            {
                "state_class": SensorStateClass.TOTAL_INCREASING,
                "device_class": "energy",
                "unit_of_measurement": "Wh",
            },
        ),
        "sensor.span_panel_main_meter_produced_energy": State(
            "sensor.span_panel_main_meter_produced_energy",
            "1234567.89",
            {
                "state_class": SensorStateClass.TOTAL_INCREASING,
                "device_class": "energy",
                "unit_of_measurement": "Wh",
            },
        ),
        "sensor.span_panel_kitchen_consumed_energy": State(
            "sensor.span_panel_kitchen_consumed_energy",
            "37087.67",
            {
                "state_class": SensorStateClass.TOTAL_INCREASING,
                "device_class": "energy",
                "unit_of_measurement": "Wh",
            },
        ),
        "sensor.span_panel_current_power": State(
            "sensor.span_panel_current_power",
            "1500.0",
            {
                "state_class": SensorStateClass.MEASUREMENT,
                "device_class": "power",
                "unit_of_measurement": "W",
            },
        ),
    }
    return sensors


class TestGetSpanEnergySensors:
    """Tests for _get_span_energy_sensors function."""

    def test_finds_total_increasing_sensors(self, mock_hass, mock_span_energy_sensors):
        """Test that only TOTAL_INCREASING sensors are returned."""
        mock_hass.states.async_entity_ids.return_value = list(
            mock_span_energy_sensors.keys()
        )
        mock_hass.states.get = lambda entity_id: mock_span_energy_sensors.get(entity_id)

        result = _get_span_energy_sensors(mock_hass)

        # Should find 3 TOTAL_INCREASING sensors (not the power sensor)
        assert len(result) == 3
        assert "sensor.span_panel_main_meter_consumed_energy" in result
        assert "sensor.span_panel_main_meter_produced_energy" in result
        assert "sensor.span_panel_kitchen_consumed_energy" in result
        assert "sensor.span_panel_current_power" not in result

    def test_ignores_non_span_sensors(self, mock_hass):
        """Test that non-SPAN sensors are ignored."""
        mock_hass.states.async_entity_ids.return_value = [
            "sensor.some_other_sensor",
            "sensor.span_panel_test_energy",
        ]
        mock_hass.states.get.return_value = State(
            "sensor.span_panel_test_energy",
            "1000",
            {"state_class": SensorStateClass.TOTAL_INCREASING},
        )

        result = _get_span_energy_sensors(mock_hass)

        assert len(result) == 1
        assert "sensor.span_panel_test_energy" in result

    def test_handles_no_sensors(self, mock_hass):
        """Test handling when no sensors exist."""
        mock_hass.states.async_entity_ids.return_value = []

        result = _get_span_energy_sensors(mock_hass)

        assert result == []


class TestFindMainMeterSensor:
    """Tests for _find_main_meter_sensor function."""

    def test_finds_main_meter_consumed(self):
        """Test finding main meter consumed energy sensor."""
        sensors = [
            "sensor.span_panel_main_meter_consumed_energy",
            "sensor.span_panel_main_meter_produced_energy",
            "sensor.span_panel_kitchen_consumed_energy",
        ]

        result = _find_main_meter_sensor(sensors)

        assert result == "sensor.span_panel_main_meter_consumed_energy"

    def test_fallback_to_any_main_meter_energy(self):
        """Test fallback when consumed energy not found."""
        sensors = [
            "sensor.span_panel_main_meter_produced_energy",
            "sensor.span_panel_kitchen_consumed_energy",
        ]

        result = _find_main_meter_sensor(sensors)

        # Falls back to any main meter energy sensor
        assert result == "sensor.span_panel_main_meter_produced_energy"

    def test_returns_none_when_no_main_meter(self):
        """Test returns None when no main meter sensor found."""
        sensors = [
            "sensor.span_panel_kitchen_consumed_energy",
            "sensor.span_panel_bedroom_consumed_energy",
        ]

        result = _find_main_meter_sensor(sensors)

        assert result is None


class TestServiceRegistration:
    """Tests for service registration."""

    @pytest.mark.asyncio
    async def test_service_registered(self, mock_hass):
        """Test that the service is registered correctly."""
        await async_setup_cleanup_energy_spikes_service(mock_hass)

        mock_hass.services.async_register.assert_called_once()
        call_args = mock_hass.services.async_register.call_args
        assert call_args[0][0] == DOMAIN
        assert call_args[0][1] == SERVICE_CLEANUP_ENERGY_SPIKES

    @pytest.mark.asyncio
    async def test_service_not_registered_twice(self, mock_hass):
        """Test that the service is not registered if it already exists."""
        # Simulate service already registered via hass.data flag
        mock_hass.data[f"{DOMAIN}_cleanup_service_registered"] = True

        await async_setup_cleanup_energy_spikes_service(mock_hass)

        mock_hass.services.async_register.assert_not_called()


class TestCleanupEnergySpikes:
    """Tests for cleanup_energy_spikes function."""

    @pytest.mark.asyncio
    async def test_invalid_config_entry(self, mock_hass):
        """Test handling when config entry is not a SPAN panel."""
        mock_hass.config_entries.async_entries.return_value = []
        start_time, end_time = _get_test_time_range()

        result = await cleanup_energy_spikes(
            mock_hass,
            config_entry_id="invalid_entry",
            start_time=start_time,
            end_time=end_time,
            dry_run=True,
        )

        assert result["entities_processed"] == 0
        assert "not a SPAN panel" in result["error"]

    @pytest.mark.asyncio
    async def test_no_sensors_found(self, mock_hass):
        """Test handling when no SPAN sensors are found."""
        mock_hass.states.async_entity_ids.return_value = []
        start_time, end_time = _get_test_time_range()

        result = await cleanup_energy_spikes(
            mock_hass,
            config_entry_id=TEST_CONFIG_ENTRY_ID,
            start_time=start_time,
            end_time=end_time,
            dry_run=True,
        )

        assert result["entities_processed"] == 0
        assert result["error"] == "No SPAN energy sensors found"

    @pytest.mark.asyncio
    async def test_no_main_meter_found(self, mock_hass, mock_entity_registry):
        """Test handling when main meter is not found."""
        # Create sensor without "main_meter" in name
        entity_ids = ["sensor.span_panel_kitchen_consumed_energy"]
        mock_hass.states.async_entity_ids.return_value = entity_ids
        mock_hass.states.get.return_value = State(
            "sensor.span_panel_kitchen_consumed_energy",
            "1000",
            {"state_class": SensorStateClass.TOTAL_INCREASING},
        )

        with patch(
            "custom_components.span_panel.services.cleanup_energy_spikes.er.async_get"
        ) as mock_er:
            mock_er.return_value = mock_entity_registry(entity_ids)
            start_time, end_time = _get_test_time_range()
            result = await cleanup_energy_spikes(
                mock_hass,
                config_entry_id=TEST_CONFIG_ENTRY_ID,
                start_time=start_time,
                end_time=end_time,
                dry_run=True,
            )

        assert "No main meter sensor found" in result["error"]

    @pytest.mark.asyncio
    async def test_no_spikes_detected(
        self, mock_hass, mock_span_energy_sensors, mock_entity_registry
    ):
        """Test when no spikes are detected."""
        entity_ids = list(mock_span_energy_sensors.keys())
        mock_hass.states.async_entity_ids.return_value = entity_ids
        mock_hass.states.get = lambda entity_id: mock_span_energy_sensors.get(entity_id)

        # Mock recorder to return stable statistics (no decreases)
        mock_stats = {
            "sensor.span_panel_main_meter_consumed_energy": [
                {"start": 1733760000, "sum": 5688000.0},
                {"start": 1733763600, "sum": 5688200.0},  # Normal increase
                {"start": 1733767200, "sum": 5688400.0},  # Normal increase
            ]
        }

        with (
            patch(
                "custom_components.span_panel.services.cleanup_energy_spikes.get_instance"
            ) as mock_get_instance,
            patch(
                "custom_components.span_panel.services.cleanup_energy_spikes.er.async_get"
            ) as mock_er,
        ):
            mock_recorder = MagicMock()
            mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_stats)
            mock_get_instance.return_value = mock_recorder
            mock_er.return_value = mock_entity_registry(entity_ids)
            start_time, end_time = _get_test_time_range()

            result = await cleanup_energy_spikes(
                mock_hass,
                config_entry_id=TEST_CONFIG_ENTRY_ID,
                start_time=start_time,
                end_time=end_time,
                dry_run=True,
            )

        assert result["entities_processed"] == 3
        assert result["reset_timestamps"] == []
        assert "No firmware reset spikes detected" in result["message"]

    @pytest.mark.asyncio
    async def test_spike_detected_dry_run(
        self, mock_hass, mock_span_energy_sensors, mock_entity_registry
    ):
        """Test detection of a firmware reset spike in dry run mode."""
        entity_ids = list(mock_span_energy_sensors.keys())
        mock_hass.states.async_entity_ids.return_value = entity_ids
        mock_hass.states.get = lambda entity_id: mock_span_energy_sensors.get(entity_id)

        # Mock recorder to return statistics with a decrease (firmware reset)
        reset_timestamp = 1733763600  # Specific timestamp for the reset
        mock_stats = {
            "sensor.span_panel_main_meter_consumed_energy": [
                {"start": 1733760000, "sum": 5688566.0},
                {"start": reset_timestamp, "sum": 5213928.0},  # Decrease = reset!
                {"start": 1733767200, "sum": 5688570.0},  # Recovery
            ]
        }

        with (
            patch(
                "custom_components.span_panel.services.cleanup_energy_spikes.get_instance"
            ) as mock_get_instance,
            patch(
                "custom_components.span_panel.services.cleanup_energy_spikes.er.async_get"
            ) as mock_er,
        ):
            mock_recorder = MagicMock()
            # Called multiple times: _find_reset_timestamps, _collect_spike_details
            mock_recorder.async_add_executor_job = AsyncMock(return_value=mock_stats)
            mock_get_instance.return_value = mock_recorder
            mock_er.return_value = mock_entity_registry(entity_ids)
            start_time, end_time = _get_test_time_range()

            result = await cleanup_energy_spikes(
                mock_hass,
                config_entry_id=TEST_CONFIG_ENTRY_ID,
                start_time=start_time,
                end_time=end_time,
                dry_run=True,
            )

        assert result["dry_run"] is True
        assert result["entities_processed"] == 3
        assert len(result["reset_timestamps"]) == 1
        assert result["sensors_adjusted"] == 0  # Dry run, no actual adjustment
        assert "error" not in result  # No error should be present

    @pytest.mark.asyncio
    async def test_spike_adjusted_when_not_dry_run(
        self, mock_hass, mock_span_energy_sensors, mock_entity_registry
    ):
        """Test actual adjustment when dry_run is False."""
        entity_ids = list(mock_span_energy_sensors.keys())
        mock_hass.states.async_entity_ids.return_value = entity_ids
        mock_hass.states.get = lambda entity_id: mock_span_energy_sensors.get(entity_id)

        # Mock recorder with a reset spike
        # Sensor names must match mock_span_energy_sensors fixture
        reset_timestamp = 1733763600

        # Initial stats with drops (before adjustment)
        initial_stats = {
            "sensor.span_panel_main_meter_consumed_energy": [
                {"start": 1733760000, "sum": 5688566.0},
                {"start": reset_timestamp, "sum": 5213928.0},  # Decrease = reset!
                {"start": 1733767200, "sum": 5688570.0},
            ],
            "sensor.span_panel_main_meter_produced_energy": [
                {"start": 1733760000, "sum": 100000.0},
                {"start": reset_timestamp, "sum": 90000.0},  # Decrease = reset!
                {"start": 1733767200, "sum": 100100.0},
            ],
            "sensor.span_panel_kitchen_consumed_energy": [
                {"start": 1733760000, "sum": 50000.0},
                {"start": reset_timestamp, "sum": 45000.0},  # Decrease = reset!
                {"start": 1733767200, "sum": 50100.0},
            ],
        }

        # Stats after adjustment (drops fixed - subsequent entries adjusted)
        # After adjusting consumed_energy: +474638 at reset_timestamp, so subsequent entries get +474638
        adjusted_consumed_stats = {
            "sensor.span_panel_main_meter_consumed_energy": [
                {"start": 1733760000, "sum": 5688566.0},
                {"start": reset_timestamp, "sum": 5688566.0},  # Adjusted: 5213928 + 474638
                {"start": 1733767200, "sum": 6163208.0},  # Adjusted: 5688570 + 474638
            ],
        }

        # After adjusting produced_energy: +10000 at reset_timestamp
        adjusted_produced_stats = {
            "sensor.span_panel_main_meter_produced_energy": [
                {"start": 1733760000, "sum": 100000.0},
                {"start": reset_timestamp, "sum": 100000.0},  # Adjusted: 90000 + 10000
                {"start": 1733767200, "sum": 101100.0},  # Adjusted: 100100 + 10000
            ],
        }


        with (
            patch(
                "custom_components.span_panel.services.cleanup_energy_spikes.get_instance"
            ) as mock_get_instance,
            patch(
                "custom_components.span_panel.services.cleanup_energy_spikes.er.async_get"
            ) as mock_er,
            patch(
                "custom_components.span_panel.services.cleanup_energy_spikes.async_call_later"
            ) as mock_call_later,
        ):
            mock_recorder = MagicMock()

            # Track adjustment calls to simulate stats changes
            adjustment_calls = []

            # Track which sensors have been adjusted
            # After an adjustment, subsequent queries return adjusted stats (no drops)
            sensors_adjusted_set = set()

            def mock_adjust_statistics(statistic_id, start_time, sum_adjustment, adjustment_unit):
                """Track adjustment calls and mark sensor as adjusted."""
                adjustment_calls.append((statistic_id, start_time, sum_adjustment))
                sensors_adjusted_set.add(statistic_id)

            mock_recorder.async_adjust_statistics = MagicMock(side_effect=mock_adjust_statistics)

            def mock_query_stats(func, *args, **kwargs):
                """Mock async_add_executor_job - accepts function and its args."""
                # Extract arguments from the function call
                # _query_statistics(hass, start_time, end_time, entity_ids, period)
                if len(args) >= 4:
                    hass_arg, start_time_arg, end_time_arg, entity_ids_arg = args[0:4]
                    period_arg = args[4] if len(args) > 4 else kwargs.get("period", "hour")
                else:
                    # Fallback if args structure is different
                    entity_ids_arg = args[3] if len(args) > 3 else set()
                    period_arg = "hour"

                # If querying a single entity (adjustment loop), return appropriate stats
                if len(entity_ids_arg) == 1:
                    entity_id = next(iter(entity_ids_arg))

                    # If this sensor has been adjusted, return adjusted stats (no drops)
                    if entity_id in sensors_adjusted_set:
                        if entity_id == "sensor.span_panel_main_meter_consumed_energy":
                            return {entity_id: adjusted_consumed_stats[entity_id]}
                        elif entity_id == "sensor.span_panel_main_meter_produced_energy":
                            return {entity_id: adjusted_produced_stats[entity_id]}
                        else:
                            return {entity_id: initial_stats.get(entity_id, [])}
                    else:
                        # Not adjusted yet, return initial stats with drop
                        return {entity_id: initial_stats[entity_id]}

                # For multi-entity queries (like _find_reset_timestamps, _collect_spike_details)
                # always return initial_stats (these happen before adjustments)
                result = {}
                for eid in entity_ids_arg:
                    result[eid] = initial_stats.get(eid, [])
                return result

            mock_recorder.async_add_executor_job = AsyncMock(side_effect=mock_query_stats)
            mock_get_instance.return_value = mock_recorder
            mock_er.return_value = mock_entity_registry(entity_ids)
            start_time, end_time = _get_test_time_range()

            # Mock async_call_later to immediately call the callback (no delay in tests)
            def immediate_callback(hass, delay, callback):
                # Call the callback immediately with current time
                from homeassistant.util import dt as dt_util
                callback(dt_util.utcnow())

            mock_call_later.side_effect = immediate_callback

            result = await cleanup_energy_spikes(
                mock_hass,
                config_entry_id=TEST_CONFIG_ENTRY_ID,
                start_time=start_time,
                end_time=end_time,
                dry_run=False,
            )

        assert result["dry_run"] is False
        assert len(result["reset_timestamps"]) == 1
        # All sensors with negative spikes are adjusted (main meter and kitchen)
        # Each sensor that experienced a drop during firmware reset should be corrected
        assert result["sensors_adjusted"] == 3  # All 3 sensors with negative spikes adjusted
        assert "error" not in result  # No error should be present
        # Verify async_adjust_statistics was called for each sensor with a negative spike
        assert mock_recorder.async_adjust_statistics.call_count == 3


class TestMainMeterMonitoring:
    """Tests for main meter monitoring functionality."""

    @pytest.mark.asyncio
    async def test_monitoring_setup(self, mock_hass, mock_span_energy_sensors):
        """Test that main meter monitoring is set up correctly."""
        from custom_components.span_panel.services.main_meter_monitoring import (
            find_main_meter_entity,
        )

        mock_hass.states.async_entity_ids.return_value = list(
            mock_span_energy_sensors.keys()
        )
        mock_hass.states.get = lambda entity_id: mock_span_energy_sensors.get(entity_id)

        # Test find_main_meter_entity
        result = find_main_meter_entity(mock_hass)
        assert result == "sensor.span_panel_main_meter_consumed_energy"

    @pytest.mark.asyncio
    async def test_monitoring_not_setup_without_main_meter(self, mock_hass):
        """Test that monitoring is not set up when main meter not found."""
        from custom_components.span_panel.services.main_meter_monitoring import (
            find_main_meter_entity,
        )

        # Return sensors without main meter
        mock_hass.states.async_entity_ids.return_value = [
            "sensor.span_panel_kitchen_consumed_energy"
        ]
        mock_hass.states.get.return_value = State(
            "sensor.span_panel_kitchen_consumed_energy",
            "1000",
            {"state_class": SensorStateClass.TOTAL_INCREASING},
        )

        result = find_main_meter_entity(mock_hass)
        assert result is None
