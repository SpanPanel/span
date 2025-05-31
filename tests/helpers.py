"""Helper functions for testing the Span Panel integration."""

import datetime
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.const import STORAGE_BATTERY_PERCENTAGE
from custom_components.span_panel.span_panel_hardware_status import (
    SpanPanelHardwareStatus,
)
from custom_components.span_panel.span_panel_data import SpanPanelData
from .factories import SpanPanelApiResponseFactory


class SimpleMockPanel:
    """Simple mock panel that returns actual values."""

    def __init__(self, panel_data: dict[str, Any]):
        """Initialize the mock panel."""
        # Map factory data keys to property names expected by sensors
        self.instant_grid_power = panel_data.get("instantGridPowerW", 0.0)
        self.feedthrough_power = panel_data.get("feedthroughPowerW", 0.0)
        self.current_run_config = panel_data.get("currentRunConfig", "PANEL_ON_GRID")
        self.dsm_grid_state = panel_data.get("dsmGridState", "DSM_GRID_UP")
        self.dsm_state = panel_data.get("dsmState", "DSM_ON_GRID")
        self.main_relay_state = panel_data.get("mainRelayState", "CLOSED")
        self.grid_sample_start_ms = panel_data.get("gridSampleStartMs", 0)
        self.grid_sample_end_ms = panel_data.get("gridSampleEndMs", 0)
        self.main_meter_energy_produced = panel_data.get("mainMeterEnergyWh", {}).get(
            "producedEnergyWh", 0.0
        )
        self.main_meter_energy_consumed = panel_data.get("mainMeterEnergyWh", {}).get(
            "consumedEnergyWh", 0.0
        )
        self.feedthrough_energy_produced = panel_data.get(
            "feedthroughEnergyWh", {}
        ).get("producedEnergyWh", 0.0)
        self.feedthrough_energy_consumed = panel_data.get(
            "feedthroughEnergyWh", {}
        ).get("consumedEnergyWh", 0.0)

        # Also set the original keys for direct access if needed
        for key, value in panel_data.items():
            if not hasattr(self, key):
                setattr(self, key, value)


class MockSpanPanelStorageBattery:
    """Mock storage battery for testing."""

    def __init__(self, battery_data: dict[str, Any]):
        """Initialize the mock storage battery."""
        self.storage_battery_percentage = battery_data.get(
            STORAGE_BATTERY_PERCENTAGE, 85
        )

        # Also set any other battery attributes from the data
        for key, value in battery_data.items():
            if not hasattr(self, key):
                setattr(self, key, value)


@contextmanager
def patch_span_panel_dependencies(
    mock_api_responses: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
):
    """Patches common dependencies for setting up the Span Panel integration in tests."""

    if mock_api_responses is None:
        mock_api_responses = (
            SpanPanelApiResponseFactory.create_complete_panel_response()
        )

    # Create mock API instance
    mock_api = AsyncMock()
    mock_api.get_status_data = AsyncMock(return_value=mock_api_responses["status"])
    mock_api.get_panel_data = AsyncMock(return_value=mock_api_responses["panel"])
    mock_api.get_circuits_data = AsyncMock(return_value=mock_api_responses["circuits"])
    mock_api.get_storage_battery_data = AsyncMock(
        return_value=mock_api_responses["battery"]
    )
    mock_api.set_relay = AsyncMock()

    # Create mock objects that properly expose the data as attributes
    mock_status = SpanPanelHardwareStatus.from_dict(mock_api_responses["status"])

    # Create real panel data using the actual from_dict method for proper solar calculations
    # If options are provided, create a proper Options object
    panel_options = None
    if options:
        from custom_components.span_panel.options import Options

        # Create a mock config entry with the options
        mock_entry = MagicMock()
        mock_entry.options = options
        panel_options = Options(mock_entry)

    mock_panel_data = SpanPanelData.from_dict(
        mock_api_responses["panel"], panel_options
    )

    mock_circuits = {}
    for circuit_id, circuit_data in mock_api_responses["circuits"].items():
        # Create proper MockSpanPanelCircuit objects instead of MagicMock
        mock_circuits[circuit_id] = MockSpanPanelCircuit(circuit_data)

    # Create proper battery mock instead of MagicMock
    mock_battery = MockSpanPanelStorageBattery(mock_api_responses["battery"])

    # Mock the SpanPanel class and ensure update() calls the API methods
    mock_span_panel = MagicMock()
    mock_span_panel.api = mock_api
    mock_span_panel.status = mock_status
    mock_span_panel.panel = mock_panel_data
    mock_span_panel.circuits = mock_circuits
    mock_span_panel.storage_battery = mock_battery

    # Make update() method actually call the API methods and update the data
    async def mock_update():
        """Mock update method that calls the API and updates data."""
        # Call the API methods to register the calls for assertion
        await mock_api.get_status_data()
        await mock_api.get_panel_data()
        await mock_api.get_circuits_data()
        await mock_api.get_storage_battery_data()

        # Update the mock data (simulate what the real update does)
        status_data = await mock_api.get_status_data()
        await mock_api.get_panel_data()
        await mock_api.get_circuits_data()
        battery_data = await mock_api.get_storage_battery_data()

        # Update mock status with a new real status object
        mock_span_panel.status = SpanPanelHardwareStatus.from_dict(status_data)

        # Update mock battery with new data
        mock_span_panel.storage_battery = MockSpanPanelStorageBattery(battery_data)

    mock_span_panel.update = mock_update

    patches = [
        patch("custom_components.span_panel.SpanPanel", return_value=mock_span_panel),
        patch(
            "custom_components.span_panel.span_panel.SpanPanel",
            return_value=mock_span_panel,
        ),
        patch(
            "custom_components.span_panel.coordinator.SpanPanel",
            return_value=mock_span_panel,
        ),
        patch(
            "homeassistant.helpers.httpx_client.get_async_client",
            return_value=AsyncMock(),
        ),
        patch("custom_components.span_panel.log_entity_summary", return_value=None),
        # Disable select platform to avoid type checking issues in tests
        patch(
            "custom_components.span_panel.select.async_setup_entry", return_value=True
        ),
    ]

    try:
        for p in patches:
            p.start()
        yield mock_span_panel, mock_api
    finally:
        for p in patches:
            p.stop()


def make_span_panel_entry(
    entry_id: str = "test_entry",
    host: str = "192.168.1.100",
    access_token: str = "test_token",
    scan_interval: int = 15,
    options: dict[str, Any] | None = None,
    version: int = 1,
) -> MockConfigEntry:
    """Create a MockConfigEntry for Span Panel with common defaults."""
    return MockConfigEntry(
        domain="span_panel",
        data={
            CONF_HOST: host,
            CONF_ACCESS_TOKEN: access_token,
            CONF_SCAN_INTERVAL: scan_interval,
        },
        options=options or {},
        entry_id=entry_id,
        version=version,
    )


def assert_entity_state(
    hass: HomeAssistant, entity_id: str, expected_state: Any
) -> None:
    """Assert the state of an entity."""
    state = hass.states.get(entity_id)
    assert state is not None, f"Entity {entity_id} not found in hass.states"
    assert state.state == str(
        expected_state
    ), f"Expected {entity_id} to be '{expected_state}', got '{state.state}'"


def assert_entity_attribute(
    hass: HomeAssistant, entity_id: str, attribute: str, expected_value: Any
) -> None:
    """Assert an attribute of an entity."""
    state = hass.states.get(entity_id)
    assert state is not None, f"Entity {entity_id} not found in hass.states"
    actual_value = state.attributes.get(attribute)
    assert (
        actual_value == expected_value
    ), f"Expected {entity_id}.{attribute} to be '{expected_value}', got '{actual_value}'"


async def advance_time(hass: HomeAssistant, seconds: int) -> None:
    """Advance Home Assistant time by a given number of seconds and block till done."""
    now = utcnow()
    future = now + datetime.timedelta(seconds=seconds)
    from .common import async_fire_time_changed

    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()


async def trigger_coordinator_update(coordinator: Any) -> None:
    """Manually trigger a coordinator update."""
    await coordinator.async_request_refresh()
    await coordinator.hass.async_block_till_done()


def setup_span_panel_entry(
    hass: HomeAssistant,
    mock_api_responses: dict[str, Any] | None = None,
    entry_id: str = "test_span_panel",
    host: str = "192.168.1.100",
    access_token: str = "test_token",
    options: dict[str, Any] | None = None,
) -> tuple[MockConfigEntry, dict[str, Any] | None]:
    """Create and setup a span panel entry for testing.

    Returns:
        tuple: (config_entry, mock_api_responses)

    """
    entry = make_span_panel_entry(
        entry_id=entry_id,
        host=host,
        access_token=access_token,
        options=options,
    )
    entry.add_to_hass(hass)

    # This will be used in the context manager
    return entry, mock_api_responses


def get_circuit_entity_id(
    circuit_id: str,
    circuit_name: str,
    platform: str,
    suffix: str,
    use_circuit_numbers: bool = False,
    use_device_prefix: bool = True,
) -> str:
    """Generate expected entity ID for a circuit entity."""
    if use_device_prefix:
        prefix = "span_panel"
    else:
        prefix = ""

    if use_circuit_numbers:
        middle = f"circuit_{circuit_id}"
    else:
        # Convert circuit name to entity ID format
        middle = circuit_name.lower().replace(" ", "_").replace("-", "_")

    parts = [p for p in [prefix, middle, suffix] if p]
    entity_id = f"{platform}.{'_'.join(parts)}"

    return entity_id


def get_panel_entity_id(
    suffix: str, platform: str = "sensor", use_device_prefix: bool = True
) -> str:
    """Generate expected entity ID for a panel-level entity."""
    if use_device_prefix:
        prefix = "span_panel"
    else:
        prefix = ""

    parts = [p for p in [prefix, suffix] if p]
    entity_id = f"{platform}.{'_'.join(parts)}"

    return entity_id


class MockSpanPanelCircuit:
    """Mock circuit for testing."""

    def __init__(self, circuit_data: dict[str, Any]):
        """Initialize the mock circuit."""
        self.id = circuit_data["id"]
        self.name = circuit_data.get("name", "Test Circuit")
        self.instant_power = circuit_data.get("instantPowerW", 0.0)
        self.consumed_energy = circuit_data.get("consumedEnergyWh", 0.0)
        self.produced_energy = circuit_data.get("producedEnergyWh", 0.0)
        self.relay_state = circuit_data.get("relayState", "CLOSED")
        self.tabs = circuit_data.get("tabs", [1])
        self.priority = circuit_data.get("priority", "NICE_TO_HAVE")
        self.is_user_controllable = circuit_data.get("is_user_controllable", True)
        self.is_sheddable = circuit_data.get("is_sheddable", True)
        self.is_never_backup = circuit_data.get("is_never_backup", False)

    def copy(self):
        """Create a copy of this circuit."""
        return MockSpanPanelCircuit(
            {
                "id": self.id,
                "name": self.name,
                "instantPowerW": self.instant_power,
                "consumedEnergyWh": self.consumed_energy,
                "producedEnergyWh": self.produced_energy,
                "relayState": self.relay_state,
                "tabs": self.tabs,
                "priority": self.priority,
                "is_user_controllable": self.is_user_controllable,
                "is_sheddable": self.is_sheddable,
                "is_never_backup": self.is_never_backup,
            }
        )


async def mock_circuit_relay_operation(
    mock_api: AsyncMock, circuit_id: str, new_state: str, mock_circuits: dict[str, Any]
) -> None:
    """Mock a circuit relay operation and update the mock data."""
    if circuit_id in mock_circuits:
        mock_circuits[circuit_id].relay_state = new_state
        # Simulate API call
        mock_api.set_relay.return_value = None
