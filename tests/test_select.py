"""Tests for select entity functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ServiceNotFound
import pytest
from span_panel_api.exceptions import SpanPanelServerError

from custom_components.span_panel.const import CircuitPriority
from tests.factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory


def _make_coordinator_with_circuit(
    circuit_id: str = "id",
    circuit_name: str = "name",
    priority: str = "SOC_THRESHOLD",
) -> MagicMock:
    """Build a mock coordinator whose .data contains a single circuit."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id=circuit_id,
        name=circuit_name,
        relay_state="CLOSED",
        instant_power_w=100.0,
        produced_energy_wh=0.0,
        consumed_energy_wh=50.0,
        tabs=[1],
        priority=priority,
        is_user_controllable=True,
    )

    snapshot = SpanPanelSnapshotFactory.create(
        circuits={circuit_id: circuit},
    )

    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.title = "SPAN Panel"
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {}
    return coordinator


def test_select_init_missing_circuit() -> None:
    """Test that initializing with a missing circuit_id raises ValueError."""
    from custom_components.span_panel.select import (
        CIRCUIT_PRIORITY_DESCRIPTION,
        SpanPanelCircuitsSelect,
    )

    # Coordinator with no circuits
    snapshot = SpanPanelSnapshotFactory.create(circuits={})
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.data = {}
    coordinator.config_entry.options = {}

    with pytest.raises(ValueError):
        SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "bad_id", "name", "Test Device"
        )


@pytest.mark.asyncio
async def test_async_select_option_service_not_found() -> None:
    """Test that ServiceNotFound triggers a notification."""
    from custom_components.span_panel.select import (
        CIRCUIT_PRIORITY_DESCRIPTION,
        SpanPanelCircuitsSelect,
    )

    coordinator = _make_coordinator_with_circuit()
    circuit = coordinator.data.circuits["id"]

    with patch(
        "custom_components.span_panel.select.async_create_span_notification",
        new_callable=AsyncMock,
    ) as mock_notification:
        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "name", "Test Device"
        )
        select.coordinator = coordinator
        select.hass = MagicMock()

        # Make the client's set_circuit_priority raise ServiceNotFound
        coordinator.client = AsyncMock()
        coordinator.client.set_circuit_priority = AsyncMock(
            side_effect=ServiceNotFound("test_domain", "test_service")
        )

        select._get_circuit = MagicMock(return_value=circuit)
        await select.async_select_option(CircuitPriority.SOC_THRESHOLD.value)

        mock_notification.assert_called_once()


@pytest.mark.asyncio
async def test_async_select_option_server_error() -> None:
    """Test that SpanPanelServerError triggers a notification."""
    from custom_components.span_panel.select import (
        CIRCUIT_PRIORITY_DESCRIPTION,
        SpanPanelCircuitsSelect,
    )

    coordinator = _make_coordinator_with_circuit()
    circuit = coordinator.data.circuits["id"]

    with patch(
        "custom_components.span_panel.select.async_create_span_notification",
        new_callable=AsyncMock,
    ) as mock_notification:
        select = SpanPanelCircuitsSelect(
            coordinator, CIRCUIT_PRIORITY_DESCRIPTION, "id", "name", "Test Device"
        )
        select.coordinator = coordinator
        select.hass = MagicMock()

        coordinator.client = AsyncMock()
        coordinator.client.set_circuit_priority = AsyncMock(
            side_effect=SpanPanelServerError("test error")
        )

        select._get_circuit = MagicMock(return_value=circuit)
        await select.async_select_option(CircuitPriority.SOC_THRESHOLD.value)

        mock_notification.assert_called_once()
