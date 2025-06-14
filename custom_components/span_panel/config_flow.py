"""Span Panel Config Flow."""

from __future__ import annotations

from collections.abc import Mapping
import enum
import logging
from typing import Any, TYPE_CHECKING
import uuid


from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowContext,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util.network import is_ipv4_address
import voluptuous as vol

from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError
from span_panel_api import SpanPanelClient

from custom_components.span_panel.span_panel_hardware_status import (
    SpanPanelHardwareStatus,
)

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTITY_NAMING_PATTERN,
    USE_CIRCUIT_NUMBERS,
    USE_DEVICE_PREFIX,
    EntityNamingPattern,
    CONF_USE_SSL,
    CONFIG_TIMEOUT,
    CONFIG_API_RETRIES,
    CONFIG_API_RETRY_TIMEOUT,
    CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    CONF_API_RETRIES,
    CONF_API_RETRY_TIMEOUT,
    CONF_API_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_API_RETRIES,
    DEFAULT_API_RETRY_TIMEOUT,
    DEFAULT_API_RETRY_BACKOFF_MULTIPLIER,
)
from .entity_migration import EntityMigrationManager
from .options import BATTERY_ENABLE, INVERTER_ENABLE, INVERTER_LEG1, INVERTER_LEG2
from .span_panel_api import SpanPanelApi

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from span_panel_api import SpanPanelClient

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_USE_SSL, default=False): bool,
    }
)

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

    VERSION = 1
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
        self._is_flow_setup: bool = False
        self.context: ConfigFlowContext = {}

    async def setup_flow(
        self, trigger_type: TriggerFlowType, host: str, use_ssl: bool = False
    ) -> None:
        """Set up the flow."""

        if self._is_flow_setup is True:
            raise AssertionError("Flow is already set up")

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
            raise AssertionError("Flow is not set up")

    async def ensure_not_already_configured(self) -> None:
        """Ensure the panel is not already configured."""
        self.ensure_flow_is_set_up()

        # Abort if we had already set this panel up
        await self.async_set_unique_id(self.serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

        host: str = user_input[CONF_HOST].strip()
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
        if not await validate_auth_token(
            self.hass, self.host, self.access_token, self.use_ssl
        ):
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
            if not await validate_auth_token(
                self.hass, self.host, self.access_token, self.use_ssl
            ):
                return self.async_show_form(
                    step_id="auth_token",
                    data_schema=STEP_AUTH_TOKEN_DATA_SCHEMA,
                    errors={"base": "invalid_access_token"},
                )

            # Proceed to the next step upon successful validation
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
                    raise ValueError(
                        "Serial number cannot be None when creating a new entry"
                    )
                if self.access_token is None:
                    raise ValueError(
                        "Access token cannot be None when creating a new entry"
                    )
                return self.create_new_entry(
                    self.host, self.serial_number, self.access_token
                )
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
        return self.async_create_entry(
            title=serial_number,
            data={
                CONF_HOST: host,
                CONF_ACCESS_TOKEN: access_token,
                CONF_USE_SSL: self.use_ssl,
            },
            options={
                USE_DEVICE_PREFIX: True,
                USE_CIRCUIT_NUMBERS: True,
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
            raise AssertionError("Entry does not exist")

        self.hass.config_entries.async_update_entry(entry, data=updated_data)
        self.hass.async_create_task(self.hass.config_entries.async_reload(entry_id))
        return self.async_abort(reason="reauth_successful")

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
    }
)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow for Span Panel."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main options menu."""
        if user_input is None:
            return self.async_show_menu(
                step_id="init",
                menu_options={
                    "general_options": "General Options",
                    "entity_naming": "Entity Naming Pattern",
                },
            )

            # This shouldn't be reached since we're showing a menu
        return self.async_abort(reason="unknown")

    async def async_step_general_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the general options (excluding entity naming)."""
        if user_input is not None:
            # Preserve existing naming flags (don't change them in general options)
            use_prefix: Any | bool = self.config_entry.options.get(USE_DEVICE_PREFIX, False)
            user_input[USE_DEVICE_PREFIX] = use_prefix

            use_circuit_numbers: Any | bool = self.config_entry.options.get(
                USE_CIRCUIT_NUMBERS, False
            )
            user_input[USE_CIRCUIT_NUMBERS] = use_circuit_numbers

            # Remove any entity naming pattern from input (shouldn't be there anyway)
            user_input.pop(ENTITY_NAMING_PATTERN, None)

            return self.async_create_entry(title="", data=user_input)

        # Show general options form (without entity naming)
        defaults: dict[str, Any] = {
            CONF_SCAN_INTERVAL: self.config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds
            ),
            BATTERY_ENABLE: self.config_entry.options.get(
                "enable_battery_percentage", False
            ),
            INVERTER_ENABLE: self.config_entry.options.get("enable_solar_circuit", False),
            INVERTER_LEG1: self.config_entry.options.get(INVERTER_LEG1, 0),
            INVERTER_LEG2: self.config_entry.options.get(INVERTER_LEG2, 0),
            CONF_API_RETRIES: self.config_entry.options.get(
                CONF_API_RETRIES, DEFAULT_API_RETRIES
            ),
            CONF_API_RETRY_TIMEOUT: self.config_entry.options.get(
                CONF_API_RETRY_TIMEOUT, DEFAULT_API_RETRY_TIMEOUT
            ),
            CONF_API_RETRY_BACKOFF_MULTIPLIER: self.config_entry.options.get(
                CONF_API_RETRY_BACKOFF_MULTIPLIER, DEFAULT_API_RETRY_BACKOFF_MULTIPLIER
            ),
        }

        return self.async_show_form(
            step_id="general_options",
            data_schema=self.add_suggested_values_to_schema(
                self._get_general_options_schema(), defaults
            ),
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

                # Migrate entity IDs in the entity registry
                await self._migrate_entity_ids(current_pattern, new_pattern)

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
                _LOGGER.info("Preserving existing options: %s", preserved_options)
                _LOGGER.info(
                    "Solar sensor enabled: %s",
                    current_options.get(INVERTER_ENABLE, False),
                )
                _LOGGER.info("Inverter leg 1: %s", current_options.get(INVERTER_LEG1, 0))
                _LOGGER.info("Inverter leg 2: %s", current_options.get(INVERTER_LEG2, 0))
                _LOGGER.info("All options after update: %s", current_options)

                # Schedule reload after the options flow completes
                async def reload_after_options_complete() -> None:
                    # Wait for the options flow to complete first
                    await self.hass.async_block_till_done()
                    _LOGGER.info("Reloading integration after entity naming pattern change")
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)

                self.hass.async_create_task(reload_after_options_complete())

                # Return success with the updated options - this will update the config entry
                _LOGGER.info("Returning updated options to complete the flow")
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

        # Debug logging to help diagnose the translation issue
        _LOGGER.info("Entity naming step - current pattern: %s", current_pattern)
        _LOGGER.info(
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

    def _get_general_options_schema(self) -> vol.Schema:
        """Get the general options schema (excluding entity naming)."""
        return vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL): vol.All(int, vol.Range(min=5)),
                vol.Optional(BATTERY_ENABLE): bool,
                vol.Optional(INVERTER_ENABLE): bool,
                vol.Optional(INVERTER_LEG1): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(INVERTER_LEG2): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(CONF_API_RETRIES): vol.All(int, vol.Range(min=0, max=10)),
                vol.Optional(CONF_API_RETRY_TIMEOUT): vol.All(
                    vol.Coerce(float), vol.Range(min=0.1, max=10.0)
                ),
                vol.Optional(CONF_API_RETRY_BACKOFF_MULTIPLIER): vol.All(
                    vol.Coerce(float), vol.Range(min=1.0, max=5.0)
                ),
            }
        )

    def _get_entity_naming_schema(self) -> vol.Schema:
        """Get the entity naming options schema."""
        # Pre-1.0.4 installations can only migrate to the two modern patterns
        # Modern installations can switch between the two modern patterns

        # Create friendly descriptions with examples
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

        # Create migration manager
        migration_manager = EntityMigrationManager(self.hass, self.config_entry.entry_id)

        # Convert string patterns to enum values
        from_pattern = EntityNamingPattern(old_pattern)
        to_pattern = EntityNamingPattern(new_pattern)

        # Perform the migration
        success = await migration_manager.migrate_entities(from_pattern, to_pattern)

        if success:
            _LOGGER.info("Entity migration completed successfully")
        else:
            _LOGGER.error("Entity migration failed")

    def _generate_new_entity_id(
        self, old_entity_id: str, old_pattern: str, new_pattern: str
    ) -> str | None:
        """Generate new entity ID based on the new naming pattern."""
        # This method is deprecated in favor of EntityMigrationManager
        # Keeping it for backward compatibility but it will be removed
        _LOGGER.warning(
            "Using deprecated _generate_new_entity_id method for %s", old_entity_id
        )
        return None


# Register the config flow handler
config_entries.HANDLERS.register(DOMAIN)(SpanPanelConfigFlow)
