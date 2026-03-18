"""Span Panel Config Flow."""

from __future__ import annotations

from collections.abc import Mapping
import enum
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowContext,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util.network import is_ipv4_address
from span_panel_api import V2AuthResponse, detect_api_version
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError
import voluptuous as vol

from .config_flow_utils import (
    build_general_options_schema,
    get_general_options_defaults,
    process_general_options_input,
    validate_auth_token,
    validate_host,
    validate_v2_passphrase,
    validate_v2_proximity,
)
from .const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOP_PASSPHRASE,
    CONF_HTTP_PORT,
    CONF_PANEL_SERIAL,
    DOMAIN,
    ENABLE_ENERGY_DIP_COMPENSATION,
    ENTITY_NAMING_PATTERN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
)
from .options import (
    ENERGY_DISPLAY_PRECISION,
    ENERGY_REPORTING_GRACE_PERIOD,
    POWER_DISPLAY_PRECISION,
    SNAPSHOT_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class ConfigFlowError(Exception):
    """Custom exception for config flow internal errors."""


def get_user_data_schema(default_host: str = "") -> vol.Schema:
    """Get the user data schema with optional default host."""
    return vol.Schema(
        {
            vol.Optional(CONF_HOST, default=default_host): str,
            vol.Optional(CONF_HTTP_PORT, default=80): int,
            vol.Optional(POWER_DISPLAY_PRECISION, default=0): int,
            vol.Optional(ENERGY_DISPLAY_PRECISION, default=2): int,
            vol.Optional(ENABLE_ENERGY_DIP_COMPENSATION, default=True): bool,
        }
    )


STEP_USER_DATA_SCHEMA = get_user_data_schema()

STEP_AUTH_TOKEN_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ACCESS_TOKEN): str,
    }
)

STEP_AUTH_PASSPHRASE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOP_PASSPHRASE): str,
    }
)


class TriggerFlowType(enum.Enum):
    """Types of configuration flow triggers."""

    CREATE_ENTRY = enum.auto()
    UPDATE_ENTRY = enum.auto()


class SpanPanelConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for Span Panel."""

    VERSION = 3
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
        self.power_display_precision: int = 0
        self.energy_display_precision: int = 2
        self._is_flow_setup: bool = False
        self.context: ConfigFlowContext = {}
        # Initial naming selection chosen during pre-setup
        self._chosen_use_device_prefix: bool | None = None
        self._chosen_use_circuit_numbers: bool | None = None
        # v2 provisioning state
        self.api_version: str = "v1"
        self._v2_broker_host: str | None = None
        self._v2_broker_port: int | None = None
        self._v2_broker_username: str | None = None
        self._v2_broker_password: str | None = None
        self._v2_passphrase: str | None = None
        self._v2_panel_serial: str | None = None
        self._http_port: int = 80
        # Energy dip compensation default for fresh installs
        self._enable_dip_compensation: bool = True

    async def setup_flow(self, trigger_type: TriggerFlowType, host: str) -> None:
        """Set up the flow by detecting the panel API version and serial number."""

        if self._is_flow_setup is True:
            _LOGGER.error("Flow setup attempted when already set up")
            raise ConfigFlowError("Flow is already set up")

        result = await detect_api_version(host, port=self._http_port)
        self.api_version = result.api_version

        self.trigger_flow_type = trigger_type
        self.host = host

        if result.status_info is not None:
            self.serial_number = result.status_info.serial_number

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

        # Set a preliminary unique_id from the host to prevent duplicate
        # in-progress discovery flows when mDNS fires repeatedly for the
        # same IP. The default raise_on_progress=True causes subsequent
        # flows for the same host to abort immediately with
        # "already_in_progress". This is replaced with the serial number
        # in ensure_not_already_configured() once the device is validated.
        await self.async_set_unique_id(discovery_info.host)

        # Detect whether this is a v2 panel based on zeroconf service type
        svc_type = getattr(discovery_info, "type", "") or ""
        is_v2_service = svc_type in ("_ebus._tcp.local.", "_secure-mqtt._tcp.local.")

        if is_v2_service:
            # v2 panels discovered via eBus / secure-mqtt service types
            # Read optional httpPort from mDNS TXT records (non-standard port)
            props = discovery_info.properties or {}
            http_port_str = props.get("httpPort", props.get("httpport", ""))
            try:
                http_port = int(http_port_str) if http_port_str else 80
            except (ValueError, TypeError):
                http_port = 80
            self._http_port = http_port

            detection = await detect_api_version(discovery_info.host, port=http_port)
            if detection.api_version != "v2" or detection.status_info is None:
                # The v2 endpoint did not respond — this IP is not a valid
                # v2 panel (e.g., an internal link address we didn't filter).
                return self.async_abort(reason="not_span_panel")
            self.api_version = "v2"
            self.host = discovery_info.host
            self.serial_number = detection.status_info.serial_number
            self.trigger_flow_type = TriggerFlowType.CREATE_ENTRY
            self.context = {
                **self.context,
                "title_placeholders": {
                    **self.context.get("title_placeholders", {}),
                    CONF_HOST: discovery_info.host,
                },
            }
            self._is_flow_setup = True
            await self.ensure_not_already_configured()
            return await self.async_step_confirm_discovery()

        # v1 path: validate via REST status endpoint
        if not await validate_host(self.hass, discovery_info.host):
            return self.async_abort(reason="not_span_panel")

        await self.setup_flow(TriggerFlowType.CREATE_ENTRY, discovery_info.host)
        await self.ensure_not_already_configured()
        return await self.async_step_confirm_discovery()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        # Store precision settings from user input (needed for both simulator and regular mode)
        self.power_display_precision = user_input.get(POWER_DISPLAY_PRECISION, 0)
        self.energy_display_precision = user_input.get(ENERGY_DISPLAY_PRECISION, 2)
        self._enable_dip_compensation = user_input.get(ENABLE_ENERGY_DIP_COMPENSATION, True)

        _LOGGER.debug(
            "CONFIG_INPUT_DEBUG: User input precision - power: %s, energy: %s, full input: %s",
            self.power_display_precision,
            self.energy_display_precision,
            user_input,
        )

        host: str = user_input.get(CONF_HOST, "").strip()
        self._http_port = int(user_input.get(CONF_HTTP_PORT, 80))
        if not host:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "host_required"},
            )

        # Validate host before setting up flow
        if not await validate_host(self.hass, host, port=self._http_port):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        # Detect v2 API before setting up the v1 flow
        detection = await detect_api_version(host, port=self._http_port)
        self.api_version = detection.api_version

        if self.api_version == "v2":
            # v2 panels: serial comes from detection, no v1 status probe needed
            self.host = host
            if detection.status_info is not None:
                self.serial_number = detection.status_info.serial_number
            self.trigger_flow_type = TriggerFlowType.CREATE_ENTRY
            self.context = {
                **self.context,
                "title_placeholders": {
                    **self.context.get("title_placeholders", {}),
                    CONF_HOST: host,
                },
            }
            self._is_flow_setup = True
            await self.ensure_not_already_configured()
            return await self.async_step_choose_v2_auth()

        # v1 path: probe via the existing setup_flow
        if not self._is_flow_setup:
            await self.setup_flow(TriggerFlowType.CREATE_ENTRY, host)
            await self.ensure_not_already_configured()

        return await self.async_step_choose_auth_type()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle a flow initiated by re-auth."""
        host = entry_data[CONF_HOST]
        self._http_port = int(entry_data.get(CONF_HTTP_PORT, 80))

        # Detect current API version of the panel
        detection = await detect_api_version(host, port=self._http_port)
        self.api_version = detection.api_version

        if self.api_version == "v2":
            # v2 reauth: set up flow state manually and offer auth choice
            self.host = host
            if detection.status_info is not None:
                self.serial_number = detection.status_info.serial_number
            self.trigger_flow_type = TriggerFlowType.UPDATE_ENTRY
            self._is_flow_setup = True
            return await self.async_step_choose_v2_auth()

        # v1 reauth: existing token flow
        await self.setup_flow(TriggerFlowType.UPDATE_ENTRY, host)
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

        # v2 panels: offer auth method choice after confirmation
        if self.api_version == "v2":
            return await self.async_step_choose_v2_auth()

        # v1 panels: choose between proximity and token auth
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

    async def async_step_choose_v2_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose v2 authentication method: passphrase or proximity."""
        return self.async_show_menu(
            step_id="choose_v2_auth",
            menu_options={
                "auth_passphrase": "Enter Panel Passphrase",
                "auth_proximity": "Proof of Proximity (open/close door)",
            },
        )

    async def async_step_auth_proximity(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Guide user through v2 proof-of-proximity authentication."""
        if user_input is None:
            return self.async_show_form(
                step_id="auth_proximity",
                data_schema=vol.Schema({}),
            )

        if not self.host:
            return self.async_abort(reason="host_not_set")

        try:
            result = await validate_v2_proximity(self.host, port=self._http_port)
        except SpanPanelAuthError:
            return self.async_show_form(
                step_id="auth_proximity",
                data_schema=vol.Schema({}),
                errors={"base": "proximity_failed"},
            )
        except SpanPanelConnectionError:
            return self.async_show_form(
                step_id="auth_proximity",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )

        self._store_v2_auth_result(result, passphrase="")
        return await self._async_finalize_v2_auth()

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
            if not await validate_auth_token(self.hass, self.host, self.access_token):
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

    async def async_step_auth_passphrase(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Collect the panel passphrase for v2 authentication."""
        if user_input is None:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
            )

        passphrase = user_input.get(CONF_HOP_PASSPHRASE, "").strip()
        if not passphrase:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
                errors={"base": "invalid_auth"},
            )

        if not self.host:
            return self.async_abort(reason="host_not_set")

        try:
            result = await validate_v2_passphrase(self.host, passphrase, port=self._http_port)
        except SpanPanelAuthError:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
                errors={"base": "invalid_auth"},
            )
        except SpanPanelConnectionError:
            return self.async_show_form(
                step_id="auth_passphrase",
                data_schema=STEP_AUTH_PASSPHRASE_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        self._store_v2_auth_result(result, passphrase)
        return await self._async_finalize_v2_auth()

    def _store_v2_auth_result(self, result: V2AuthResponse, passphrase: str) -> None:
        """Store v2 auth credentials from registration result."""
        self.access_token = result.access_token
        self._v2_broker_host = result.ebus_broker_host
        self._v2_broker_port = result.ebus_broker_mqtts_port
        self._v2_broker_username = result.ebus_broker_username
        self._v2_broker_password = result.ebus_broker_password
        self._v2_passphrase = passphrase
        self._v2_panel_serial = result.serial_number

    async def _async_finalize_v2_auth(self) -> ConfigFlowResult:
        """Route to appropriate next step after successful v2 auth."""
        if self.trigger_flow_type == TriggerFlowType.UPDATE_ENTRY:
            if "entry_id" not in self.context:
                raise ValueError("Entry ID is missing from context")
            return self._update_v2_entry(self.context["entry_id"])
        return await self.async_step_choose_entity_naming_initial()

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

        entry_data: dict[str, Any] = {
            CONF_HOST: host,
            CONF_ACCESS_TOKEN: access_token,
            "device_name": device_name,
        }

        # Add v2-specific fields
        if self.api_version == "v2":
            entry_data[CONF_API_VERSION] = "v2"
            entry_data[CONF_EBUS_BROKER_HOST] = self._v2_broker_host
            entry_data[CONF_EBUS_BROKER_PORT] = self._v2_broker_port
            entry_data[CONF_EBUS_BROKER_USERNAME] = self._v2_broker_username
            entry_data[CONF_EBUS_BROKER_PASSWORD] = self._v2_broker_password
            entry_data[CONF_HOP_PASSPHRASE] = self._v2_passphrase
            entry_data[CONF_PANEL_SERIAL] = self._v2_panel_serial
            if self._http_port != 80:
                entry_data[CONF_HTTP_PORT] = self._http_port

        return self.async_create_entry(
            title=device_name,
            data=entry_data,
            options={
                USE_DEVICE_PREFIX: use_device_prefix,
                USE_CIRCUIT_NUMBERS: use_circuit_numbers,
                POWER_DISPLAY_PRECISION: self.power_display_precision,
                ENERGY_DISPLAY_PRECISION: self.energy_display_precision,
                ENABLE_ENERGY_DIP_COMPENSATION: self._enable_dip_compensation,
            },
        )

    def _update_v2_entry(self, entry_id: str) -> ConfigFlowResult:
        """Update an existing config entry with new v2 MQTT credentials."""
        entry: ConfigEntry[Any] | None = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            _LOGGER.error("Config entry %s does not exist during v2 reauth", entry_id)
            return self.async_abort(reason="reauth_failed")

        updated_data = dict(entry.data)
        updated_data[CONF_ACCESS_TOKEN] = self.access_token
        updated_data[CONF_API_VERSION] = "v2"
        updated_data[CONF_EBUS_BROKER_HOST] = self._v2_broker_host
        updated_data[CONF_EBUS_BROKER_PORT] = self._v2_broker_port
        updated_data[CONF_EBUS_BROKER_USERNAME] = self._v2_broker_username
        updated_data[CONF_EBUS_BROKER_PASSWORD] = self._v2_broker_password
        updated_data[CONF_HOP_PASSPHRASE] = self._v2_passphrase
        updated_data[CONF_PANEL_SERIAL] = self._v2_panel_serial
        if self._http_port != 80:
            updated_data[CONF_HTTP_PORT] = self._http_port

        self.hass.config_entries.async_update_entry(entry, data=updated_data)
        self.hass.async_create_task(self.hass.config_entries.async_reload(entry_id))
        return self.async_abort(reason="reauth_successful")

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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration (e.g. host change)."""
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is None:
            current_host = reconfigure_entry.data.get(CONF_HOST, "")
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=current_host): str}),
            )

        host = user_input[CONF_HOST].strip()
        if not host:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=""): str}),
                errors={"base": "host_required"},
            )

        # Validate the host is reachable and is a v2 panel
        http_port = int(reconfigure_entry.data.get(CONF_HTTP_PORT, 80))
        try:
            detection = await detect_api_version(host, port=http_port)
        except (SpanPanelConnectionError, Exception):
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors={"base": "cannot_connect"},
            )

        if detection.api_version != "v2" or detection.status_info is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors={"base": "cannot_connect"},
            )

        # Ensure the serial number matches — prevent switching to a different panel
        await self.async_set_unique_id(detection.status_info.serial_number)
        self._abort_if_unique_id_mismatch(reason="unique_id_mismatch")

        return self.async_update_reload_and_abort(
            reconfigure_entry,
            data_updates={CONF_HOST: host},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Create the options flow."""
        return OptionsFlowHandler()


OPTIONS_SCHEMA: vol.Schema = vol.Schema(
    {
        vol.Optional(SNAPSHOT_UPDATE_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=15)
        ),
        vol.Optional(ENTITY_NAMING_PATTERN): vol.In([e.value for e in EntityNamingPattern]),
        vol.Optional(ENERGY_REPORTING_GRACE_PERIOD): vol.All(int, vol.Range(min=0, max=60)),
    }
)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow for Span Panel."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start the options flow with general options directly."""
        return await self.async_step_general_options(user_input)

    async def async_step_general_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the general options (excluding entity naming)."""
        if user_input is not None:
            # Process the user input using the utility function
            filtered_input, errors = process_general_options_input(self.config_entry, user_input)

            # If no errors, proceed with saving options
            if not errors:
                return self.async_create_entry(title="", data=filtered_input)
        else:
            errors = {}

        # Build schema and defaults using utility functions
        schema = build_general_options_schema(self.config_entry)
        defaults = get_general_options_defaults(self.config_entry)

        return self.async_show_form(
            step_id="general_options",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )


# Register the config flow handler
config_entries.HANDLERS.register(DOMAIN)(SpanPanelConfigFlow)
