"""Span Panel Config Flow."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import enum
import logging
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowContext,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import selector
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util import slugify
from homeassistant.util.network import is_ipv4_address
from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError
from span_panel_api.phase_validation import (
    are_tabs_opposite_phase,
    get_tab_phase,
    validate_solar_tabs,
)
from span_panel_api.simulation import DynamicSimulationEngine, SimulationConfig
import voluptuous as vol
import yaml

from custom_components.span_panel.span_panel_hardware_status import (
    SpanPanelHardwareStatus,
)

from .const import (
    CONF_API_RETRIES,
    CONF_API_RETRY_BACKOFF_MULTIPLIER,
    CONF_API_RETRY_TIMEOUT,
    CONF_SIMULATION_CONFIG,
    CONF_SIMULATION_OFFLINE_MINUTES,
    CONF_SIMULATION_START_TIME,
    CONF_USE_SSL,
    CONFIG_API_RETRIES,
    CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    CONFIG_API_RETRY_TIMEOUT,
    CONFIG_TIMEOUT,
    COORDINATOR,
    DEFAULT_API_RETRIES,
    DEFAULT_API_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_API_RETRY_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTITY_NAMING_PATTERN,
    ISO_DATETIME_FORMAT,
    TIME_ONLY_FORMATS,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from .options import (
    BATTERY_ENABLE,
    ENERGY_DISPLAY_PRECISION,
    ENERGY_REPORTING_GRACE_PERIOD,
    INVERTER_ENABLE,
    INVERTER_LEG1,
    INVERTER_LEG2,
    POWER_DISPLAY_PRECISION,
)
from .simulation_utils import clone_panel_to_simulation
from .span_panel_api import SpanPanelApi

_LOGGER = logging.getLogger(__name__)


# Simulation config import/export option keys
SIM_FILE_KEY = "simulation_config_file"
SIM_EXPORT_PATH = "simulation_export_path"
SIM_IMPORT_PATH = "simulation_import_path"


def get_available_simulation_configs() -> dict[str, str]:
    """Get available simulation configuration files.

    Returns:
        Dictionary mapping config keys to display names

    """
    configs = {}

    # Get the integration's simulation_configs directory
    current_file = Path(__file__)
    config_dir = current_file.parent / "simulation_configs"

    if config_dir.exists():
        for yaml_file in config_dir.glob("*.yaml"):
            config_key = yaml_file.stem

            # Create user-friendly display names from filename
            display_name = config_key.replace("simulation_config_", "").replace("_", " ").title()

            configs[config_key] = display_name

    # If no configs found, provide a default
    if not configs:
        configs["simulation_config_32_circuit"] = "32-Circuit Residential Panel (Default)"

    return configs


async def get_available_unmapped_tabs(hass: HomeAssistant, config_entry: ConfigEntry) -> list[int]:
    """Get list of available unmapped tab numbers from panel data.

    Args:
        hass: Home Assistant instance
        config_entry: Configuration entry for this integration

    Returns:
        List of unmapped tab numbers available for solar configuration

    """
    try:
        # Get the coordinator from the integration's data
        coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
        panel_data = coordinator.data

        if not panel_data or not hasattr(panel_data, "circuits"):
            return []

        # Get all tab numbers from circuits that start with "unmapped_tab_"
        unmapped_tabs = []
        for circuit_id in panel_data.circuits:
            if circuit_id.startswith("unmapped_tab_"):
                try:
                    tab_number = int(circuit_id.replace("unmapped_tab_", ""))
                    unmapped_tabs.append(tab_number)
                except ValueError:
                    continue

        return sorted(unmapped_tabs)

    except (KeyError, AttributeError) as e:
        _LOGGER.warning("Could not get unmapped tabs from panel data: %s", e)
        return []


def validate_solar_tab_selection(
    tab1: int, tab2: int, available_tabs: list[int]
) -> tuple[bool, str]:
    """Validate solar tab selection for proper 240V configuration.

    Args:
        tab1: First selected tab number
        tab2: Second selected tab number
        available_tabs: List of available unmapped tab numbers

    Returns:
        tuple of (is_valid, error_message) where:
        - is_valid: True if selection is valid for 240V solar
        - error_message: Description of validation result or error

    """
    # Check if both tabs are provided
    if tab1 == 0 or tab2 == 0:
        return (
            False,
            "Both solar legs must be selected. Single leg configuration is not supported for proper 240V measurement.",
        )

    # Check if tabs are the same
    if tab1 == tab2:
        return (
            False,
            f"Solar legs cannot use the same tab ({tab1}). Two different tabs are required for 240V measurement.",
        )

    # Check if both tabs are available (unmapped)
    if tab1 not in available_tabs:
        return False, f"Tab {tab1} is not available or is already mapped to a circuit."

    if tab2 not in available_tabs:
        return False, f"Tab {tab2} is not available or is already mapped to a circuit."

    # Use phase validation from the API package
    is_valid, message = validate_solar_tabs(tab1, tab2, available_tabs)

    # If validation failed due to same phase, provide more detailed error
    if not is_valid and "both on" in message:
        try:
            phase1 = get_tab_phase(tab1)
            phase2 = get_tab_phase(tab2)
            return False, (
                f"Invalid selection: Tab {tab1} ({phase1}) and Tab {tab2} ({phase2}) are both on the same phase. "
                f"For proper 240V measurement, tabs must be on opposite phases (L1 + L2)."
            )
        except ValueError:
            pass

    return is_valid, message


def get_filtered_tab_options(
    selected_tab: int, available_tabs: list[int], include_none: bool = True
) -> dict[int, str]:
    """Get filtered tab options based on opposite phase requirement.

    Args:
        selected_tab: Currently selected tab (0 for none)
        available_tabs: List of all available unmapped tabs
        include_none: Whether to include "None (Disabled)" option

    Returns:
        Dictionary mapping tab numbers to display names, filtered to show only
        tabs on the opposite phase of the selected tab (or all if no tab selected)

    """
    tab_options = {}

    # Always include "None (Disabled)" option if requested
    if include_none:
        tab_options[0] = "None (Disabled)"

    # If no tab is selected (0), show all available tabs with phase info
    if selected_tab == 0:
        for tab in available_tabs:
            try:
                phase = get_tab_phase(tab)
                tab_options[tab] = f"Tab {tab} ({phase})"
            except ValueError:
                tab_options[tab] = f"Tab {tab}"
        return tab_options

    # Filter to show only tabs on the opposite phase using the API function
    for tab in available_tabs:
        if are_tabs_opposite_phase(selected_tab, tab, available_tabs):
            try:
                phase = get_tab_phase(tab)
                tab_options[tab] = f"Tab {tab} ({phase})"
            except ValueError:
                tab_options[tab] = f"Tab {tab}"

    return tab_options


class ConfigFlowError(Exception):
    """Custom exception for config flow internal errors."""


if TYPE_CHECKING:
    from span_panel_api import SpanPanelClient


def get_user_data_schema(default_host: str = "") -> vol.Schema:
    """Get the user data schema with optional default host."""
    return vol.Schema(
        {
            vol.Optional(CONF_HOST, default=default_host): str,
            vol.Optional(CONF_USE_SSL, default=False): bool,
            vol.Optional("simulator_mode", default=False): bool,
            vol.Optional(POWER_DISPLAY_PRECISION, default=0): int,
            vol.Optional(ENERGY_DISPLAY_PRECISION, default=2): int,
        }
    )


STEP_USER_DATA_SCHEMA = get_user_data_schema()

STEP_AUTH_TOKEN_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ACCESS_TOKEN): str,
    }
)


class TriggerFlowType(enum.Enum):
    """Types of configuration flow triggers."""

    CREATE_ENTRY = enum.auto()
    UPDATE_ENTRY = enum.auto()


def create_config_client(host: str, use_ssl: bool = False) -> SpanPanelClient:
    """Create a SpanPanelClient with config settings for quick feedback."""

    return SpanPanelClient(
        host=host,
        timeout=CONFIG_TIMEOUT,
        use_ssl=use_ssl,
        retries=CONFIG_API_RETRIES,
        retry_timeout=CONFIG_API_RETRY_TIMEOUT,
        retry_backoff_multiplier=CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    )


def create_api_controller(
    hass: HomeAssistant,
    host: str,
    access_token: str | None = None,  # nosec
) -> SpanPanelApi:
    """Create a Span Panel API controller."""
    params: dict[str, Any] = {"host": host}
    if access_token is not None:
        params["access_token"] = access_token
    return SpanPanelApi(**params)


async def validate_host(
    hass: HomeAssistant,
    host: str,
    access_token: str | None = None,  # nosec
    use_ssl: bool = False,
) -> bool:
    """Validate the host connection."""

    # Use context manager for short-lived validation (recommended pattern)
    # Use config settings for quick feedback - no retries and shorter timeout
    async with SpanPanelClient(
        host=host,
        timeout=CONFIG_TIMEOUT,
        use_ssl=use_ssl,
        retries=CONFIG_API_RETRIES,
        retry_timeout=CONFIG_API_RETRY_TIMEOUT,
        retry_backoff_multiplier=CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    ) as client:
        if access_token:
            client.set_access_token(access_token)
            try:
                # Test authenticated endpoint
                await client.get_panel_state()
                return True
            except Exception:
                return False
        else:
            try:
                # Test unauthenticated endpoint
                await client.get_status()
                return True
            except Exception:
                return False


async def validate_auth_token(
    hass: HomeAssistant, host: str, access_token: str, use_ssl: bool = False
) -> bool:
    """Perform an authenticated call to confirm validity of provided token."""

    # Use context manager for short-lived validation (recommended pattern)
    # Use config settings for quick feedback - no retries and shorter timeout
    async with SpanPanelClient(
        host=host,
        timeout=CONFIG_TIMEOUT,
        use_ssl=use_ssl,
        retries=CONFIG_API_RETRIES,
        retry_timeout=CONFIG_API_RETRY_TIMEOUT,
        retry_backoff_multiplier=CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    ) as client:
        client.set_access_token(access_token)
        try:
            # Test authenticated endpoint
            await client.get_panel_state()
            return True
        except SpanPanelAuthError as e:
            _LOGGER.warning("Auth token validation failed - invalid token: %s", e)
            return False
        except SpanPanelConnectionError as e:
            _LOGGER.warning("Auth token validation failed - connection error: %s", e)
            return False
        except Exception as e:
            _LOGGER.warning("Auth token validation failed - unexpected error: %s", e)
            return False


class SpanPanelConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for Span Panel."""

    VERSION = 2
    MINOR_VERSION = 1
    domain = DOMAIN

    def is_matching(self, other_flow: SpanPanelConfigFlow) -> bool:
        """Return True if other_flow is a matching Span Panel."""
        return bool(other_flow and other_flow.context.get("source") == "zeroconf")

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.trigger_flow_type: TriggerFlowType | None = None
        self.host: str | None = None
        self.serial_number: str | None = None
        self.access_token: str | None = None
        self.use_ssl: bool = False
        self.power_display_precision: int = 0
        self.energy_display_precision: int = 2
        self._is_flow_setup: bool = False
        self.context: ConfigFlowContext = {}
        # Initial naming selection chosen during pre-setup
        self._chosen_use_device_prefix: bool | None = None
        self._chosen_use_circuit_numbers: bool | None = None

    async def setup_flow(
        self, trigger_type: TriggerFlowType, host: str, use_ssl: bool = False
    ) -> None:
        """Set up the flow."""

        if self._is_flow_setup is True:
            _LOGGER.error("Flow setup attempted when already set up")
            raise ConfigFlowError("Flow is already set up")

        # Use config settings for quick feedback - no retries and shorter timeout
        async with SpanPanelClient(
            host=host,
            timeout=CONFIG_TIMEOUT,
            use_ssl=use_ssl,
            retries=CONFIG_API_RETRIES,
            retry_timeout=CONFIG_API_RETRY_TIMEOUT,
            retry_backoff_multiplier=CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
        ) as client:
            status_response = await client.get_status()
            # Convert to our data class format
            status_dict = status_response.to_dict()  # type: ignore[attr-defined]
            panel_status = SpanPanelHardwareStatus.from_dict(status_dict)

        self.trigger_flow_type = trigger_type
        self.host = host
        self.serial_number = panel_status.serial_number

        # Keep the existing context values and add the host value
        self.context = {
            **self.context,
            "title_placeholders": {
                **self.context.get("title_placeholders", {}),
                CONF_HOST: self.host,
            },
        }

        self._is_flow_setup = True

    def ensure_flow_is_set_up(self) -> None:
        """Ensure the flow is set up."""
        if self._is_flow_setup is False:
            _LOGGER.error("Flow method called before setup")
            raise ConfigFlowError("Flow is not set up")

    async def ensure_not_already_configured(self) -> None:
        """Ensure the panel is not already configured."""
        self.ensure_flow_is_set_up()

        # Abort if we had already set this panel up
        await self.async_set_unique_id(self.serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a flow initiated by zeroconf discovery."""
        # Do not probe device if the host is already configured
        self._async_abort_entries_match({CONF_HOST: discovery_info.host})

        # Do not probe device if it is not an ipv4 address
        if not is_ipv4_address(discovery_info.host):
            return self.async_abort(reason="not_ipv4_address")

        # Validate that this is a valid Span Panel (assume HTTP for discovery)
        if not await validate_host(self.hass, discovery_info.host, use_ssl=False):
            return self.async_abort(reason="not_span_panel")

            # Discovered devices default to HTTP/no SSL
        self.use_ssl = False
        await self.setup_flow(TriggerFlowType.CREATE_ENTRY, discovery_info.host, False)
        await self.ensure_not_already_configured()
        return await self.async_step_confirm_discovery()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        # Store precision settings from user input (needed for both simulator and regular mode)
        self.power_display_precision = user_input.get(POWER_DISPLAY_PRECISION, 0)
        self.energy_display_precision = user_input.get(ENERGY_DISPLAY_PRECISION, 2)

        _LOGGER.debug(
            "CONFIG_INPUT_DEBUG: User input precision - power: %s, energy: %s, full input: %s",
            self.power_display_precision,
            self.energy_display_precision,
            user_input,
        )

        # Check if simulator mode is enabled
        if user_input.get("simulator_mode", False):
            return await self._handle_simulator_setup(user_input)

        # For non-simulator mode, host is required
        host: str = user_input.get(CONF_HOST, "").strip()
        if not host:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "host_required"},
            )

        use_ssl: bool = user_input.get(CONF_USE_SSL, False)

        # Validate host before setting up flow
        if not await validate_host(self.hass, host, use_ssl=use_ssl):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        # Store SSL setting for later use
        self.use_ssl = use_ssl

        # Only setup flow if validation succeeded
        if not self._is_flow_setup:
            await self.setup_flow(TriggerFlowType.CREATE_ENTRY, host, use_ssl)
            await self.ensure_not_already_configured()

        return await self.async_step_choose_auth_type()

    async def _handle_simulator_setup(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Handle simulator mode setup."""
        # Precision settings already stored in async_step_user

        # Check if this is the initial simulator selection or the config selection
        if CONF_SIMULATION_CONFIG not in user_input:
            # Show simulator configuration selection
            return await self.async_step_simulator_config()

        # Get the simulation config and host
        simulation_config = user_input[CONF_SIMULATION_CONFIG]
        host = user_input.get(CONF_HOST, "").strip()
        simulation_start_time = user_input.get(CONF_SIMULATION_START_TIME, "").strip()

        # If no host provided, try to extract serial from the selected config
        if not host:
            try:
                from tests.test_factories.span_panel_simulation_factory import (  # pylint: disable=import-outside-toplevel
                    SpanPanelSimulationFactory,
                )

                config_path = (
                    Path(__file__).parent / "simulation_configs" / f"{simulation_config}.yaml"
                )
                if config_path.exists():
                    host = SpanPanelSimulationFactory.extract_serial_number_from_yaml(
                        str(config_path)
                    )
                else:
                    host = "span-sim-001"
            except (ImportError, FileNotFoundError, Exception):
                # Fallback to a default
                host = "span-sim-001"

        # Create entry for simulator mode
        base_name = "Span Simulator"
        device_name = self.get_unique_device_name(base_name)

        # Prepare config data
        config_data = {
            CONF_HOST: host,
            CONF_ACCESS_TOKEN: "simulator_token",
            CONF_USE_SSL: False,
            "simulation_mode": True,
            CONF_SIMULATION_CONFIG: simulation_config,
            "device_name": device_name,
        }

        # Add simulation start time if provided
        if simulation_start_time:
            try:
                validated_time = validate_simulation_time(simulation_start_time)
                config_data[CONF_SIMULATION_START_TIME] = validated_time
            except ValueError as e:
                return self.async_show_form(
                    step_id="simulator_config",
                    data_schema=self.add_suggested_values_to_schema(
                        vol.Schema(
                            {
                                vol.Required(
                                    CONF_SIMULATION_CONFIG, default="simulation_config_32_circuit"
                                ): vol.In(get_available_simulation_configs()),
                                vol.Optional(CONF_HOST, default=""): str,
                                vol.Optional(CONF_SIMULATION_START_TIME, default=""): str,
                            }
                        ),
                        user_input,
                    ),
                    errors={"base": str(e)},
                )

        _LOGGER.debug(
            "SIMULATOR_CONFIG_DEBUG: Creating simulator entry with precision - power: %s, energy: %s",
            self.power_display_precision,
            self.energy_display_precision,
        )
        # Determine simulator naming flags based on selection (default Friendly Names)
        selected_pattern = user_input.get(
            ENTITY_NAMING_PATTERN, EntityNamingPattern.FRIENDLY_NAMES.value
        )
        sim_use_device_prefix = True
        sim_use_circuit_numbers = selected_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value

        return self.async_create_entry(
            title=device_name,
            data=config_data,
            options={
                USE_DEVICE_PREFIX: sim_use_device_prefix,
                USE_CIRCUIT_NUMBERS: sim_use_circuit_numbers,
                POWER_DISPLAY_PRECISION: self.power_display_precision,
                ENERGY_DISPLAY_PRECISION: self.energy_display_precision,
            },
        )

    async def async_step_simulator_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle simulator configuration selection."""
        if user_input is None:
            # Discover files dynamically and build dropdown options
            available_configs = get_available_simulation_configs()
            options_list = [
                {"value": key, "label": label} for key, label in available_configs.items()
            ]

            # Choose a sensible default
            default_key = (
                "simulation_config_32_circuit"
                if "simulation_config_32_circuit" in available_configs
                else next(iter(available_configs.keys()))
            )

            # Create schema with forced dropdown for simulation configuration
            schema = vol.Schema(
                {
                    vol.Required(CONF_SIMULATION_CONFIG, default=default_key): selector(
                        {
                            "select": {
                                "options": options_list,
                                "mode": "dropdown",
                            }
                        }
                    ),
                    vol.Optional(CONF_HOST, default=""): str,
                    vol.Optional(CONF_SIMULATION_START_TIME, default=""): str,
                    vol.Required(
                        ENTITY_NAMING_PATTERN, default=EntityNamingPattern.FRIENDLY_NAMES.value
                    ): vol.In(
                        {
                            EntityNamingPattern.FRIENDLY_NAMES.value: "Circuit Friendly Names",
                            EntityNamingPattern.CIRCUIT_NUMBERS.value: "Tab Based Names",
                        }
                    ),
                }
            )

            return self.async_show_form(
                step_id="simulator_config",
                data_schema=schema,
                description_placeholders={
                    "config_count": str(len(available_configs)),
                },
            )

        # Continue with simulator setup using the selected config
        # Ensure simulator_mode is set since it's not in the form data
        user_input_with_sim_mode = dict(user_input)
        user_input_with_sim_mode["simulator_mode"] = True
        return await self._handle_simulator_setup(user_input_with_sim_mode)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle a flow initiated by re-auth."""
        use_ssl = entry_data.get(CONF_USE_SSL, False)
        self.use_ssl = use_ssl
        await self.setup_flow(TriggerFlowType.UPDATE_ENTRY, entry_data[CONF_HOST], use_ssl)
        return await self.async_step_auth_token(dict(entry_data))

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt user to confirm a discovered Span Panel."""
        self.ensure_flow_is_set_up()

        # Prompt the user for confirmation
        if user_input is None:
            self._set_confirm_only()
            host = self.host if self.host is not None else ""
            return self.async_show_form(
                step_id="confirm_discovery",
                description_placeholders={
                    "host": host,
                },
            )

        # Pass (empty) dictionary to signal the call came from this step, not abort
        return await self.async_step_choose_auth_type(user_input)

    async def async_step_choose_auth_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the authentication method to use."""
        self.ensure_flow_is_set_up()

        # None means this method was called by HA core as an abort
        if user_input is None:
            return await self.async_step_confirm_discovery()

        return self.async_show_menu(
            step_id="choose_auth_type",
            menu_options={
                "auth_proximity": "Proof of Proximity (recommended)",
                "auth_token": "Existing Auth Token",
            },
        )

    async def async_step_auth_proximity(
        self,
        entry_data: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step that guide users through the proximity authentication process."""
        self.ensure_flow_is_set_up()

        # Use config settings for quick feedback - no retries and shorter timeout
        async with SpanPanelClient(
            host=self.host or "",
            timeout=CONFIG_TIMEOUT,
            use_ssl=self.use_ssl,
            retries=CONFIG_API_RETRIES,
            retry_timeout=CONFIG_API_RETRY_TIMEOUT,
            retry_backoff_multiplier=CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
        ) as client:
            # Get status to check proximity state
            status_response = await client.get_status()
            status_dict = status_response.to_dict()  # type: ignore[attr-defined]
            panel_status = SpanPanelHardwareStatus.from_dict(status_dict)

            # Check if running firmware newer or older than r202342
            if panel_status.proximity_proven is not None:
                # Reprompt until we are able to do proximity auth for new firmware
                proximity_verified: bool = panel_status.proximity_proven
                if proximity_verified is False:
                    return self.async_show_form(step_id="auth_proximity")
            else:
                # Reprompt until we are able to do proximity auth for old firmware
                remaining_presses: int = panel_status.remaining_auth_unlock_button_presses
                if remaining_presses != 0:
                    return self.async_show_form(
                        step_id="auth_proximity",
                    )

            # Ensure host is set
            if not self.host:
                return self.async_abort(reason="host_not_set")

            client_name = f"home-assistant-{uuid.uuid4()}"
            auth_response = await client.authenticate(
                client_name, "Home Assistant Local Span Integration"
            )
            self.access_token = auth_response.access_token
        # Type checking: ensure access_token is not None before calling validate_auth_token
        if self.access_token is None:
            return self.async_abort(reason="invalid_access_token")
        if not await validate_auth_token(self.hass, self.host, self.access_token, self.use_ssl):
            return self.async_abort(reason="invalid_access_token")

        return await self.async_step_resolve_entity(entry_data)

    async def async_step_auth_token(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Step that prompts user for access token."""
        self.ensure_flow_is_set_up()

        if user_input is None:
            # Show the form to prompt for the access token
            return self.async_show_form(
                step_id="auth_token", data_schema=STEP_AUTH_TOKEN_DATA_SCHEMA
            )

        # Extract access token from user input
        access_token: str | None = user_input.get(CONF_ACCESS_TOKEN)

        # Check if token was provided and is not empty
        if access_token and access_token.strip():
            self.access_token = access_token.strip()

            # Ensure host is set
            if not self.host:
                return self.async_abort(reason="host_not_set")

            # Validate the provided token
            if not await validate_auth_token(self.hass, self.host, self.access_token, self.use_ssl):
                return self.async_show_form(
                    step_id="auth_token",
                    data_schema=STEP_AUTH_TOKEN_DATA_SCHEMA,
                    errors={"base": "invalid_access_token"},
                )

            # Proceed to pre-setup naming selection then to entry creation
            return await self.async_step_resolve_entity(user_input)

        # If no access token was provided or it's empty, show form with error
        return self.async_show_form(
            step_id="auth_token",
            data_schema=STEP_AUTH_TOKEN_DATA_SCHEMA,
            errors={"base": "missing_access_token"},
        )

    async def async_step_resolve_entity(
        self,
        entry_data: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Resolve the entity."""
        self.ensure_flow_is_set_up()

        # Continue based on flow trigger type
        match self.trigger_flow_type:
            case TriggerFlowType.CREATE_ENTRY:
                if self.host is None:
                    raise ValueError("Host cannot be None when creating a new entry")
                if self.serial_number is None:
                    raise ValueError("Serial number cannot be None when creating a new entry")
                if self.access_token is None:
                    raise ValueError("Access token cannot be None when creating a new entry")
                # Before creating the entry, prompt for naming pattern selection
                return await self.async_step_choose_entity_naming_initial()
            case TriggerFlowType.UPDATE_ENTRY:
                if self.host is None:
                    raise ValueError("Host cannot be None when updating an entry")
                if self.access_token is None:
                    raise ValueError("Access token cannot be None when updating an entry")
                if "entry_id" not in self.context:
                    raise ValueError("Entry ID is missing from context")
                return self.update_existing_entry(
                    self.context["entry_id"],
                    self.host,
                    self.access_token,
                    entry_data or {},
                )
            case _:
                raise NotImplementedError()

    def create_new_entry(
        self, host: str, serial_number: str, access_token: str
    ) -> ConfigFlowResult:
        """Create a new SPAN panel entry."""
        base_name = "Span Panel"
        device_name = self.get_unique_device_name(base_name)
        _LOGGER.debug(
            "CONFIG_FLOW_DEBUG: Creating entry with precision - power: %s, energy: %s",
            self.power_display_precision,
            self.energy_display_precision,
        )
        # Determine initial naming flags with default to Friendly Names
        use_device_prefix = (
            True if self._chosen_use_device_prefix is None else self._chosen_use_device_prefix
        )
        use_circuit_numbers = (
            False if self._chosen_use_circuit_numbers is None else self._chosen_use_circuit_numbers
        )

        return self.async_create_entry(
            title=device_name,
            data={
                CONF_HOST: host,
                CONF_ACCESS_TOKEN: access_token,
                CONF_USE_SSL: self.use_ssl,
                "device_name": device_name,
            },
            options={
                USE_DEVICE_PREFIX: use_device_prefix,
                USE_CIRCUIT_NUMBERS: use_circuit_numbers,
                POWER_DISPLAY_PRECISION: self.power_display_precision,
                ENERGY_DISPLAY_PRECISION: self.energy_display_precision,
            },
        )

    def update_existing_entry(
        self,
        entry_id: str,
        host: str,
        access_token: str,
        entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Update an existing entry with new configurations."""
        # Update the existing data with reauthed data
        # Create a new mutable copy of the entry data (Mapping is immutable)
        updated_data = dict(entry_data)
        updated_data[CONF_HOST] = host
        updated_data[CONF_ACCESS_TOKEN] = access_token
        updated_data[CONF_USE_SSL] = self.use_ssl

        # An existing entry must exist before we can update it
        entry: ConfigEntry[Any] | None = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            _LOGGER.error("Config entry %s does not exist during reauth", entry_id)
            return self.async_abort(reason="reauth_failed")

        self.hass.config_entries.async_update_entry(entry, data=updated_data)
        self.hass.async_create_task(self.hass.config_entries.async_reload(entry_id))
        return self.async_abort(reason="reauth_successful")

    def get_unique_device_name(self, base_name: str) -> str:
        """Return a unique device name based on existing config entry titles."""
        existing_names = {entry.title for entry in self.hass.config_entries.async_entries(DOMAIN)}
        if base_name not in existing_names:
            return base_name
        i = 2
        while f"{base_name} {i}" in existing_names:
            i += 1
        return f"{base_name} {i}"

    async def async_step_choose_entity_naming_initial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pre-setup choice of Entity ID naming pattern.

        Default to Friendly Names; both choices imply device prefix enabled.
        """

        self.ensure_flow_is_set_up()

        pattern_options = {
            EntityNamingPattern.FRIENDLY_NAMES.value: "Circuit Friendly Names",
            EntityNamingPattern.CIRCUIT_NUMBERS.value: "Tab Based Names",
        }

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(
                        ENTITY_NAMING_PATTERN, default=EntityNamingPattern.FRIENDLY_NAMES.value
                    ): vol.In(pattern_options)
                }
            )
            return self.async_show_form(
                step_id="choose_entity_naming_initial",
                data_schema=schema,
            )

        selected = user_input.get(ENTITY_NAMING_PATTERN, EntityNamingPattern.FRIENDLY_NAMES.value)
        self._chosen_use_device_prefix = True
        self._chosen_use_circuit_numbers = selected == EntityNamingPattern.CIRCUIT_NUMBERS.value

        # Proceed to create the entry
        if self.host is None or self.serial_number is None or self.access_token is None:
            raise ConfigFlowError("Missing required parameters during entry creation")
        return self.create_new_entry(self.host, self.serial_number, self.access_token)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Create the options flow."""
        return OptionsFlowHandler()


OPTIONS_SCHEMA: Any = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL): vol.All(int, vol.Range(min=5)),
        vol.Optional(BATTERY_ENABLE): bool,
        vol.Optional(INVERTER_ENABLE): bool,
        vol.Optional(INVERTER_LEG1): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(INVERTER_LEG2): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ENTITY_NAMING_PATTERN): vol.In([e.value for e in EntityNamingPattern]),
        vol.Optional(CONF_API_RETRIES): vol.All(int, vol.Range(min=0, max=10)),
        vol.Optional(CONF_API_RETRY_TIMEOUT): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=10.0)
        ),
        vol.Optional(CONF_API_RETRY_BACKOFF_MULTIPLIER): vol.All(
            vol.Coerce(float), vol.Range(min=1.0, max=5.0)
        ),
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
    }
)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow for Span Panel."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the main options menu."""
        _LOGGER.info("=== MAIN OPTIONS FLOW ENTRY ===")
        _LOGGER.info("async_step_init called with user_input: %s", user_input)
        if user_input is None:
            menu_options = {
                "general_options": "General Options",
            }

            # Add simulation options if this is a simulation mode integration
            if self.config_entry.data.get("simulation_mode", False):
                menu_options["simulation_options"] = "Simulation Options"
            else:
                # Live panel: offer cloning into a simulation config
                menu_options["clone_panel_to_simulation"] = "Clone Panel To Simulation"

            return self.async_show_menu(
                step_id="init",
                menu_options=menu_options,
            )

            # This shouldn't be reached since we're showing a menu
        return self.async_abort(reason="unknown")

    async def async_step_general_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the general options (excluding entity naming)."""
        _LOGGER.info("=== OPTIONS FLOW ENTRY ===")
        _LOGGER.info("async_step_general_options called with user_input: %s", user_input)
        errors: dict[str, str] = {}

        # Get available unmapped tabs for dropdown
        available_tabs = await get_available_unmapped_tabs(self.hass, self.config_entry)

        if user_input is not None:
            # Filter out separator fields from user input
            filtered_input = {k: v for k, v in user_input.items() if not k.startswith("_separator")}
            # Handle legacy upgrade flag if present
            legacy_upgrade_requested: bool = bool(
                user_input.get("legacy_upgrade_to_friendly", False)
            )
            filtered_input.pop("legacy_upgrade_to_friendly", None)

            # Merge with existing options to preserve unchanged values
            merged_options = dict(self.config_entry.options)
            merged_options.update(filtered_input)
            filtered_input = merged_options

            # Validate solar tab selection if solar is enabled
            if filtered_input.get(INVERTER_ENABLE, False):
                # Coerce selector values (strings) back to integers
                leg1_raw = filtered_input.get(INVERTER_LEG1, 0)
                leg2_raw = filtered_input.get(INVERTER_LEG2, 0)
                try:
                    leg1 = int(leg1_raw)
                except (TypeError, ValueError):
                    leg1 = 0
                try:
                    leg2 = int(leg2_raw)
                except (TypeError, ValueError):
                    leg2 = 0

                # Only validate when we actually have available tabs information
                if available_tabs:
                    is_valid, error_message = validate_solar_tab_selection(
                        leg1, leg2, available_tabs
                    )
                    if not is_valid:
                        errors["base"] = error_message
                        _LOGGER.warning("Solar tab validation failed: %s", error_message)

                # Persist coerced integer values
                filtered_input[INVERTER_LEG1] = leg1
                filtered_input[INVERTER_LEG2] = leg2

            # If no errors, proceed with saving options
            if not errors:
                # Preserve existing naming flags by default.
                # Important: default use_device_prefix to True for new installs
                # so we do not accidentally treat them as legacy when the option
                # was not yet persisted.
                use_prefix: Any | bool = self.config_entry.options.get(USE_DEVICE_PREFIX, True)
                use_circuit_numbers: Any | bool = self.config_entry.options.get(
                    USE_CIRCUIT_NUMBERS, False
                )

                # If legacy upgrade requested, check if entities need renaming
                needs_prefix_upgrade = False
                _LOGGER.info("=== LEGACY UPGRADE DEBUG ===")
                _LOGGER.info("legacy_upgrade_requested: %s", legacy_upgrade_requested)
                _LOGGER.info("use_prefix (before): %s", use_prefix)

                if legacy_upgrade_requested:
                    # Mark this config entry for legacy prefix upgrade after reload
                    # The migration code will check which entities actually need renaming
                    self._mark_for_legacy_migration()
                    use_prefix = True
                    use_circuit_numbers = False
                    needs_prefix_upgrade = True

                filtered_input[USE_DEVICE_PREFIX] = use_prefix
                filtered_input[USE_CIRCUIT_NUMBERS] = use_circuit_numbers

                # Set the prefix upgrade flag directly in filtered_input if upgrade is needed
                if needs_prefix_upgrade:
                    filtered_input["pending_legacy_migration"] = True
                    _LOGGER.info("=== OPTIONS FLOW DEBUG ===")
                    _LOGGER.info("Setting pending_legacy_migration flag directly in filtered_input")
                    _LOGGER.info("Full filtered_input: %s", filtered_input)
                    _LOGGER.info("Flag value: %s", filtered_input.get("pending_legacy_migration"))
                    _LOGGER.info(
                        "Flag type: %s", type(filtered_input.get("pending_legacy_migration"))
                    )
                    _LOGGER.info("=== OPTIONS FLOW DEBUG END ===")
                else:
                    _LOGGER.info("No prefix upgrade needed - needs_prefix_upgrade was False")

                # Remove any entity naming pattern from input (shouldn't be there anyway)
                filtered_input.pop(ENTITY_NAMING_PATTERN, None)

                # Global options are now handled natively during reload

                return self.async_create_entry(title="", data=filtered_input)

        # Get current values for dynamic filtering
        try:
            current_leg1 = int(self.config_entry.options.get(INVERTER_LEG1, 0))
        except (TypeError, ValueError):
            current_leg1 = 0
        try:
            current_leg2 = int(self.config_entry.options.get(INVERTER_LEG2, 0))
        except (TypeError, ValueError):
            current_leg2 = 0

        # If user_input exists, use those values for filtering (for dynamic updates)
        if user_input is not None:
            leg1_raw_dyn = user_input.get(INVERTER_LEG1, current_leg1)
            leg2_raw_dyn = user_input.get(INVERTER_LEG2, current_leg2)
            try:
                current_leg1 = int(leg1_raw_dyn)
            except (TypeError, ValueError):
                current_leg1 = 0
            try:
                current_leg2 = int(leg2_raw_dyn)
            except (TypeError, ValueError):
                current_leg2 = 0

        # Create filtered tab options for each dropdown
        leg1_options = get_filtered_tab_options(current_leg2, available_tabs)
        leg2_options = get_filtered_tab_options(current_leg1, available_tabs)
        # Convert to selector options lists (value/label) to force dropdowns
        leg1_select_options = [{"value": str(k), "label": v} for k, v in leg1_options.items()]
        leg2_select_options = [{"value": str(k), "label": v} for k, v in leg2_options.items()]

        # Show general options form (without entity naming)
        defaults: dict[str, Any] = {
            CONF_SCAN_INTERVAL: self.config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds
            ),
            BATTERY_ENABLE: self.config_entry.options.get("enable_battery_percentage", False),
            INVERTER_ENABLE: self.config_entry.options.get("enable_solar_circuit", False),
            # Defaults for selector values must be strings
            INVERTER_LEG1: str(current_leg1),
            INVERTER_LEG2: str(current_leg2),
            CONF_API_RETRIES: self.config_entry.options.get(CONF_API_RETRIES, DEFAULT_API_RETRIES),
            CONF_API_RETRY_TIMEOUT: self.config_entry.options.get(
                CONF_API_RETRY_TIMEOUT, DEFAULT_API_RETRY_TIMEOUT
            ),
            CONF_API_RETRY_BACKOFF_MULTIPLIER: self.config_entry.options.get(
                CONF_API_RETRY_BACKOFF_MULTIPLIER, DEFAULT_API_RETRY_BACKOFF_MULTIPLIER
            ),
            ENERGY_REPORTING_GRACE_PERIOD: self.config_entry.options.get(
                ENERGY_REPORTING_GRACE_PERIOD, 15
            ),
        }

        # Create schema with filtered dropdown selections for solar tabs
        schema_fields = {
            vol.Optional(CONF_SCAN_INTERVAL): vol.All(int, vol.Range(min=5)),
            vol.Optional(BATTERY_ENABLE): bool,
            vol.Optional(INVERTER_ENABLE): bool,
            vol.Optional(INVERTER_LEG1, default=str(current_leg1)): selector(
                {"select": {"options": leg1_select_options, "mode": "dropdown"}}
            ),
            vol.Optional(INVERTER_LEG2, default=str(current_leg2)): selector(
                {"select": {"options": leg2_select_options, "mode": "dropdown"}}
            ),
            vol.Optional(CONF_API_RETRIES): vol.All(int, vol.Range(min=0, max=10)),
            vol.Optional(CONF_API_RETRY_TIMEOUT): vol.All(
                vol.Coerce(float), vol.Range(min=0.1, max=10.0)
            ),
            vol.Optional(CONF_API_RETRY_BACKOFF_MULTIPLIER): vol.All(
                vol.Coerce(float), vol.Range(min=1.0, max=5.0)
            ),
            vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
        }

        # If legacy (no device prefix), show upgrade toggle in general options.
        # Check USE_DEVICE_PREFIX flag to determine if this is a legacy installation.
        is_legacy_install = not self.config_entry.options.get(USE_DEVICE_PREFIX, False)
        if is_legacy_install:
            schema_fields[vol.Optional("legacy_upgrade_to_friendly", default=False)] = bool
            defaults["legacy_upgrade_to_friendly"] = False

        schema = vol.Schema(schema_fields)

        return self.async_show_form(
            step_id="general_options",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )

    async def async_step_entity_naming(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage entity naming pattern options."""
        if user_input is not None:
            # Check if entity naming pattern changed
            current_pattern = self._get_current_naming_pattern()
            new_pattern = user_input.get(ENTITY_NAMING_PATTERN, current_pattern)

            # For legacy installations, treat the selection as a change even
            # if it matches the default since we default to Friendly Names
            # for display but the actual pattern is Legacy
            pattern_changed = False
            if current_pattern == EntityNamingPattern.LEGACY_NAMES.value:
                # Pre-1.0.4 installation - any selection is a migration
                # But only if they actually selected something (not just submitted
                # with defaults)
                if ENTITY_NAMING_PATTERN in user_input:
                    pattern_changed = True
            else:
                # Modern installation - only migrate if pattern actually changed
                pattern_changed = new_pattern != current_pattern

            if pattern_changed:
                # Entity naming pattern changed - update the configuration flags
                naming_options = {}
                if new_pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value:
                    naming_options[USE_CIRCUIT_NUMBERS] = True
                    naming_options[USE_DEVICE_PREFIX] = True
                elif new_pattern == EntityNamingPattern.FRIENDLY_NAMES.value:
                    naming_options[USE_CIRCUIT_NUMBERS] = False
                    naming_options[USE_DEVICE_PREFIX] = True

                _LOGGER.info(
                    "Pattern change: %s -> %s, setting flags: USE_CIRCUIT_NUMBERS=%s, USE_DEVICE_PREFIX=%s",
                    current_pattern,
                    new_pattern,
                    naming_options.get(USE_CIRCUIT_NUMBERS),
                    naming_options.get(USE_DEVICE_PREFIX),
                )

                # Entity ID migration will be handled after reload via pending_legacy_migration flag

                # Update only the naming-related options, preserve ALL other options
                current_options = dict(self.config_entry.options)

                # Only update the specific naming flags, preserve everything else
                current_options[USE_CIRCUIT_NUMBERS] = naming_options[USE_CIRCUIT_NUMBERS]
                current_options[USE_DEVICE_PREFIX] = naming_options[USE_DEVICE_PREFIX]

                # Debug: Log what options we're preserving
                preserved_options = {
                    k: v
                    for k, v in current_options.items()
                    if k not in [USE_CIRCUIT_NUMBERS, USE_DEVICE_PREFIX]
                }
                _LOGGER.debug("Preserving existing options: %s", preserved_options)
                _LOGGER.debug(
                    "Solar sensor enabled: %s",
                    current_options.get(INVERTER_ENABLE, False),
                )
                _LOGGER.debug("Inverter leg 1: %s", current_options.get(INVERTER_LEG1, 0))
                _LOGGER.debug("Inverter leg 2: %s", current_options.get(INVERTER_LEG2, 0))
                _LOGGER.debug("All options after update: %s", current_options)

                # Schedule reload after the options flow completes
                async def reload_after_options_complete() -> None:
                    # Wait for the options flow to complete first
                    await self.hass.async_block_till_done()
                    _LOGGER.info("Reloading integration after entity naming pattern change")
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)

                self.hass.async_create_task(reload_after_options_complete())

                # Return success with the updated options - this will update the config entry
                _LOGGER.debug("Returning updated options to complete the flow")
                return self.async_create_entry(title="", data=current_options)
            else:
                # No pattern change - just return success
                return self.async_create_entry(title="", data={})

        # Show entity naming form
        current_pattern = self._get_current_naming_pattern()

        # For legacy installations, default to Friendly Names but allow user to choose
        # For modern installations, show the current pattern
        if current_pattern == EntityNamingPattern.LEGACY_NAMES.value:
            display_pattern = EntityNamingPattern.FRIENDLY_NAMES.value
        else:
            display_pattern = current_pattern

        defaults: dict[str, Any] = {
            ENTITY_NAMING_PATTERN: display_pattern,
        }

        # Provide placeholders for the translation system
        description_placeholders = {
            "friendly_example": "**Friendly Names Example**: span_panel_kitchen_outlets_power",
            "circuit_example": "**Circuit Numbers Example**: span_panel_circuit_15_power",
        }

        _LOGGER.debug("Entity naming step - current pattern: %s", current_pattern)
        _LOGGER.debug(
            "Entity naming step - description placeholders: %s",
            description_placeholders,
        )

        return self.async_show_form(
            step_id="entity_naming",
            data_schema=self.add_suggested_values_to_schema(
                self._get_entity_naming_schema(), defaults
            ),
            description_placeholders=description_placeholders,
        )

    async def async_step_simulation_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show simulation-related actions."""
        if user_input is None:
            return self.async_show_menu(
                step_id="simulation_options",
                menu_options={
                    "edit_simulation_settings": "Edit Simulation Settings",
                    "manage_simulation_configs": "Manage Simulation Configurations",
                },
            )
        return self.async_abort(reason="unknown")

    async def async_step_edit_simulation_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit simulation settings (time and performance)."""
        if user_input is not None:
            simulation_start_time = user_input.get(CONF_SIMULATION_START_TIME, "").strip()
            offline_minutes = user_input.get(CONF_SIMULATION_OFFLINE_MINUTES, 0)

            _LOGGER.info("Edit simulation settings - user_input: %s", user_input)
            _LOGGER.info(
                "Edit simulation settings - offline_minutes: %s, start_time: %s",
                offline_minutes,
                simulation_start_time,
            )

            if simulation_start_time:
                try:
                    simulation_start_time = validate_simulation_time(simulation_start_time)
                    user_input[CONF_SIMULATION_START_TIME] = simulation_start_time
                except ValueError as e:
                    return self.async_show_form(
                        step_id="edit_simulation_settings",
                        data_schema=self.add_suggested_values_to_schema(
                            self._get_simulation_schema(),
                            self._get_simulation_defaults(),
                        ),
                        errors={"base": str(e)},
                    )

            # Merge with existing options to preserve general options
            merged_options = dict(self.config_entry.options)
            merged_options.update(user_input)

            _LOGGER.info("Saving simulation options: %s", user_input)
            _LOGGER.info("Merged options: %s", merged_options)
            _LOGGER.info("Current config_entry.options before save: %s", self.config_entry.options)
            _LOGGER.info("About to call async_create_entry to save options")
            result = self.async_create_entry(title="", data=merged_options)
            _LOGGER.info("async_create_entry completed, result type: %s", type(result))
            _LOGGER.info("Config_entry.options after save: %s", self.config_entry.options)

            # Manually trigger offline mode setting since update listener isn't working
            try:
                coordinator_data = self.hass.data.get(DOMAIN, {}).get(
                    self.config_entry.entry_id, {}
                )
                coordinator = coordinator_data.get(COORDINATOR)
                if coordinator and hasattr(coordinator, "span_panel") and coordinator.span_panel:
                    span_panel = coordinator.span_panel
                    if hasattr(span_panel, "api") and span_panel.api:
                        simulation_offline_minutes = user_input.get("simulation_offline_minutes", 0)
                        _LOGGER.info(
                            "Manually setting offline mode: %s minutes", simulation_offline_minutes
                        )
                        span_panel.api.set_simulation_offline_mode(simulation_offline_minutes)
                    else:
                        _LOGGER.warning("SpanPanel API not found for manual offline mode setting")
                else:
                    _LOGGER.warning(
                        "Coordinator or SpanPanel not found for manual offline mode setting"
                    )
            except Exception as e:
                _LOGGER.error("Failed to manually set offline mode: %s", e)

            return result

        return self.async_show_form(
            step_id="edit_simulation_settings",
            data_schema=self.add_suggested_values_to_schema(
                self._get_simulation_schema(),
                self._get_simulation_defaults(),
            ),
        )

    async def async_step_simulation_export(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle simulation config export."""
        errors: dict[str, str] = {}

        if user_input is not None:
            config_key = user_input.get(SIM_FILE_KEY, "")
            export_path_raw = str(user_input.get(SIM_EXPORT_PATH, "")).strip()

            if not config_key:
                errors[SIM_FILE_KEY] = "Please select a simulation config to export"
            elif not export_path_raw:
                errors[SIM_EXPORT_PATH] = "Export path is required"
            else:
                try:
                    current_file = Path(__file__)
                    config_dir = current_file.parent / "simulation_configs"
                    src_yaml = config_dir / f"{config_key}.yaml"

                    export_path = Path(export_path_raw)
                    await self.hass.async_add_executor_job(
                        lambda: export_path.parent.mkdir(parents=True, exist_ok=True)
                    )
                    if not await self.hass.async_add_executor_job(src_yaml.exists):
                        raise FileNotFoundError(f"Source simulation file not found: {src_yaml}")
                    await self.hass.async_add_executor_job(shutil.copyfile, src_yaml, export_path)
                    _LOGGER.info("Exported simulation config '%s' to %s", config_key, export_path)

                    # Build friendly name for confirmation
                    friendly = get_available_simulation_configs().get(config_key, config_key)
                    return self.async_create_entry(
                        title="",
                        data={},
                        description=f"Exported '{friendly}' to {export_path}",
                    )

                except Exception as e:
                    _LOGGER.error("Simulation config export error: %s", e)
                    errors["base"] = f"Export failed: {e}"

        # Show export form
        available_configs = get_available_simulation_configs()
        options_list = [{"value": k, "label": v} for k, v in available_configs.items()]
        current_config_key = self.config_entry.data.get(
            CONF_SIMULATION_CONFIG, "simulation_config_32_circuit"
        )
        default_export = f"/tmp/{current_config_key}.yaml"  # nosec

        export_schema = vol.Schema(
            {
                vol.Required(SIM_FILE_KEY, default=current_config_key): selector(
                    {
                        "select": {
                            "options": options_list,
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(SIM_EXPORT_PATH, default=default_export): str,
            }
        )

        return self.async_show_form(
            step_id="simulation_export",
            data_schema=export_schema,
            errors=errors,
        )

    async def async_step_simulation_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle simulation config import."""
        errors: dict[str, str] = {}

        if user_input is not None:
            import_path_raw = str(user_input.get(SIM_IMPORT_PATH, "")).strip()

            if not import_path_raw:
                errors[SIM_IMPORT_PATH] = "Import path is required"
            else:
                try:
                    import_path = Path(import_path_raw)
                    if not await self.hass.async_add_executor_job(import_path.exists):
                        raise FileNotFoundError(f"Import file not found: {import_path}")

                    # Load and validate YAML using span-panel-api's validator
                    def load_yaml_file() -> dict[str, Any]:
                        with import_path.open("r", encoding="utf-8") as f:
                            result = yaml.safe_load(f)
                            if result is None:
                                return {}
                            if isinstance(result, dict):
                                return result
                            return {}

                    loaded_yaml = await self.hass.async_add_executor_job(load_yaml_file)
                    # Use DynamicSimulationEngine internal validation
                    config = SimulationConfig(**loaded_yaml)
                    engine = DynamicSimulationEngine(config_data=config)
                    await engine.initialize_async()

                    # Copy to simulation_configs directory
                    current_file = Path(__file__)
                    config_dir = current_file.parent / "simulation_configs"
                    dest_name = (
                        import_path.name if import_path.suffix else f"{import_path.name}.yaml"
                    )
                    dest_yaml = config_dir / dest_name
                    await self.hass.async_add_executor_job(
                        lambda: dest_yaml.parent.mkdir(parents=True, exist_ok=True)
                    )
                    await self.hass.async_add_executor_job(shutil.copyfile, import_path, dest_yaml)
                    _LOGGER.info("Imported and validated simulation config to %s", dest_yaml)

                    # Update config entry to point to the imported simulation config
                    try:
                        new_data = dict(self.config_entry.data)
                        new_data[CONF_SIMULATION_CONFIG] = dest_yaml.stem
                        self.hass.config_entries.async_update_entry(
                            self.config_entry, data=new_data
                        )
                        _LOGGER.debug("Set CONF_SIMULATION_CONFIG to %s", dest_yaml.stem)
                    except Exception as update_err:
                        _LOGGER.warning(
                            "Failed to set CONF_SIMULATION_CONFIG to %s: %s",
                            dest_yaml.stem,
                            update_err,
                        )

                    return self.async_create_entry(
                        title="",
                        data={},
                        description=f"Imported '{dest_yaml.stem}' into simulation configurations",
                    )

                except Exception as e:
                    _LOGGER.error("Simulation config import error: %s", e)
                    errors["base"] = f"Import failed: {e}"

        # Show import form
        import_schema = vol.Schema(
            {
                vol.Required(SIM_IMPORT_PATH, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="simulation_import",
            data_schema=import_schema,
            errors=errors,
        )

    async def async_step_clone_panel_to_simulation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Clone the live panel into a simulation YAML stored in simulation_configs."""
        result = await clone_panel_to_simulation(self.hass, self.config_entry, user_input)

        # If result is a ConfigFlowResult, return it directly
        if hasattr(result, "type"):
            return result  # type: ignore[return-value]

        # Otherwise, result is (dest_path, errors) for the form
        if isinstance(result, tuple) and len(result) == 2:
            dest_path, errors = result
            if not isinstance(errors, dict):
                errors = {}
        else:
            # Fallback if result format is unexpected
            _LOGGER.error(
                "Unexpected result format from clone_panel_to_simulation: %s", type(result)
            )
            return self.async_abort(reason="unknown")

        # Compute device name for form display
        device_name = self.config_entry.data.get("device_name", self.config_entry.title)

        # Confirm form with destination field
        schema = vol.Schema(
            {
                vol.Required("destination", default=str(dest_path)): selector(
                    {"text": {"multiline": False}}
                )
            }
        )
        return self.async_show_form(
            step_id="clone_panel_to_simulation",
            data_schema=schema,
            description_placeholders={
                "panel": device_name or "Span Panel",
            },
            errors=errors,
        )

    async def async_step_manage_simulation_configs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Menu to import or export simulation configs."""
        if user_input is None:
            return self.async_show_menu(
                step_id="manage_simulation_configs",
                menu_options={
                    "simulation_import": "Import Simulation Config",
                    "simulation_export": "Export Simulation Config",
                },
            )
        return self.async_abort(reason="unknown")

    def _get_simulation_schema(self) -> vol.Schema:
        """Get the simulation options schema."""
        return vol.Schema(
            {
                vol.Optional(CONF_SIMULATION_START_TIME): str,
                vol.Optional(CONF_SIMULATION_OFFLINE_MINUTES): int,
            }
        )

    def _get_simulation_defaults(self) -> dict[str, Any]:
        """Get the simulation options defaults."""
        return {
            CONF_SIMULATION_START_TIME: self.config_entry.options.get(
                CONF_SIMULATION_START_TIME, ""
            ),
            CONF_SIMULATION_OFFLINE_MINUTES: self.config_entry.options.get(
                CONF_SIMULATION_OFFLINE_MINUTES, 0
            ),
        }

    def _get_entity_naming_schema(self) -> vol.Schema:
        """Get the entity naming options schema."""
        current_pattern = self._get_current_naming_pattern()

        # Legacy installations can only migrate to friendly names first
        if current_pattern == EntityNamingPattern.LEGACY_NAMES.value:
            pattern_options = {
                EntityNamingPattern.FRIENDLY_NAMES.value: "Friendly Names (e.g., span_panel_kitchen_outlets_power)",
            }
        else:
            # Modern installations can switch between the two modern patterns
            pattern_options = {
                EntityNamingPattern.FRIENDLY_NAMES.value: "Friendly Names (e.g., span_panel_kitchen_outlets_power)",
                EntityNamingPattern.CIRCUIT_NUMBERS.value: "Circuit Numbers (e.g., span_panel_circuit_15_power)",
            }

        return vol.Schema(
            {
                vol.Optional(ENTITY_NAMING_PATTERN): vol.In(pattern_options),
            }
        )

    def _get_current_naming_pattern(self) -> str:
        """Determine the current entity naming pattern from configuration flags."""
        use_circuit_numbers = self.config_entry.options.get(USE_CIRCUIT_NUMBERS, False)
        use_device_prefix = self.config_entry.options.get(USE_DEVICE_PREFIX, False)

        if use_circuit_numbers:
            return EntityNamingPattern.CIRCUIT_NUMBERS.value
        elif use_device_prefix:
            return EntityNamingPattern.FRIENDLY_NAMES.value
        else:
            # Pre-1.0.4 installation - no device prefix
            return EntityNamingPattern.LEGACY_NAMES.value

    async def _migrate_entity_ids(self, old_pattern: str, new_pattern: str) -> None:
        """Migrate entity IDs when naming pattern changes."""
        _LOGGER.info("Starting entity ID migration from %s to %s", old_pattern, new_pattern)

        # Get the coordinator to handle migration using actual entity objects
        coordinator_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        coordinator = coordinator_data.get(COORDINATOR)

        if not coordinator:
            _LOGGER.error("Cannot migrate entities: coordinator not found")
            return

        # Determine old and new flags based on patterns
        old_flags = self._pattern_to_flags(old_pattern)
        new_flags = self._pattern_to_flags(new_pattern)

        # Perform the migration using the coordinator with old and new flags
        success = await coordinator.migrate_synthetic_entities(old_flags, new_flags)

        if success:
            _LOGGER.debug("Entity migration completed successfully")
        else:
            _LOGGER.error("Entity migration failed")

    @staticmethod
    def _entities_have_device_prefix(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
        """Best-effort detection if entities already use the device prefix.

        Checks the entity registry for any entity belonging to this config entry where
        the object_id starts with the device name prefix. Both FRIENDLY_NAMES and CIRCUIT_NUMBERS
        patterns include the device name prefix; only LEGACY lacks it.
        """
        registry = er.async_get(hass)

        # Get the device name from config entry and sanitize it
        device_name = config_entry.data.get("device_name", config_entry.title)
        if not device_name:
            return False

        sanitized_device_name = slugify(device_name)
        for entry in registry.entities.values():
            try:
                if entry.config_entry_id != config_entry.entry_id:
                    continue
                object_id = entry.entity_id.split(".", 1)[1]
                # Check if the object_id starts with the device name followed by underscore
                if object_id.startswith(f"{sanitized_device_name}_"):
                    return True
            except (IndexError, AttributeError):
                continue
        return False

    def _pattern_to_flags(self, pattern: str) -> dict[str, bool]:
        """Convert entity naming pattern to configuration flags."""
        if pattern == EntityNamingPattern.CIRCUIT_NUMBERS.value:
            return {USE_CIRCUIT_NUMBERS: True, USE_DEVICE_PREFIX: True}
        elif pattern == EntityNamingPattern.FRIENDLY_NAMES.value:
            return {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: True}
        else:  # LEGACY_NAMES
            return {USE_CIRCUIT_NUMBERS: False, USE_DEVICE_PREFIX: False}

    def _mark_for_legacy_migration(self) -> None:
        """Mark the config entry for legacy migration after reload.

        This method stores a flag in the config entry data that indicates a legacy
        migration is needed. The integration will check for this flag after startup
        but before the first update.
        """
        _LOGGER.info("Marking config entry for legacy migration after reload")

        # Update the config entry data to include the migration flag
        current_data = dict(self.config_entry.data)
        current_data["pending_legacy_migration"] = True

        _LOGGER.info("Setting pending_legacy_migration flag in config entry data: %s", current_data)

        # Update the config entry with the migration flag
        self.hass.config_entries.async_update_entry(self.config_entry, data=current_data)


# Register the config flow handler
config_entries.HANDLERS.register(DOMAIN)(SpanPanelConfigFlow)


def validate_simulation_time(time_input: str) -> str:
    """Validate and convert simulation time input.

    Supports:
    - Time-only formats: "17:30", "5:30" (24-hour and 12-hour)
    - Full ISO datetime: "2024-06-15T17:30:00"

    Returns:
        ISO datetime string with current date if time-only, or original if full datetime

    Raises:
        ValueError: If the time format is invalid

    """
    if not time_input.strip():
        return ""

    time_input = time_input.strip()

    # Check if it's a full ISO datetime first
    try:
        datetime.fromisoformat(time_input)
        return time_input  # Valid ISO datetime, return as-is
    except ValueError:
        pass  # Not a full datetime, try time-only formats

    # Try time-only formats (HH:MM or H:MM)
    try:
        # Parse the time
        if ":" in time_input:
            parts = time_input.split(":")
            if len(parts) == 2:
                hour = int(parts[0])
                minute = int(parts[1])

                # Validate hour and minute ranges
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    # Convert to current date with the specified time
                    now = datetime.now()
                    time_only = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    return time_only.isoformat()

        raise ValueError(
            f"Invalid time format. Use {', '.join(TIME_ONLY_FORMATS)} or {ISO_DATETIME_FORMAT}"
        )
    except (ValueError, IndexError) as e:
        raise ValueError(
            f"Invalid time format. Use {', '.join(TIME_ONLY_FORMATS)} or {ISO_DATETIME_FORMAT}"
        ) from e
