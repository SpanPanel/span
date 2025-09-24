"""Helper functions for testing the Span Panel integration."""

from collections.abc import Generator
from contextlib import contextmanager
import datetime

# Import from factories.py (the module file, not the package directory)
# Force direct file import to avoid the factories/ directory conflict
import importlib.util
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.const import (
    DSM_GRID_UP,
    DSM_ON_GRID,
    PANEL_ON_GRID,
    STORAGE_BATTERY_PERCENTAGE,
)
from custom_components.span_panel.span_panel_data import SpanPanelData
from custom_components.span_panel.span_panel_hardware_status import (
    SpanPanelHardwareStatus,
)

factories_path = os.path.join(os.path.dirname(__file__), "factories.py")
spec = importlib.util.spec_from_file_location("factories_direct", factories_path)
factories_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factories_module)

SpanPanelApiResponseFactory = factories_module.SpanPanelApiResponseFactory


class SimpleMockPanel:
    """Simple mock panel that returns actual values."""

    def __init__(self, panel_data: dict[str, Any]):
        """Initialize the mock panel."""
        # Map factory data keys to property names expected by sensors
        self.instant_grid_power = panel_data.get("instantGridPowerW", 0.0)
        self.feedthrough_power = panel_data.get("feedthroughPowerW", 0.0)
        self.current_run_config = panel_data.get("currentRunConfig", PANEL_ON_GRID)
        self.dsm_grid_state = panel_data.get("dsmGridState", DSM_GRID_UP)
        self.dsm_state = panel_data.get("dsmState", DSM_ON_GRID)
        self.main_relay_state = panel_data.get("mainRelayState", "CLOSED")
        self.grid_sample_start_ms = panel_data.get("gridSampleStartMs", 0)
        self.grid_sample_end_ms = panel_data.get("gridSampleEndMs", 0)
        self.main_meter_energy_produced = panel_data.get("mainMeterEnergyWh", {}).get(
            "producedEnergyWh", 0.0
        )
        self.main_meter_energy_consumed = panel_data.get("mainMeterEnergyWh", {}).get(
            "consumedEnergyWh", 0.0
        )
        self.feedthrough_energy_produced = panel_data.get("feedthroughEnergyWh", {}).get(
            "producedEnergyWh", 0.0
        )
        self.feedthrough_energy_consumed = panel_data.get("feedthroughEnergyWh", {}).get(
            "consumedEnergyWh", 0.0
        )

        # Also set the original keys for direct access if needed
        for key, value in panel_data.items():
            if not hasattr(self, key):
                setattr(self, key, value)


class MockSpanPanelStorageBattery:
    """Mock storage battery for testing."""

    def __init__(self, battery_data: dict[str, Any]):
        """Initialize the mock storage battery."""
        self.storage_battery_percentage = battery_data.get(STORAGE_BATTERY_PERCENTAGE, 85)

        # Also set any other battery attributes from the data
        for key, value in battery_data.items():
            if not hasattr(self, key):
                setattr(self, key, value)


@contextmanager
def patch_span_panel_dependencies(
    mock_api_responses: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> Generator[tuple[MagicMock, AsyncMock], None, None]:
    """Patches common dependencies for setting up the Span Panel integration in tests."""

    if mock_api_responses is None:
        mock_api_responses = SpanPanelApiResponseFactory.create_complete_panel_response()

    # Create mock API instance
    mock_api = AsyncMock()
    mock_api.get_status_data = AsyncMock(return_value=mock_api_responses["status"])
    mock_api.get_panel_data = AsyncMock(return_value=mock_api_responses["panel"])
    mock_api.get_circuits_data = AsyncMock(return_value=mock_api_responses["circuits"])
    mock_api.get_storage_battery_data = AsyncMock(return_value=mock_api_responses["battery"])
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

    mock_panel_data = SpanPanelData.from_dict(mock_api_responses["panel"], panel_options)

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
        patch(
            "custom_components.span_panel.entity_summary.log_entity_summary",
            return_value=None,
        ),
        # Disable select platform to avoid type checking issues in tests
        patch("custom_components.span_panel.select.async_setup_entry", return_value=True),
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
    version: int = 2,
    unique_id: str | None = None,
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
        unique_id=unique_id or f"{host}_{entry_id}",
    )


def assert_entity_state(hass: HomeAssistant, entity_id: str, expected_state: Any) -> None:
    """Assert the state of an entity."""
    state = hass.states.get(entity_id)
    assert state is not None, f"Entity {entity_id} not found in hass.states"
    assert state.state == str(expected_state), (
        f"Entity {entity_id} state is '{state.state}', expected '{expected_state}'"
    )


def assert_entity_attribute(
    hass: HomeAssistant, entity_id: str, attribute: str, expected_value: Any
) -> None:
    """Assert an attribute of an entity."""
    state = hass.states.get(entity_id)
    assert state is not None, f"Entity {entity_id} not found in hass.states"
    actual_value = state.attributes.get(attribute)
    assert actual_value == expected_value, (
        f"Expected {entity_id}.{attribute} to be '{expected_value}', got '{actual_value}'"
    )


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


def get_circuit_entity_id_from_integration(
    hass: HomeAssistant,
    coordinator: Any,
    span_panel: Any,
    circuit_data: Any,
    suffix: str,
) -> str:
    """Generate expected entity ID for a circuit entity using integration helpers.

    This function uses the same logic as the integration to generate entity IDs,
    ensuring tests match the actual behavior.

    Args:
        hass: Home Assistant instance
        coordinator: The coordinator instance
        span_panel: The span panel data
        circuit_data: Circuit data object
        suffix: Sensor suffix (e.g., "power", "energy_consumed")

    Returns:
        Entity ID string that matches what the integration actually generates

    """
    from custom_components.span_panel.helpers import construct_entity_id, get_circuit_number

    circuit_number = get_circuit_number(circuit_data)

    return construct_entity_id(
        coordinator,
        span_panel,
        "sensor",
        circuit_data.name,
        circuit_number,
        suffix,
    )


def get_panel_entity_id_from_integration(
    hass: HomeAssistant,
    coordinator: Any,
    span_panel: Any,
    suffix: str,
) -> str:
    """Generate expected entity ID for a panel-level entity using integration helpers.

    This function uses the same logic as the integration to generate entity IDs,
    ensuring tests match the actual behavior.

    Args:
        hass: Home Assistant instance
        coordinator: The coordinator instance
        span_panel: The span panel data
        suffix: Sensor suffix (e.g., "current_power", "dsm_state")

    Returns:
        Entity ID string that matches what the integration actually generates

    """
    from custom_components.span_panel.helpers import construct_panel_entity_id

    device_name = coordinator.config_entry.title
    use_device_prefix = coordinator.config_entry.options.get("use_device_prefix", False)
    return construct_panel_entity_id(
        coordinator,
        span_panel,
        "sensor",
        suffix,
        device_name,
        use_device_prefix=use_device_prefix,
    )


def find_circuit_entity_by_name_and_suffix(
    hass: HomeAssistant,
    circuit_name: str,
    suffix: str,
    platform: str = "sensor",
) -> str | None:
    """Find a circuit entity by circuit name and suffix from actual HA states.

    This function searches through the actual Home Assistant entity states
    to find entities that match the expected circuit name and suffix pattern.
    This works with both native sensors and synthetic sensors.

    Args:
        hass: Home Assistant instance
        circuit_name: Human-readable circuit name (e.g., "Kitchen Outlets")
        suffix: Sensor suffix (e.g., "power", "energy_consumed")
        platform: Platform name (default: "sensor")

    Returns:
        Entity ID if found, None otherwise

    """
    # Look for entities with the circuit name in the friendly name and suffix in entity ID
    expected_friendly_name_part = circuit_name
    expected_suffix = suffix

    for state in hass.states.async_all():
        entity_id = state.entity_id
        if not entity_id.startswith(f"{platform}."):
            continue

        # Check if entity ID contains the suffix
        if expected_suffix not in entity_id:
            continue

        # Check if friendly name contains the circuit name
        friendly_name = state.attributes.get("friendly_name", "")
        if expected_friendly_name_part in friendly_name:
            return entity_id

    return None


def find_panel_entity_by_suffix(
    hass: HomeAssistant,
    suffix: str,
    platform: str = "sensor",
) -> str | None:
    """Find a panel entity by suffix from actual HA states.

    This function searches through the actual Home Assistant entity states
    to find panel-level entities that match the expected suffix pattern.

    Args:
        hass: Home Assistant instance
        suffix: Sensor suffix (e.g., "current_power", "dsm_state")
        platform: Platform name (default: "sensor")

    Returns:
        Entity ID if found, None otherwise

    """
    # Look for entities with the suffix in the entity ID
    for state in hass.states.async_all():
        entity_id = state.entity_id
        if not entity_id.startswith(f"{platform}."):
            continue

        # Check if entity ID contains the suffix
        if suffix in entity_id:
            return entity_id

    return None


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


def cleanup_synthetic_yaml_files(hass: HomeAssistant) -> None:
    """Clean up synthetic sensor YAML files to ensure clean test state.

    This removes both the main synthetic sensors YAML and solar synthetic sensors YAML
    to ensure tests start with a clean slate and generate YAML from scratch.
    """
    # Main synthetic sensors YAML
    main_yaml_path = (
        Path(hass.config.config_dir) / "custom_components" / "span_panel" / "span_sensors.yaml"
    )
    if main_yaml_path.exists():
        main_yaml_path.unlink()

    # Solar synthetic sensors YAML
    solar_yaml_path = (
        Path(hass.config.config_dir)
        / "custom_components"
        / "span_panel"
        / "solar_synthetic_sensors.yaml"
    )
    if solar_yaml_path.exists():
        solar_yaml_path.unlink()


def reset_span_sensor_manager_static_state() -> None:
    """Reset static state in SpanSensorManager to prevent test pollution."""
    from custom_components.span_panel.span_sensor_manager import SpanSensorManager

    SpanSensorManager._static_registered_entities = None
    SpanSensorManager._static_entities_generated = False
    SpanSensorManager.static_entities_registered = False


def cleanup_synthetic_state(hass: HomeAssistant) -> None:
    """Clean up both YAML files and static state for test independence."""
    cleanup_synthetic_yaml_files(hass)
    reset_span_sensor_manager_static_state()


@contextmanager
def clean_synthetic_yaml_test():
    """Context manager that ensures clean YAML state for tests.

    This ensures tests start with no pre-existing YAML files and allows tests
    to verify actual YAML generation behavior rather than relying on fixtures.

    Usage:
        with clean_synthetic_yaml_test():
            # Test code that generates YAML from mock panel data
            pass
        # Caller should clean up YAML files in teardown
    """
    try:
        yield
    finally:
        # Note: We can't cleanup here directly since we don't have hass
        # Tests using this context manager should call cleanup_synthetic_yaml_files
        # in their teardown or use the setup_span_panel_entry_with_cleanup helper
        pass


def setup_span_panel_entry_with_cleanup(
    hass: HomeAssistant,
    mock_api_responses: dict[str, Any] | None = None,
    entry_id: str = "test_span_panel",
    host: str = "192.168.1.100",
    access_token: str = "test_token",
    options: dict[str, Any] | None = None,
    cleanup_yaml: bool = True,
) -> tuple[MockConfigEntry, dict[str, Any] | None]:
    """Set up a Span Panel config entry with optional YAML cleanup.

    Args:
        hass: Home Assistant instance
        mock_api_responses: Mock API responses (defaults to complete panel response)
        entry_id: Config entry ID
        host: Panel host
        access_token: Panel access token
        options: Config entry options
        cleanup_yaml: Whether to clean up existing YAML files before setup

    Returns:
        Tuple of (config_entry, mock_api_responses)

    """
    if cleanup_yaml:
        cleanup_synthetic_yaml_files(hass)

    return setup_span_panel_entry(hass, mock_api_responses, entry_id, host, access_token, options)


async def wait_for_synthetic_sensors(hass: HomeAssistant) -> None:
    """Wait for synthetic sensors to be created by yielding to the event loop."""
    # Give synthetic sensors time to be processed by yielding to the event loop multiple times
    for _ in range(5):
        await hass.async_block_till_done()


async def wait_for_entity_state(
    hass: HomeAssistant,
    entity_id: str,
    expected_state: Any,
    timeout: float = 5.0,
    check_interval: float = 0.1,
) -> None:
    """Wait for an entity to reach the expected state with retry logic."""
    # First, yield to the event loop to let synthetic sensors complete
    await wait_for_synthetic_sensors(hass)

    # Then do the assertion
    assert_entity_state(hass, entity_id, expected_state)
