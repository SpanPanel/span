from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ServiceNotFound
import pytest
from span_panel_api.exceptions import SpanPanelServerError

from custom_components.span_panel.select import (
    CIRCUIT_PRIORITY_DESCRIPTION,
    SpanPanelCircuitsSelect,
)

try:
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    _HAS_REAL_CIRCUIT = True
except ImportError:
    # If this fails, you must adjust your PYTHONPATH or test runner so custom_components is importable
    SpanPanelCircuit = None
    _HAS_REAL_CIRCUIT = False


class DummySpanPanel:
    """Dummy span panel class for testing."""

    circuits = {}
    status = MagicMock(serial_number="123")


class DummyCoordinator:
    """Dummy coordinator class for testing."""

    data = DummySpanPanel()


class DummySpanPanelCircuit:
    """Dummy span panel circuit class for testing."""

    def __init__(self, circuit_id, name, tabs, priority, is_user_controllable):
        """Initialize dummy span panel circuit."""
        self.circuit_id = circuit_id
        self.name = name
        self.tabs = tabs
        self.priority = priority
        self.is_user_controllable = is_user_controllable


def test_select_init_missing_circuit():
    coordinator = DummyCoordinator()
    with pytest.raises(ValueError):
        SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "bad_id", "name", "Test Device")


@pytest.mark.asyncio
async def test_async_select_option_service_not_found(monkeypatch):
    if not _HAS_REAL_CIRCUIT:
        import pytest

        pytest.skip("SpanPanelCircuit import failed; adjust PYTHONPATH or test runner.")

    with patch(
        "custom_components.span_panel.select.async_create_span_notification", new_callable=AsyncMock
    ) as mock_notification:
        coordinator = MagicMock()
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.title = "SPAN Panel"
        coordinator.config_entry.data = {}
        coordinator.config_entry.options = {}
        circuit = SpanPanelCircuit(
            circuit_id="id",
            name="name",
            relay_state="CLOSED",
            instant_power=100.0,
            instant_power_update_time=123456,
            produced_energy=0.0,
            consumed_energy=50.0,
            energy_accum_update_time=123456,
            tabs=[1],
            priority="MUST_HAVE",
            is_user_controllable=True,
            is_sheddable=False,
            is_never_backup=False,
        )
        coordinator.data.circuits = {"id": circuit}
        span_panel = coordinator.data
        span_panel.api.set_priority = AsyncMock(
            side_effect=ServiceNotFound("test_domain", "test_service")
        )
        select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "name", "Test Device")
        select.coordinator = coordinator
        select.hass = MagicMock()
        select._get_circuit = MagicMock(return_value=circuit)
        await select.async_select_option("Must Have")

        # Verify notification was called
        mock_notification.assert_called_once()


@pytest.mark.asyncio
async def test_async_select_option_server_error(monkeypatch):
    if not _HAS_REAL_CIRCUIT:
        import pytest

        pytest.skip("SpanPanelCircuit import failed; adjust PYTHONPATH or test runner.")

    with patch(
        "custom_components.span_panel.select.async_create_span_notification", new_callable=AsyncMock
    ) as mock_notification:
        coordinator = MagicMock()
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.title = "SPAN Panel"
        coordinator.config_entry.data = {}
        coordinator.config_entry.options = {}
        circuit = SpanPanelCircuit(
            circuit_id="id",
            name="name",
            relay_state="CLOSED",
            instant_power=100.0,
            instant_power_update_time=123456,
            produced_energy=0.0,
            consumed_energy=50.0,
            energy_accum_update_time=123456,
            tabs=[1],
            priority="MUST_HAVE",
            is_user_controllable=True,
            is_sheddable=False,
            is_never_backup=False,
        )
        coordinator.data.circuits = {"id": circuit}
        span_panel = coordinator.data
        span_panel.api.set_priority = AsyncMock(side_effect=SpanPanelServerError("test error"))
        select = SpanPanelCircuitsSelect(coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "name", "Test Device")
        select.coordinator = coordinator
        select.hass = MagicMock()
        select._get_circuit = MagicMock(return_value=circuit)
        await select.async_select_option("Must Have")

        # Verify notification was called
        mock_notification.assert_called_once()


# Additional async_select_option tests can be added with more mocks for ServiceNotFound, etc.
