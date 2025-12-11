"""Module to read production and consumption values from a Span panel."""

from datetime import datetime
import logging
from time import time as _epoch_time

from .exceptions import SpanPanelReturnedEmptyData, SpanPanelSimulationOfflineError
from .options import Options
from .span_panel_api import SpanPanelApi
from .span_panel_circuit import SpanPanelCircuit
from .span_panel_data import SpanPanelData
from .span_panel_hardware_status import SpanPanelHardwareStatus
from .span_panel_storage_battery import SpanPanelStorageBattery

STATUS_URL = "http://{}/api/v1/status"
SPACES_URL = "http://{}/api/v1/spaces"
CIRCUITS_URL = "http://{}/api/v1/circuits"
PANEL_URL = "http://{}/api/v1/panel"
REGISTER_URL = "http://{}/api/v1/auth/register"
STORAGE_BATTERY_URL = "http://{}/api/v1/storage/soe"

_LOGGER: logging.Logger = logging.getLogger(__name__)

SPAN_CIRCUITS = "circuits"
SPAN_SYSTEM = "system"
PANEL_POWER = "instantGridPowerW"
SYSTEM_DOOR_STATE = "doorState"
SYSTEM_DOOR_STATE_CLOSED = "CLOSED"
SYSTEM_DOOR_STATE_OPEN = "OPEN"
SYSTEM_ETHERNET_LINK = "eth0Link"
SYSTEM_CELLULAR_LINK = "wwanLink"
SYSTEM_WIFI_LINK = "wlanLink"


class SpanPanel:
    """Class to manage the Span Panel."""

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
        """Initialize the Span Panel."""
        self._options = options
        self.api = SpanPanelApi(
            host,
            access_token,
            options,
            use_ssl,
            scan_interval,
            simulation_mode,
            simulation_config_path,
            simulation_start_time,
            simulation_offline_minutes,
        )
        self._status: SpanPanelHardwareStatus | None = None
        self._panel: SpanPanelData | None = None
        self._circuits: dict[str, SpanPanelCircuit] = {}
        self._storage_battery: SpanPanelStorageBattery | None = None

    def _get_hardware_status(self) -> SpanPanelHardwareStatus:
        """Get hardware status with type checking."""
        if self._status is None:
            raise RuntimeError("Hardware status not available")
        return self._status

    def _get_data(self) -> SpanPanelData:
        """Get data with type checking."""
        if self._panel is None:
            raise RuntimeError("Panel data not available")
        return self._panel

    def _get_storage_battery(self) -> SpanPanelStorageBattery:
        """Get storage battery with type checking."""
        if self._storage_battery is None:
            raise RuntimeError("Storage battery not available")
        return self._storage_battery

    @property
    def host(self) -> str:
        """Return the host of the panel."""
        return self.api.host

    @property
    def options(self) -> Options | None:
        """Get options data atomically."""
        return self._options

    def _update_status(self, new_status: SpanPanelHardwareStatus) -> None:
        """Atomic update of status data."""
        self._status = new_status

    def _update_panel(self, new_panel: SpanPanelData) -> None:
        """Atomic update of panel data."""
        self._panel = new_panel

    def _update_circuits(self, new_circuits: dict[str, SpanPanelCircuit]) -> None:
        """Atomic update of circuits data."""
        circuit_keys = list(new_circuits.keys())
        _LOGGER.debug("Updating circuits. Total: %s", len(circuit_keys))

        self._circuits = new_circuits

    def _update_storage_battery(self, new_battery: SpanPanelStorageBattery) -> None:
        """Atomic update of storage battery data."""
        self._storage_battery = new_battery

    async def update(self) -> None:
        """Update all panel data atomically."""
        try:
            # Start timing for API calls
            api_start = _epoch_time()

            # Debug battery option status
            battery_option_enabled = self._options and self._options.enable_battery_percentage

            # Use batch API call for true parallelization at the client level
            # This makes concurrent HTTP requests when cache misses occur
            all_data = await self.api.get_all_data(include_battery=bool(battery_option_enabled))

            # Extract processed data from batch response
            new_status = all_data.get("status")
            new_panel = all_data.get("panel")
            new_circuits = all_data.get("circuits")
            new_battery = all_data.get("battery") if battery_option_enabled else None

            # Atomic updates - ensure we have the required data
            if new_status is not None:
                self._update_status(new_status)
            if new_panel is not None:
                self._update_panel(new_panel)
            if new_circuits is not None:
                self._update_circuits(new_circuits)

            if new_battery is not None:
                self._update_storage_battery(new_battery)

            api_duration = _epoch_time() - api_start

            # INFO level logging for API call performance
            _LOGGER.info("Panel API calls completed (CLIENT-PARALLEL) - Total: %.3fs", api_duration)

            _LOGGER.debug("Panel update completed successfully")
        except SpanPanelReturnedEmptyData:
            _LOGGER.warning("Span Panel returned empty data")
        except SpanPanelSimulationOfflineError:  # Debug logged in coordinator.py
            raise
        except Exception as err:
            # Keep the message concise to avoid noisy tracebacks; the coordinator
            # will mark panel_offline and grace-period logic will take over.
            _LOGGER.warning(
                "Panel update failed (%s); marking offline",
                err,
            )
            raise

    @property
    def status(self) -> SpanPanelHardwareStatus:
        """Get status data atomically."""
        result = self._get_hardware_status()
        return result

    @property
    def panel(self) -> SpanPanelData:
        """Get panel data atomically."""
        return self._get_data()

    @property
    def circuits(self) -> dict[str, SpanPanelCircuit]:
        """Get circuits data atomically."""
        return self._circuits

    @property
    def storage_battery(self) -> SpanPanelStorageBattery:
        """Get storage battery data atomically."""
        result = self._get_storage_battery()
        return result

    async def close(self) -> None:
        """Close the API client and clean up resources."""
        await self.api.close()
