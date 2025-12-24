"""Tests for the cleanup energy spikes service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
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


class TestCleanupEnergySpikes:
    """Tests for cleanup_energy_spikes function."""

    @pytest.mark.asyncio
    async def test_invalid_config_entry(self, mock_hass):
        """Test handling when config entry is not a SPAN panel."""
        mock_hass.config_entries.async_entries.return_value = []

        result = await cleanup_energy_spikes(
            mock_hass, config_entry_id="invalid_entry", days_back=1, dry_run=True
        )

        assert result["entities_processed"] == 0
        assert "not a SPAN panel" in result["error"]

    @pytest.mark.asyncio
    async def test_no_sensors_found(self, mock_hass):
        """Test handling when no SPAN sensors are found."""
        mock_hass.states.async_entity_ids.return_value = []

        result = await cleanup_energy_spikes(
            mock_hass, config_entry_id=TEST_CONFIG_ENTRY_ID, days_back=1, dry_run=True
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
            result = await cleanup_energy_spikes(
                mock_hass, config_entry_id=TEST_CONFIG_ENTRY_ID, days_back=1, dry_run=True
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

            result = await cleanup_energy_spikes(
                mock_hass, config_entry_id=TEST_CONFIG_ENTRY_ID, days_back=1, dry_run=True
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

            result = await cleanup_energy_spikes(
                mock_hass, config_entry_id=TEST_CONFIG_ENTRY_ID, days_back=1, dry_run=True
            )

        assert result["dry_run"] is True
        assert result["entities_processed"] == 3
        assert len(result["reset_timestamps"]) == 1
        assert result["entries_deleted"] == 0  # Dry run, no actual deletion
        assert "error" not in result  # No error should be present

    @pytest.mark.asyncio
    async def test_spike_deleted_when_not_dry_run(
        self, mock_hass, mock_span_energy_sensors, mock_entity_registry
    ):
        """Test actual deletion when dry_run is False."""
        entity_ids = list(mock_span_energy_sensors.keys())
        mock_hass.states.async_entity_ids.return_value = entity_ids
        mock_hass.states.get = lambda entity_id: mock_span_energy_sensors.get(entity_id)

        # Mock recorder with a reset spike
        reset_timestamp = 1733763600
        mock_stats = {
            "sensor.span_panel_main_meter_consumed_energy": [
                {"start": 1733760000, "sum": 5688566.0},
                {"start": reset_timestamp, "sum": 5213928.0},  # Decrease = reset!
                {"start": 1733767200, "sum": 5688570.0},
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

            # Called multiple times:
            # 1. _find_reset_timestamps (returns stats)
            # 2. _collect_spike_details (returns stats)
            # 3. _delete_statistics_entries (returns count)
            mock_recorder.async_add_executor_job = AsyncMock(
                side_effect=[mock_stats, mock_stats, 3]  # 3 entries deleted
            )
            mock_get_instance.return_value = mock_recorder
            mock_er.return_value = mock_entity_registry(entity_ids)

            result = await cleanup_energy_spikes(
                mock_hass, config_entry_id=TEST_CONFIG_ENTRY_ID, days_back=1, dry_run=False
            )

        assert result["dry_run"] is False
        assert len(result["reset_timestamps"]) == 1
        assert result["entries_deleted"] == 3
        assert "error" not in result  # No error should be present


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
