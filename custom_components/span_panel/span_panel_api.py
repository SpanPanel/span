"""Span Panel API - Updated to use span-panel-api package."""

from copy import deepcopy
from datetime import datetime
import logging
import os
from typing import Any
import uuid

from span_panel_api import PanelCapability, SpanPanelClient, set_async_delay_func
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)

from .const import (
    API_TIMEOUT,
    DEFAULT_API_RETRIES,
    DEFAULT_API_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_API_RETRY_TIMEOUT,
    PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE,
    CircuitPriority,
    CircuitRelayState,
)
from .exceptions import SpanPanelReturnedEmptyData, SpanPanelSimulationOfflineError
from .options import Options
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery

_LOGGER: logging.Logger = logging.getLogger(__name__)


class SpanPanelApi:
    """Span Panel API - Now using span-panel-api package."""

    def __init__(
        self,
        host: str,
        access_token: str | None = None,  # nosec
        options: Options | None = None,
        use_ssl: bool = False,
        scan_interval: int | None = None,
        simulation_mode: bool = False,
        simulation_config_path: str | None = None,
        simulation_start_time: datetime | None = None,
        simulation_offline_minutes: int = 0,
    ) -> None:
        """Initialize the Span Panel API."""
        # For simulation mode, keep the original host (which should be the serial number)
        # For real hardware, normalize to lowercase
        self.host: str = host if simulation_mode else host.lower()
        self.access_token: str | None = access_token
        self.options: Options | None = options
        self.scan_interval: int = scan_interval or 15  # Default to 15 seconds
        self.use_ssl: bool = use_ssl
        self.simulation_mode: bool = simulation_mode
        self.simulation_config_path: str | None = simulation_config_path
        self.simulation_start_time: datetime | None = simulation_start_time
        self.simulation_offline_minutes: int = simulation_offline_minutes
        # Separate timer for offline mode - when the offline period started
        # Use simulation_start_time as the offline start time when offline mode is enabled
        # Explicitly declare attribute type for mypy
        self.offline_start_time: datetime | None = None
        if simulation_mode and simulation_offline_minutes > 0 and simulation_start_time:
            self.offline_start_time = simulation_start_time

        # Initialize client as None - will be created in setup()
        self._authenticated = False

        # Store client parameters for lazy initialization
        # Get retry configuration from options or use defaults
        if options:
            self._retries = options.api_retries
            self._retry_timeout = options.api_retry_timeout
            self._retry_backoff_multiplier = options.api_retry_backoff_multiplier
        else:
            self._retries = DEFAULT_API_RETRIES
            self._retry_timeout = DEFAULT_API_RETRY_TIMEOUT
            self._retry_backoff_multiplier = DEFAULT_API_RETRY_BACKOFF_MULTIPLIER

        # Initialize client as None - will be created in setup()
        self._client: SpanPanelClient | None = None

    def _is_panel_offline(self) -> bool:
        """Check if the panel should be offline based on simulation settings.

        Returns:
            True if the panel should appear offline, False otherwise.

        """
        if not self.simulation_mode or self.simulation_offline_minutes <= 0:
            _LOGGER.debug(
                "[SpanPanelApi] Panel not offline: simulation_mode=%s, offline_minutes=%s",
                self.simulation_mode,
                self.simulation_offline_minutes,
            )
            return False

        if not self.offline_start_time:
            _LOGGER.debug("[SpanPanelApi] Panel not offline: no offline start time")
            return False

        # Calculate how many minutes have passed since offline period started
        now = datetime.now()
        elapsed_minutes = (now - self.offline_start_time).total_seconds() / 60

        is_offline = elapsed_minutes < self.simulation_offline_minutes
        _LOGGER.debug(
            "[SpanPanelApi] Panel offline check: offline_start_time=%s, elapsed_minutes=%.2f, offline_minutes=%s, is_offline=%s",
            self.offline_start_time,
            elapsed_minutes,
            self.simulation_offline_minutes,
            is_offline,
        )

        # Panel is offline for the specified number of minutes after offline period starts
        return is_offline

    def set_simulation_offline_mode(self, offline_minutes: int) -> None:
        """Set the offline simulation mode for simulators only.

        Args:
            offline_minutes: Number of minutes the panel should appear offline (0 to disable)

        """
        _LOGGER.info(
            "[SpanPanelApi] set_simulation_offline_mode called: offline_minutes=%s, simulation_mode=%s",
            offline_minutes,
            self.simulation_mode,
        )

        if not self.simulation_mode:
            _LOGGER.warning("[SpanPanelApi] Cannot set offline mode: not in simulation mode")
            return

        self.simulation_offline_minutes = offline_minutes

        # Set offline start time to "now" if offline mode is enabled, otherwise clear it
        if offline_minutes > 0:
            self.offline_start_time = datetime.now()
            _LOGGER.info(
                "[SpanPanelApi] Set simulation offline mode: %s minutes starting now (%s)",
                offline_minutes,
                self.offline_start_time,
            )
        else:
            self.offline_start_time = None
            _LOGGER.info("[SpanPanelApi] Disabled simulation offline mode")

    def _create_client(self) -> None:
        """Create the SpanPanelClient with stored parameters."""
        _LOGGER.debug("[SpanPanelApi] Creating SpanPanelClient for host=%s", self.host)

        # Determine simulation config path if in simulation mode
        config_path = None
        if self.simulation_mode:
            if self.simulation_config_path:
                config_path = self.simulation_config_path
            else:
                # Use default 32-circuit config relative to this file
                current_dir = os.path.dirname(__file__)
                config_path = os.path.join(
                    current_dir, "simulation_configs", "simulation_config_32_circuit.yaml"
                )
                _LOGGER.debug("[SpanPanelApi] Using default simulation config: %s", config_path)

        # Create client with appropriate parameters based on mode
        if self.simulation_mode:
            # Convert datetime to ISO format string if provided
            simulation_start_time_str = None
            if self.simulation_start_time:
                simulation_start_time_str = self.simulation_start_time.isoformat()

            self._client = SpanPanelClient(
                host=self.host,
                timeout=API_TIMEOUT,
                use_ssl=self.use_ssl,
                retries=self._retries,
                retry_timeout=self._retry_timeout,
                retry_backoff_multiplier=self._retry_backoff_multiplier,
                simulation_mode=self.simulation_mode,
                simulation_config_path=config_path,
                simulation_start_time=simulation_start_time_str,
            )
        else:
            # For live panels, don't pass simulation parameters
            self._client = SpanPanelClient(
                host=self.host,
                timeout=API_TIMEOUT,
                use_ssl=self.use_ssl,
                retries=self._retries,
                retry_timeout=self._retry_timeout,
                retry_backoff_multiplier=self._retry_backoff_multiplier,
            )
        if self.access_token:
            self._client.set_access_token(self.access_token)
            # Mark as authenticated since we have a token - avoid unnecessary re-auth
            self._authenticated = True

    def _ensure_client_open(self) -> None:
        # Check if client was explicitly closed (None and we've tried to create it before)
        if self._client is None and hasattr(self, "_client_created"):
            _LOGGER.debug(
                "[SpanPanelApi] Client was closed, cannot recreate after explicit close for host=%s",
                self.host,
            )
            raise SpanPanelAPIError("API client has been closed")

        # Create client if it doesn't exist yet
        if self._client is None:
            self._create_client()
            self._client_created = True
            return

        client_obj = getattr(self._client, "_client", None)
        if client_obj is not None and getattr(client_obj, "is_closed", False):
            _LOGGER.debug(
                "[SpanPanelApi] Underlying httpx client is closed for host=%s (SSL=%s), will be recreated on next use",
                self.host,
                self.use_ssl,
            )
            # Let the SpanPanelClient handle closed connections internally
            # Don't interfere with its connection management - it will create new connections as needed

    def _debug_check_client(self, method_name: str) -> None:
        # Check if the client is in a closed or invalid state
        if self._client is None:
            _LOGGER.error(
                "[SpanPanelApi] Client is None in %s! This indicates a bug in the lifecycle management.",
                method_name,
            )
            return

        client_obj = getattr(self._client, "_client", None)
        is_closed = False
        if client_obj is not None:
            # httpx.AsyncClient has a .is_closed property
            is_closed = getattr(client_obj, "is_closed", False)
        if is_closed:
            _LOGGER.error(
                "[SpanPanelApi] Attempting to use a closed client in %s! This will cause runtime errors.",
                method_name,
            )

    async def setup(self) -> None:
        """Initialize the API client (Long-Lived Pattern setup)."""
        _LOGGER.debug("[SpanPanelApi] Setting up API client for host=%s", self.host)

        # Create the client first
        if self._client is None:
            self._create_client()
            self._client_created = True

        try:
            # If we have a token, verify it works
            if self.access_token:
                _LOGGER.debug("[SpanPanelApi] Testing existing access token")
                await self.get_panel_data()  # Test authenticated endpoint
                # _authenticated should already be True from _create_client
                _LOGGER.debug("[SpanPanelApi] Existing access token is valid")
            else:
                _LOGGER.debug("[SpanPanelApi] No access token provided during setup")
        except SpanPanelAuthError:
            _LOGGER.warning(
                "[SpanPanelApi] Access token invalid, will re-authenticate on first use"
            )
            self._authenticated = False
        except Exception as e:
            _LOGGER.error("[SpanPanelApi] Setup failed: %s", e)
            self._authenticated = False
            await self.close()  # Clean up on setup failure
            raise

    async def _ensure_authenticated(self) -> None:
        """Ensure we have valid authentication, re-authenticate if needed."""
        if not self._authenticated:
            _LOGGER.debug("[SpanPanelApi] Re-authentication needed")
            if self._client is None:
                raise SpanPanelAPIError("API client has been closed")
            try:
                # Generate a unique client name for re-authentication
                client_name = f"home-assistant-{uuid.uuid4()}"
                auth_response = await self._client.authenticate(
                    client_name, "Home Assistant Local Span Integration"
                )
                self.access_token = auth_response.access_token
                self._authenticated = True
                _LOGGER.debug("[SpanPanelApi] Re-authentication successful")
            except Exception as e:
                _LOGGER.error("[SpanPanelApi] Re-authentication failed: %s", e)
                raise SpanPanelAuthError(f"Re-authentication failed: {e}") from e

    async def ping(self) -> bool:
        """Ping the Span Panel API."""
        # Check if panel should be offline in simulation mode
        if self._is_panel_offline():
            raise SpanPanelSimulationOfflineError("Panel is offline in simulation mode")

        self._ensure_client_open()
        if self._client is None:
            return False
        # status endpoint doesn't require auth.
        try:
            await self.get_status_data()
            return True
        except (SpanPanelConnectionError, SpanPanelTimeoutError, SpanPanelAPIError):
            return False

    async def ping_with_auth(self) -> bool:
        """Test connection and authentication."""
        # Check if panel should be offline in simulation mode
        if self._is_panel_offline():
            raise SpanPanelSimulationOfflineError("Panel is offline in simulation mode")

        self._ensure_client_open()
        if self._client is None:
            return False
        try:
            # Use get_panel_data() since it requires authentication
            await self.get_panel_data()
            return True
        except SpanPanelAuthError:
            return False
        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelReturnedEmptyData,
        ):
            return False
        except Exception as e:
            # Catch any other authentication-related errors from span-panel-api
            _LOGGER.warning("Unexpected error during authentication test: %s", e)
            return False

    async def get_access_token(self) -> str:
        """Get the access token."""
        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")
        try:
            # Generate a unique client name
            client_name = f"home-assistant-{uuid.uuid4()}"
            auth_response = await self._client.authenticate(
                client_name, "Home Assistant Local Span Integration"
            )

            # Store the token
            self.access_token = auth_response.access_token
            return str(auth_response.access_token)

        except (SpanPanelConnectionError, SpanPanelAPIError) as e:
            raise SpanPanelReturnedEmptyData(f"Failed to get access token: {e}") from e

    async def get_status_data(self) -> SpanPanelHardwareStatus:
        """Get the status data."""
        # Check if panel should be offline in simulation mode
        if self._is_panel_offline():
            raise SpanPanelSimulationOfflineError("Panel is offline in simulation mode")

        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")
        self._debug_check_client("get_status_data")
        try:
            status_response = await self._client.get_status()

            # Convert the attrs model to dict and then to our data class
            status_dict = status_response.to_dict()
            status_data = SpanPanelHardwareStatus.from_dict(status_dict)
            return status_data

        except SpanPanelRetriableError as e:
            _LOGGER.warning("Retriable error getting status data (will retry): %s", e)
            raise
        except SpanPanelServerError as e:
            _LOGGER.error("Server error getting status data (will not retry): %s", e)
            raise
        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ) as e:
            _LOGGER.error("Failed to get status data: %s", e)
            raise

    async def get_panel_data(self) -> SpanPanelData:
        """Get the panel data."""
        # Check if panel should be offline in simulation mode
        if self._is_panel_offline():
            raise SpanPanelSimulationOfflineError("Panel is offline in simulation mode")

        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")
        self._debug_check_client("get_panel_data")
        try:
            await self._ensure_authenticated()
            panel_response = await self._client.get_panel_state()

            # Convert the attrs model to dict and deep copy before processing
            raw_data: Any = deepcopy(panel_response.to_dict())
            panel_data: SpanPanelData = SpanPanelData.from_dict(raw_data, self.options)

            # Span Panel API might return empty result.
            # We use relay state == UNKNOWN as an indication of that scenario.
            if panel_data.main_relay_state == PANEL_MAIN_RELAY_STATE_UNKNOWN_VALUE:
                raise SpanPanelReturnedEmptyData()

            return panel_data

        except SpanPanelRetriableError as e:
            _LOGGER.warning("Retriable error getting panel data (will retry): %s", e)
            raise
        except SpanPanelServerError as e:
            _LOGGER.error("Server error getting panel data (will not retry): %s", e)
            raise
        except SpanPanelAuthError as e:
            # Reset auth flag and let coordinator handle retry
            self._authenticated = False
            _LOGGER.error("Authentication failed for panel data: %s", e)
            raise
        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ) as e:
            _LOGGER.error("Failed to get panel data: %s", e)
            raise

    async def get_circuits_data(self) -> dict[str, SpanPanelCircuit]:
        """Get the circuits data."""
        # Check if panel should be offline in simulation mode
        if self._is_panel_offline():
            raise SpanPanelSimulationOfflineError("Panel is offline in simulation mode")

        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")
        self._debug_check_client("get_circuits_data")
        try:
            await self._ensure_authenticated()
            circuits_response = await self._client.get_circuits()

            # Extract circuits from the response
            raw_circuits_data = circuits_response.circuits.additional_properties

            if not raw_circuits_data:
                raise SpanPanelReturnedEmptyData()

            circuits_data: dict[str, SpanPanelCircuit] = {}
            for circuit_id, raw_circuit_data in raw_circuits_data.items():
                # Convert attrs model to dict
                try:
                    circuit_dict = raw_circuit_data.to_dict()
                    circuits_data[circuit_id] = SpanPanelCircuit.from_dict(circuit_dict)
                except Exception as e:
                    if circuit_id.startswith("unmapped_tab_"):
                        _LOGGER.error("Failed to convert unmapped circuit %s: %s", circuit_id, e)
                    raise

            return circuits_data

        except SpanPanelRetriableError as e:
            _LOGGER.warning("Retriable error getting circuits data (will retry): %s", e)
            raise
        except SpanPanelServerError as e:
            _LOGGER.error("Server error getting circuits data (will not retry): %s", e)
            raise
        except SpanPanelAuthError as e:
            # Reset auth flag and let coordinator handle retry
            self._authenticated = False
            _LOGGER.error("Authentication failed for circuits data: %s", e)
            raise
        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ) as e:
            _LOGGER.error("Failed to get circuits data: %s", e)
            raise

    async def get_storage_battery_data(self) -> SpanPanelStorageBattery:
        """Get the storage battery data."""
        # Check if panel should be offline in simulation mode
        if self._is_panel_offline():
            raise SpanPanelSimulationOfflineError("Panel is offline in simulation mode")

        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")
        self._debug_check_client("get_storage_battery_data")
        try:
            await self._ensure_authenticated()
            storage_response = await self._client.get_storage_soe()

            # Extract SOE data from the response
            storage_battery_data = storage_response.soe.to_dict()

            # Span Panel API might return empty result.
            if not storage_battery_data:
                raise SpanPanelReturnedEmptyData()

            return SpanPanelStorageBattery.from_dict(storage_battery_data)

        except SpanPanelRetriableError as e:
            _LOGGER.warning("Retriable error getting storage battery data (will retry): %s", e)
            raise
        except SpanPanelServerError as e:
            _LOGGER.error("Server error getting storage battery data (will not retry): %s", e)
            raise
        except SpanPanelAuthError as e:
            # Reset auth flag and let coordinator handle retry
            self._authenticated = False
            _LOGGER.error("Authentication failed for storage battery data: %s", e)
            raise
        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ) as e:
            _LOGGER.error("Failed to get storage battery data: %s", e)
            raise

    async def set_relay(self, circuit: SpanPanelCircuit, state: CircuitRelayState) -> None:
        """Set the relay state."""
        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")
        self._debug_check_client("set_relay")
        try:
            await self._ensure_authenticated()
            await self._client.set_circuit_relay(circuit.circuit_id, state.name)

        except SpanPanelRetriableError as e:
            _LOGGER.warning("Retriable error setting relay state (will retry): %s", e)
            raise
        except SpanPanelServerError as e:
            _LOGGER.error("Server error setting relay state (will not retry): %s", e)
            raise
        except SpanPanelAuthError as e:
            # Reset auth flag and let coordinator handle retry
            self._authenticated = False
            _LOGGER.error("Authentication failed for set relay: %s", e)
            raise
        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ) as e:
            _LOGGER.error("Failed to set relay state: %s", e)
            raise

    async def set_priority(self, circuit: SpanPanelCircuit, priority: CircuitPriority) -> None:
        """Set the priority."""
        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")
        self._debug_check_client("set_priority")
        try:
            await self._ensure_authenticated()
            await self._client.set_circuit_priority(circuit.circuit_id, priority.name)

        except SpanPanelRetriableError as e:
            _LOGGER.warning("Retriable error setting priority (will retry): %s", e)
            raise
        except SpanPanelServerError as e:
            _LOGGER.error("Server error setting priority (will not retry): %s", e)
            raise
        except SpanPanelAuthError as e:
            # Reset auth flag and let coordinator handle retry
            self._authenticated = False
            _LOGGER.error("Authentication failed for set priority: %s", e)
            raise
        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ) as e:
            _LOGGER.error("Failed to set priority: %s", e)
            raise

    async def get_all_data(self, include_battery: bool = False) -> dict[str, Any]:
        """Get all panel data in parallel for maximum performance.

        Args:
            include_battery: Whether to include battery/storage data

        Returns:
            Dictionary containing all panel data with proper processing applied

        """
        # Check if panel should be offline in simulation mode
        if self._is_panel_offline():
            raise SpanPanelSimulationOfflineError("Panel is offline in simulation mode")

        self._ensure_client_open()
        if self._client is None:
            raise SpanPanelAPIError("API client has been closed")

        try:
            # Use the client's parallel batch method
            raw_data = await self._client.get_all_data(include_battery=include_battery)

            # Process data using the same logic as individual methods
            result: dict[str, Any] = {}

            if raw_data.get("status"):
                # Process status data (same as get_status_data)
                status_out = raw_data["status"]
                status_dict = status_out.to_dict()
                result["status"] = SpanPanelHardwareStatus.from_dict(status_dict)

            if raw_data.get("panel_state"):
                # Process panel data (same as get_panel_data)
                panel_state = raw_data["panel_state"]
                panel_dict = deepcopy(panel_state.to_dict())
                result["panel"] = SpanPanelData.from_dict(panel_dict, self.options)

            if raw_data.get("circuits"):
                # Process circuits data (same as get_circuits_data)
                circuits_out = raw_data["circuits"]
                circuits_dict: dict[str, SpanPanelCircuit] = {}
                if hasattr(circuits_out, "circuits") and hasattr(
                    circuits_out.circuits, "additional_properties"
                ):
                    for (
                        circuit_id,
                        raw_circuit_data,
                    ) in circuits_out.circuits.additional_properties.items():
                        # Convert attrs model to dict (same as get_circuits_data)
                        circuit_dict = raw_circuit_data.to_dict()
                        circuits_dict[circuit_id] = SpanPanelCircuit.from_dict(circuit_dict)
                result["circuits"] = circuits_dict

            if include_battery and raw_data.get("storage"):
                # Process battery data (same as get_storage_battery_data)
                battery_storage = raw_data["storage"]
                storage_battery_data = battery_storage.soe.to_dict()
                result["battery"] = SpanPanelStorageBattery.from_dict(storage_battery_data)

            return result

        except (
            SpanPanelConnectionError,
            SpanPanelTimeoutError,
            SpanPanelAPIError,
        ) as e:
            _LOGGER.error("Failed to get all panel data: %s", e)
            raise

    @property
    def capabilities(self) -> PanelCapability:
        """Return the panel's capabilities.

        Reads directly from the underlying client so the value reflects the
        connected transport (GEN2_FULL for OpenAPI/HTTP, GEN3_INITIAL for gRPC).
        Falls back to GEN2_FULL when the client has not yet been created.
        """
        if self._client is not None:
            return self._client.capabilities
        return PanelCapability.GEN2_FULL

    async def close(self) -> None:
        """Close the API client and clean up resources."""
        _LOGGER.debug("[SpanPanelApi] Closing API client for host=%s", self.host)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:
                _LOGGER.warning("Error closing API client: %s", e)
            finally:
                # Reset client reference to prevent further use
                self._client = None


# Re-export items that are imported by __init__.py
# Options is already imported above, SpanPanelAuthError comes from span_panel_api package

# Export list for this module
__all__ = ["SpanPanelApi", "Options", "SpanPanelAuthError", "set_async_delay_func"]
