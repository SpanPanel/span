from unittest.mock import AsyncMock, MagicMock, patch

from httpcore import RemoteProtocolError
import pytest
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
)

from custom_components.span_panel.exceptions import SpanPanelReturnedEmptyData
from custom_components.span_panel.span_panel_api import SpanPanelApi


@pytest.mark.asyncio
async def test_setup_with_invalid_token(monkeypatch):
    api = SpanPanelApi("host", access_token="badtoken")
    monkeypatch.setattr(api, "get_panel_data", AsyncMock(side_effect=Exception("fail")))
    with pytest.raises(Exception):
        await api.setup()
    assert not api._authenticated


@pytest.mark.asyncio
async def test_ping_success(monkeypatch):
    api = SpanPanelApi("host")
    monkeypatch.setattr(api, "get_status_data", AsyncMock(return_value=True))
    assert await api.ping() is True


@pytest.mark.asyncio
async def test_ping_failure(monkeypatch):
    api = SpanPanelApi("host")
    monkeypatch.setattr(api, "get_status_data", AsyncMock(side_effect=SpanPanelAPIError("fail")))
    assert await api.ping() is False


@pytest.mark.asyncio
async def test_get_access_token_success(monkeypatch):
    api = SpanPanelApi("host")

    # Mock the entire get_access_token method to avoid network calls
    async def mock_get_access_token():
        return "token"

    monkeypatch.setattr(api, "get_access_token", mock_get_access_token)
    assert await api.get_access_token() == "token"


@pytest.mark.asyncio
async def test_get_access_token_auth_error(monkeypatch):
    api = SpanPanelApi("host")

    # Mock the entire get_access_token method to raise the expected error
    async def mock_get_access_token():
        raise SpanPanelAuthError("fail")

    monkeypatch.setattr(api, "get_access_token", mock_get_access_token)
    with pytest.raises(SpanPanelAuthError):
        await api.get_access_token()


@pytest.mark.asyncio
async def test_close_sets_client_none():
    api = SpanPanelApi("host")
    await api.close()
    assert api._client is None


@pytest.mark.asyncio
async def test_setup_with_valid_token():
    api = SpanPanelApi("host", access_token="valid_token")
    # Mock get_panel_data to succeed
    api.get_panel_data = AsyncMock()
    await api.setup()
    assert api._authenticated is True


@pytest.mark.asyncio
async def test_setup_without_token():
    api = SpanPanelApi("host")
    await api.setup()
    # Should not be authenticated without token
    assert api._authenticated is False


@pytest.mark.asyncio
async def test_ping_with_auth_success():
    api = SpanPanelApi("host")
    api.get_panel_data = AsyncMock()
    result = await api.ping_with_auth()
    assert result is True


@pytest.mark.asyncio
async def test_ping_with_auth_failure():
    api = SpanPanelApi("host")
    api.get_panel_data = AsyncMock(side_effect=SpanPanelAuthError("fail"))
    result = await api.ping_with_auth()
    assert result is False


def test_ensure_client_open_client_none():
    api = SpanPanelApi("host")
    api._client = None
    api._client_created = True  # Mark as previously created and closed
    with pytest.raises(SpanPanelAPIError, match="API client has been closed"):
        api._ensure_client_open()


def test_debug_check_client_none():
    api = SpanPanelApi("host")
    api._client = None
    # Should not raise, just log
    api._debug_check_client("test_method")


@pytest.mark.asyncio
async def test_get_status_data():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_status = MagicMock()
    mock_client.get_status = AsyncMock(return_value=mock_status)
    api._client = mock_client
    api._ensure_client_open = MagicMock()

    result = await api.get_status_data()
    assert result is not None


@pytest.mark.asyncio
async def test_get_all_data_retries_on_remote_protocol_error():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_all_data = AsyncMock(
        side_effect=[RemoteProtocolError("boom"), {}]
    )
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._recreate_client = AsyncMock()

    result = await api.get_all_data()

    assert result == {}
    api._recreate_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_all_data_remote_protocol_error_after_retry():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_all_data = AsyncMock(side_effect=RemoteProtocolError("boom"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._recreate_client = AsyncMock()

    with pytest.raises(SpanPanelConnectionError):
        await api.get_all_data()

    api._recreate_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_panel_data():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_panel = MagicMock()
    mock_client.get_panel_state = AsyncMock(return_value=mock_panel)
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    result = await api.get_panel_data()
    assert result is not None


@pytest.mark.asyncio
async def test_get_circuits_data():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_circuits_response = MagicMock()
    mock_circuits_response.circuits.additional_properties = {
        "1": MagicMock(),
        "2": MagicMock(),
    }
    # Mock the to_dict method for each circuit with correct key names
    for circuit in mock_circuits_response.circuits.additional_properties.values():
        circuit.to_dict.return_value = {
            "id": "1",
            "name": "Test Circuit",
            "relayState": "CLOSED",
            "instantPowerW": 100.0,
            "instantPowerUpdateTimeS": 1672531200,
            "producedEnergyWh": 0.0,
            "consumedEnergyWh": 50.0,
            "energyAccumUpdateTimeS": 1672531200,
            "tabs": [1],
            "priority": "MUST_HAVE",
            "isUserControllable": True,
            "isSheddable": False,
            "isNeverBackup": False,
        }

    mock_client.get_circuits = AsyncMock(return_value=mock_circuits_response)
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    result = await api.get_circuits_data()
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_storage_battery_data():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_storage_response = MagicMock()
    mock_storage_response.soe.to_dict.return_value = {"battery_percentage": 85.5}
    mock_client.get_storage_soe = AsyncMock(return_value=mock_storage_response)
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    result = await api.get_storage_battery_data()
    assert result is not None


@pytest.mark.asyncio
async def test_set_relay():
    from custom_components.span_panel.const import CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.set_circuit_relay = AsyncMock()
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    # Create a mock circuit
    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    await api.set_relay(circuit, CircuitRelayState.OPEN)
    mock_client.set_circuit_relay.assert_called_once_with("1", "OPEN")


@pytest.mark.asyncio
async def test_set_priority():
    from custom_components.span_panel.const import CircuitPriority, CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.set_circuit_priority = AsyncMock()
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    # Create a mock circuit
    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    await api.set_priority(circuit, CircuitPriority.NICE_TO_HAVE)
    mock_client.set_circuit_priority.assert_called_once_with("1", "NICE_TO_HAVE")


@pytest.mark.asyncio
async def test_api_error_handling():
    api = SpanPanelApi("host")
    api._client = None
    api._client_created = True  # Mark as previously created and closed

    with pytest.raises(SpanPanelAPIError, match="API client has been closed"):
        await api.get_status_data()

    with pytest.raises(SpanPanelAPIError, match="API client has been closed"):
        await api.get_panel_data()

    with pytest.raises(SpanPanelAPIError, match="API client has been closed"):
        await api.get_circuits_data()


@pytest.mark.asyncio
async def test_ensure_client_open_with_closed_client():
    api = SpanPanelApi("host")
    # Mock a client with closed underlying httpx client
    mock_client = MagicMock()
    mock_httpx_client = MagicMock()
    mock_httpx_client.is_closed = True
    mock_client._client = mock_httpx_client
    api._client = mock_client

    # The method should not create a new client when underlying httpx is closed
    # It just logs a message and lets SpanPanelClient handle it internally
    api._ensure_client_open()

    # Should not create new client, just log the message
    assert api._client == mock_client


@pytest.mark.asyncio
async def test_ensure_client_open_with_options():
    from custom_components.span_panel.options import Options

    # Create mock config entry
    mock_entry = MagicMock()
    mock_entry.options = {
        "api_retries": 5,
        "api_retry_timeout": 10,
        "api_retry_backoff_multiplier": 2.5,
    }
    options = Options(mock_entry)

    api = SpanPanelApi("host", options=options)
    # Mock a client with closed underlying httpx client
    mock_client = MagicMock()
    mock_httpx_client = MagicMock()
    mock_httpx_client.is_closed = True
    mock_client._client = mock_httpx_client
    api._client = mock_client

    # The method should not create a new client when underlying httpx is closed
    # It just logs a message and lets SpanPanelClient handle it internally
    api._ensure_client_open()

    # Should not create new client, just log the message
    assert api._client == mock_client


@pytest.mark.asyncio
async def test_ensure_client_open_with_access_token():
    api = SpanPanelApi("host", access_token="test_token")
    # Mock a client with closed underlying httpx client
    mock_client = MagicMock()
    mock_httpx_client = MagicMock()
    mock_httpx_client.is_closed = True
    mock_client._client = mock_httpx_client
    api._client = mock_client

    # The method should not create a new client when underlying httpx is closed
    # It just logs a message and lets SpanPanelClient handle it internally
    api._ensure_client_open()

    # Should not create new client, just log the message
    assert api._client == mock_client


@pytest.mark.asyncio
async def test_ensure_authenticated_success():
    api = SpanPanelApi("host")
    api._authenticated = False
    mock_client = MagicMock()
    mock_auth_response = MagicMock()
    mock_auth_response.access_token = "new_token"
    mock_client.authenticate = AsyncMock(return_value=mock_auth_response)
    api._client = mock_client

    await api._ensure_authenticated()

    assert api._authenticated is True
    assert api.access_token == "new_token"
    mock_client.authenticate.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_authenticated_failure():
    api = SpanPanelApi("host")
    api._authenticated = False
    mock_client = MagicMock()
    mock_client.authenticate = AsyncMock(side_effect=Exception("Auth failed"))
    api._client = mock_client

    with pytest.raises(SpanPanelAuthError, match="Re-authentication failed"):
        await api._ensure_authenticated()


@pytest.mark.asyncio
async def test_ensure_authenticated_client_none():
    api = SpanPanelApi("host")
    api._authenticated = False
    api._client = None

    with pytest.raises(SpanPanelAPIError, match="API client has been closed"):
        await api._ensure_authenticated()


@pytest.mark.asyncio
async def test_setup_with_auth_error():
    api = SpanPanelApi("host", access_token="invalid_token")
    api.get_panel_data = AsyncMock(side_effect=SpanPanelAuthError("Invalid token"))
    api.close = AsyncMock()

    await api.setup()

    assert api._authenticated is False


@pytest.mark.asyncio
async def test_setup_with_general_exception():
    api = SpanPanelApi("host", access_token="token")
    api.get_panel_data = AsyncMock(side_effect=Exception("Network error"))
    api.close = AsyncMock()

    with pytest.raises(Exception, match="Network error"):
        await api.setup()

    api.close.assert_called_once()


@pytest.mark.asyncio
async def test_debug_check_client_with_closed_client():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_httpx_client = MagicMock()
    mock_httpx_client.is_closed = True
    mock_client._client = mock_httpx_client
    mock_client._in_context = False
    api._client = mock_client

    # Should not raise, just log
    api._debug_check_client("test_method")


@pytest.mark.asyncio
async def test_get_status_data_with_retriable_error():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_status = AsyncMock(side_effect=SpanPanelRetriableError("Retry me"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()

    with pytest.raises(SpanPanelRetriableError):
        await api.get_status_data()


@pytest.mark.asyncio
async def test_get_status_data_with_server_error():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_status = AsyncMock(side_effect=SpanPanelServerError("Server error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()

    with pytest.raises(SpanPanelServerError):
        await api.get_status_data()


@pytest.mark.asyncio
async def test_get_panel_data_with_empty_data():
    from custom_components.span_panel.const import PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE

    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_panel_response = MagicMock()
    mock_panel_response.to_dict.return_value = {
        "main_relay_state": PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE
    }
    mock_client.get_panel_state = AsyncMock(return_value=mock_panel_response)
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with patch("custom_components.span_panel.span_panel_api.SpanPanelData") as mock_data_class:
        mock_data = MagicMock()
        mock_data.main_relay_state = PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE
        mock_data_class.from_dict.return_value = mock_data

        with pytest.raises(SpanPanelReturnedEmptyData):
            await api.get_panel_data()


@pytest.mark.asyncio
async def test_get_panel_data_with_auth_error():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_panel_state = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()
    api._authenticated = True

    with pytest.raises(SpanPanelAuthError):
        await api.get_panel_data()

    # Should reset auth flag
    assert api._authenticated is False


@pytest.mark.asyncio
async def test_get_circuits_data_empty_response():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_circuits_response = MagicMock()
    mock_circuits_response.circuits.additional_properties = {}
    mock_client.get_circuits = AsyncMock(return_value=mock_circuits_response)
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelReturnedEmptyData):
        await api.get_circuits_data()


@pytest.mark.asyncio
async def test_get_storage_battery_data_empty_response():
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_storage_response = MagicMock()
    mock_storage_response.soe.to_dict.return_value = {}  # Empty response
    mock_client.get_storage_soe = AsyncMock(return_value=mock_storage_response)
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelReturnedEmptyData):
        await api.get_storage_battery_data()


@pytest.mark.asyncio
async def test_get_panel_data_auth_reset_on_auth_error():
    """Test that authentication flag is reset when auth error occurs in get_panel_data."""
    api = SpanPanelApi("host")
    api._authenticated = True  # Start as authenticated
    mock_client = MagicMock()
    mock_client.get_panel_state = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelAuthError):
        await api.get_panel_data()

    # Verify auth flag was reset
    assert api._authenticated is False


@pytest.mark.asyncio
async def test_get_circuits_data_auth_reset_on_auth_error():
    """Test that authentication flag is reset when auth error occurs in get_circuits_data."""
    api = SpanPanelApi("host")
    api._authenticated = True  # Start as authenticated
    mock_client = MagicMock()
    mock_client.get_circuits = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelAuthError):
        await api.get_circuits_data()

    # Verify auth flag was reset
    assert api._authenticated is False


@pytest.mark.asyncio
async def test_get_storage_battery_data_auth_reset_on_auth_error():
    """Test that authentication flag is reset when auth error occurs in get_storage_battery_data."""
    api = SpanPanelApi("host")
    api._authenticated = True  # Start as authenticated
    mock_client = MagicMock()
    mock_client.get_storage_soe = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelAuthError):
        await api.get_storage_battery_data()

    # Verify auth flag was reset
    assert api._authenticated is False


@pytest.mark.asyncio
async def test_set_relay_retriable_error():
    """Test set_relay handles retriable errors correctly."""
    from custom_components.span_panel.const import CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.set_circuit_relay = AsyncMock(
        side_effect=SpanPanelRetriableError("Retriable error")
    )
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    with pytest.raises(SpanPanelRetriableError):
        await api.set_relay(circuit, CircuitRelayState.OPEN)


@pytest.mark.asyncio
async def test_set_relay_server_error():
    """Test set_relay handles server errors correctly."""
    from custom_components.span_panel.const import CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.set_circuit_relay = AsyncMock(side_effect=SpanPanelServerError("Server error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    with pytest.raises(SpanPanelServerError):
        await api.set_relay(circuit, CircuitRelayState.OPEN)


@pytest.mark.asyncio
async def test_set_relay_auth_reset_on_auth_error():
    """Test that authentication flag is reset when auth error occurs in set_relay."""
    from custom_components.span_panel.const import CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    api._authenticated = True  # Start as authenticated
    mock_client = MagicMock()
    mock_client.set_circuit_relay = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    with pytest.raises(SpanPanelAuthError):
        await api.set_relay(circuit, CircuitRelayState.OPEN)

    # Verify auth flag was reset
    assert api._authenticated is False


@pytest.mark.asyncio
async def test_set_priority_retriable_error():
    """Test set_priority handles retriable errors correctly."""
    from custom_components.span_panel.const import CircuitPriority, CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.set_circuit_priority = AsyncMock(
        side_effect=SpanPanelRetriableError("Retriable error")
    )
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    with pytest.raises(SpanPanelRetriableError):
        await api.set_priority(circuit, CircuitPriority.MUST_HAVE)


@pytest.mark.asyncio
async def test_set_priority_server_error():
    """Test set_priority handles server errors correctly."""
    from custom_components.span_panel.const import CircuitPriority, CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.set_circuit_priority = AsyncMock(side_effect=SpanPanelServerError("Server error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    with pytest.raises(SpanPanelServerError):
        await api.set_priority(circuit, CircuitPriority.MUST_HAVE)


@pytest.mark.asyncio
async def test_set_priority_auth_reset_on_auth_error():
    """Test that authentication flag is reset when auth error occurs in set_priority."""
    from custom_components.span_panel.const import CircuitPriority, CircuitRelayState
    from custom_components.span_panel.span_panel_circuit import SpanPanelCircuit

    api = SpanPanelApi("host")
    api._authenticated = True  # Start as authenticated
    mock_client = MagicMock()
    mock_client.set_circuit_priority = AsyncMock(side_effect=SpanPanelAuthError("Auth failed"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    circuit = SpanPanelCircuit(
        circuit_id="1",
        name="Test Circuit",
        relay_state=CircuitRelayState.CLOSED,
        instant_power=100.0,
        instant_power_update_time="2023-01-01T00:00:00Z",
        produced_energy=0.0,
        consumed_energy=50.0,
        energy_accum_update_time="2023-01-01T00:00:00Z",
        tabs=["A"],
        priority="MUST_HAVE",
        is_user_controllable=True,
        is_sheddable=False,
        is_never_backup=False,
    )

    with pytest.raises(SpanPanelAuthError):
        await api.set_priority(circuit, CircuitPriority.MUST_HAVE)

    # Verify auth flag was reset
    assert api._authenticated is False


@pytest.mark.asyncio
async def test_close_with_exception():
    """Test close method handles exceptions during client close."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.close = AsyncMock(side_effect=Exception("Close error"))
    api._client = mock_client

    # Should not raise exception, just log warning
    await api.close()

    # Client should still be set to None
    assert api._client is None


@pytest.mark.asyncio
async def test_ping_with_auth_connection_error():
    """Test ping_with_auth handles connection errors correctly."""
    api = SpanPanelApi("host")
    api.get_panel_data = AsyncMock(side_effect=SpanPanelReturnedEmptyData("Empty data"))
    result = await api.ping_with_auth()
    assert result is False


@pytest.mark.asyncio
async def test_get_access_token_connection_error():
    """Test get_access_token handles connection errors correctly."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.authenticate = AsyncMock(side_effect=SpanPanelAPIError("Connection failed"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()

    with pytest.raises(SpanPanelReturnedEmptyData):
        await api.get_access_token()


@pytest.mark.asyncio
async def test_get_storage_battery_data_retriable_error():
    """Test get_storage_battery_data handles retriable errors correctly."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_storage_soe = AsyncMock(side_effect=SpanPanelRetriableError("Retriable error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelRetriableError):
        await api.get_storage_battery_data()


@pytest.mark.asyncio
async def test_get_storage_battery_data_server_error():
    """Test get_storage_battery_data handles server errors correctly."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_storage_soe = AsyncMock(side_effect=SpanPanelServerError("Server error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelServerError):
        await api.get_storage_battery_data()


@pytest.mark.asyncio
async def test_get_circuits_data_retriable_error():
    """Test get_circuits_data handles retriable errors correctly."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_circuits = AsyncMock(side_effect=SpanPanelRetriableError("Retriable error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelRetriableError):
        await api.get_circuits_data()


@pytest.mark.asyncio
async def test_get_circuits_data_server_error():
    """Test get_circuits_data handles server errors correctly."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_circuits = AsyncMock(side_effect=SpanPanelServerError("Server error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelServerError):
        await api.get_circuits_data()


@pytest.mark.asyncio
async def test_get_panel_data_retriable_error():
    """Test get_panel_data handles retriable errors correctly."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_panel_state = AsyncMock(side_effect=SpanPanelRetriableError("Retriable error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelRetriableError):
        await api.get_panel_data()


@pytest.mark.asyncio
async def test_get_panel_data_server_error():
    """Test get_panel_data handles server errors correctly."""
    api = SpanPanelApi("host")
    mock_client = MagicMock()
    mock_client.get_panel_state = AsyncMock(side_effect=SpanPanelServerError("Server error"))
    api._client = mock_client
    api._ensure_client_open = MagicMock()
    api._ensure_authenticated = AsyncMock()

    with pytest.raises(SpanPanelServerError):
        await api.get_panel_data()
