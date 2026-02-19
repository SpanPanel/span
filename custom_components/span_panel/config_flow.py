"""Span Panel Config Flow."""

from __future__ import annotations

from collections.abc import Mapping
import enum
import logging
from pathlib import Path
import shutil
from time import time
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
from span_panel_api.simulation import DynamicSimulationEngine, SimulationConfig
import voluptuous as vol
import yaml

from custom_components.span_panel.span_panel_hardware_status import (
    SpanPanelHardwareStatus,
)

from .config_flow_utils import (
    build_general_options_schema,
    get_available_simulation_configs,
    get_available_unmapped_tabs,
    get_current_naming_pattern,
    get_general_options_defaults,
    pattern_to_flags,
    process_general_options_input,
    validate_auth_token,
    validate_host,
    validate_simulation_time,
)
from .config_flow_utils.options import (
    build_entity_naming_options_schema,
    get_entity_naming_options_defaults,
    process_entity_naming_options_input,
)
from .const import (
    CONF_API_RETRIES,
    CONF_API_RETRY_BACKOFF_MULTIPLIER,
    CONF_API_RETRY_TIMEOUT,
    CONF_PANEL_GEN,
    CONF_SIMULATION_CONFIG,
    CONF_SIMULATION_OFFLINE_MINUTES,
    CONF_SIMULATION_START_TIME,
    CONF_USE_SSL,
    CONFIG_API_RETRIES,
    CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    CONFIG_API_RETRY_TIMEOUT,
    CONFIG_TIMEOUT,
    COORDINATOR,
    DOMAIN,
    ENTITY_NAMING_PATTERN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from .helpers import generate_unique_simulator_serial_number
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

        # Validate host before setting up flow (Gen2 REST API)
        if not await validate_host(self.hass, host, use_ssl=use_ssl):
            # REST failed — try Gen3 gRPC as fallback
            if await self._test_gen3_connection(host):
                _LOGGER.info("Gen3 panel detected at %s (REST unavailable, gRPC OK)", host)
                # Gen3 panels don't need auth — create entry directly
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SPAN Panel ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PANEL_GEN: "gen3",
                    },
                )

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

    async def _test_gen3_connection(self, host: str) -> bool:
        """Test if the host is a Gen3 panel via gRPC on port 50065."""
        try:
            from .gen3.span_grpc_client import SpanGrpcClient  # noqa: E402

            client = SpanGrpcClient(host)
            return await client.test_connection()
        except Exception:
            _LOGGER.debug("Gen3 gRPC connection test failed for %s", host)
            return False

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

        # Generate unique simulator serial number first
        simulator_serial = generate_unique_simulator_serial_number(self.hass)

        # Use the generated simulator serial number as the host
        # This ensures the span panel API uses the correct serial number
        host = simulator_serial

        # Create entry for simulator mode
        base_name = "Span Simulator"
        device_name = self.get_unique_device_name(base_name)

        # Prepare config data
        config_data = {
            CONF_HOST: host,  # This is now the simulator serial number (sim-nnn)
            CONF_ACCESS_TOKEN: "simulator_token",
            CONF_USE_SSL: False,
            "simulation_mode": True,
            CONF_SIMULATION_CONFIG: simulation_config,
            "device_name": device_name,
            "simulator_serial_number": simulator_serial,
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
        if user_input is None:
            menu_options = {
                "general_options": "General Options",
            }

            # Add entity naming options only for live panels (not simulations)
            if not self.config_entry.data.get("simulation_mode", False):
                menu_options["entity_naming_options"] = "Entity Naming Options"

            # Add simulation options if this is a simulation mode integration
            if self.config_entry.data.get("simulation_mode", False):
                menu_options["simulation_start_time"] = "Simulation Start Time"
                menu_options["simulation_offline_minutes"] = "Simulation Offline Minutes"
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
        # Get available unmapped tabs for dropdown
        available_tabs = await get_available_unmapped_tabs(self.hass, self.config_entry)

        if user_input is not None:
            # Process the user input using the utility function
            filtered_input, errors = process_general_options_input(
                self.config_entry, user_input, available_tabs
            )

            # If no errors, proceed with saving options
            if not errors:
                return self.async_create_entry(title="", data=filtered_input)
        else:
            errors = {}

        # Get current values for dynamic filtering
        try:
            current_leg1 = int(self.config_entry.options.get(INVERTER_LEG1, 0))
        except (TypeError, ValueError):
            current_leg1 = 0
        try:
            current_leg2 = int(self.config_entry.options.get(INVERTER_LEG2, 0))
        except (TypeError, ValueError):
            current_leg2 = 0

        # Build schema and defaults using utility functions
        schema = build_general_options_schema(
            self.config_entry, available_tabs, current_leg1, current_leg2, user_input
        )
        defaults = get_general_options_defaults(self.config_entry, current_leg1, current_leg2)

        return self.async_show_form(
            step_id="general_options",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )

    async def async_step_entity_naming_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage entity naming options including legacy upgrade and naming patterns."""
        if user_input is not None:
            # Process the user input for entity naming options
            filtered_input, errors = process_entity_naming_options_input(
                self.config_entry, user_input
            )

            # If no errors, proceed with saving options
            if not errors:
                # Check if there are pending migrations that need to be handled by coordinator
                if filtered_input.get("pending_legacy_migration", False) or filtered_input.get(
                    "pending_naming_migration", False
                ):
                    # Merge with existing options to preserve all settings
                    merged_options = dict(self.config_entry.options)
                    merged_options.update(filtered_input)

                    # Log the migration flags for debugging
                    _LOGGER.info(
                        "Setting migration flags: pending_naming_migration=%s, old_flags=(%s,%s), new_flags=(%s,%s)",
                        merged_options.get("pending_naming_migration", False),
                        merged_options.get("old_use_circuit_numbers", "None"),
                        merged_options.get("old_use_device_prefix", "None"),
                        merged_options.get(USE_CIRCUIT_NUMBERS, "None"),
                        merged_options.get(USE_DEVICE_PREFIX, "None"),
                    )

                    # Return the merged options to trigger reload with migration flags
                    return self.async_create_entry(title="", data=merged_options)
                else:
                    # No pending migrations, proceed with normal reload
                    # Merge with existing options to preserve all settings
                    merged_options = dict(self.config_entry.options)
                    merged_options.update(filtered_input)
                    return self.async_create_entry(title="", data=merged_options)
        else:
            errors = {}

        # Build the entity naming options schema
        schema = build_entity_naming_options_schema(self.config_entry)
        defaults = get_entity_naming_options_defaults(self.config_entry)

        return self.async_show_form(
            step_id="entity_naming_options",
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

    async def async_step_simulation_start_time(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit simulation start time settings."""
        if user_input is not None:
            simulation_start_time = user_input.get(CONF_SIMULATION_START_TIME, "").strip()

            _LOGGER.info("Edit simulation start time - start_time: %s", simulation_start_time)

            if simulation_start_time:
                try:
                    simulation_start_time = validate_simulation_time(simulation_start_time)
                    user_input[CONF_SIMULATION_START_TIME] = simulation_start_time
                except ValueError as e:
                    return self.async_show_form(
                        step_id="simulation_start_time",
                        data_schema=self.add_suggested_values_to_schema(
                            self._get_simulation_start_time_schema(),
                            self._get_simulation_start_time_defaults(),
                        ),
                        errors={"base": str(e)},
                    )

            # Merge with existing options to preserve other settings
            merged_options = dict(self.config_entry.options)
            merged_options.update(user_input)

            # Clean up any simulation-only change flag since this will trigger a reload
            merged_options.pop("_simulation_only_change", None)

            _LOGGER.info("Saving simulation start time: %s", user_input)
            _LOGGER.info("Merged options: %s", merged_options)

            return self.async_create_entry(title="", data=merged_options)

        return self.async_show_form(
            step_id="simulation_start_time",
            data_schema=self.add_suggested_values_to_schema(
                self._get_simulation_start_time_schema(),
                self._get_simulation_start_time_defaults(),
            ),
        )

    async def async_step_simulation_offline_minutes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit simulation offline minutes settings."""
        if user_input is not None:
            offline_minutes = user_input.get(CONF_SIMULATION_OFFLINE_MINUTES, 0)

            _LOGGER.info("Edit simulation offline minutes - offline_minutes: %s", offline_minutes)

            # Merge with existing options to preserve other settings
            merged_options = dict(self.config_entry.options)
            merged_options.update(user_input)

            # Add a flag to indicate this is a simulation-only change
            merged_options["_simulation_only_change"] = True

            # Add a timestamp to force change detection even when offline_minutes value is the same
            # This ensures the update listener is called to restart the offline timer
            merged_options["_simulation_timestamp"] = int(time())

            return self.async_create_entry(title="", data=merged_options)

        return self.async_show_form(
            step_id="simulation_offline_minutes",
            data_schema=self.add_suggested_values_to_schema(
                self._get_simulation_offline_minutes_schema(),
                self._get_simulation_offline_minutes_defaults(),
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

        # If user_input was provided and there are no errors, the operation succeeded
        if user_input is not None and not errors:
            return self.async_create_entry(
                title="Simulation Created",
                data={},
                description=f"Cloned panel to {dest_path.name} in simulation_configs",
            )

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

    def _get_simulation_start_time_schema(self) -> vol.Schema:
        """Get the simulation start time schema."""
        return vol.Schema(
            {
                vol.Optional(CONF_SIMULATION_START_TIME): str,
            }
        )

    def _get_simulation_start_time_defaults(self) -> dict[str, Any]:
        """Get the simulation start time defaults."""
        return {
            CONF_SIMULATION_START_TIME: self.config_entry.options.get(
                CONF_SIMULATION_START_TIME, ""
            ),
        }

    def _get_simulation_offline_minutes_schema(self) -> vol.Schema:
        """Get the simulation offline minutes schema."""
        return vol.Schema(
            {
                vol.Optional(CONF_SIMULATION_OFFLINE_MINUTES): int,
            }
        )

    def _get_simulation_offline_minutes_defaults(self) -> dict[str, Any]:
        """Get the simulation offline minutes defaults."""
        return {
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
        return get_current_naming_pattern(self.config_entry)

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
        success = await coordinator.migrate_entity_ids(old_flags, new_flags)

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
        return pattern_to_flags(pattern)

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


# Export commonly used items for backward compatibility

# Register the config flow handler
config_entries.HANDLERS.register(DOMAIN)(SpanPanelConfigFlow)
