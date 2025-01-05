"""Span Panel Config Flow"""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import (CONF_ACCESS_TOKEN, CONF_HOST,
                                 CONF_SCAN_INTERVAL)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.network import is_ipv4_address

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .options import (BATTERY_ENABLE, INVERTER_ENABLE, INVERTER_LEG1,
                      INVERTER_LEG2)
from .span_panel_api import SpanPanelApi

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)

STEP_AUTH_TOKEN_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ACCESS_TOKEN): str,
    }
)


class TriggerFlowType(enum.Enum):
    CREATE_ENTRY = enum.auto()
    UPDATE_ENTRY = enum.auto()


def create_api_controller(
    hass: HomeAssistant, host: str, access_token: str | None = None
) -> SpanPanelApi:
    params: dict[str, Any] = {"host": host, "async_client": get_async_client(hass)}
    if access_token is not None:
        params["access_token"] = access_token
    return SpanPanelApi(**params)


async def validate_host(
    hass: HomeAssistant, host: str, access_token: str | None = None
) -> bool:
    span_api = create_api_controller(hass, host, access_token)
    if access_token:
        return await span_api.ping_with_auth()
    return await span_api.ping()


async def validate_auth_token(hass: HomeAssistant, host: str, access_token: str) -> bool:
    """Perform an authenticated call to confirm validity of provided token."""
    span_api = create_api_controller(hass, host, access_token)
    return await span_api.ping_with_auth()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore
    """
    Handle a config flow for Span Panel.
    """

    VERSION = 1

    def __init__(self) -> None:
        self.trigger_flow_type: TriggerFlowType | None = None
        self.host: str | None = None
        self.serial_number: str | None = None
        self.access_token: str | None = None

        self._is_flow_setup: bool = False

    async def setup_flow(self, trigger_type: TriggerFlowType, host: str):
        """Set up the flow."""

        if self._is_flow_setup is True:
            raise AssertionError("Flow is already set up")

        span_api = create_api_controller(self.hass, host)
        panel_status = await span_api.get_status_data()

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

    def ensure_flow_is_set_up(self):
        """Ensure the flow is set up."""
        if self._is_flow_setup is False:
            raise AssertionError("Flow is not set up")

    async def ensure_not_already_configured(self):
        """Ensure the panel is not already configured."""
        self.ensure_flow_is_set_up()

        # Abort if we had already set this panel up
        await self.async_set_unique_id(self.serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """
        Handle a flow initiated by zeroconf discovery.
        """
        # Do not probe device if the host is already configured
        self._async_abort_entries_match({CONF_HOST: discovery_info.host})

        # Do not probe device if it is not an ipv4 address
        if not is_ipv4_address(discovery_info.host):
            return self.async_abort(reason="not_ipv4_address")

        # Validate that this is a valid Span Panel
        if not await validate_host(self.hass, discovery_info.host):
            return self.async_abort(reason="not_span_panel")

        await self.setup_flow(TriggerFlowType.CREATE_ENTRY, discovery_info.host)
        await self.ensure_not_already_configured()
        return await self.async_step_confirm_discovery()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        host = user_input[CONF_HOST].strip()
        
        # Validate host before setting up flow
        if not await validate_host(self.hass, host):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        # Only setup flow if validation succeeded
        if not self._is_flow_setup:
            await self.setup_flow(TriggerFlowType.CREATE_ENTRY, host)
            await self.ensure_not_already_configured()
            
        return await self.async_step_choose_auth_type()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """
        Handle a flow initiated by re-auth.
        """
        await self.setup_flow(TriggerFlowType.UPDATE_ENTRY, entry_data[CONF_HOST])
        return await self.async_step_auth_token(dict(entry_data))

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Prompt user to confirm a discovered Span Panel.
        """
        self.ensure_flow_is_set_up()

        # Prompt the user for confirmation
        if user_input is None:
            self._set_confirm_only()
            return self.async_show_form(
                step_id="confirm_discovery",
                description_placeholders={
                    "host": self.host,
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
        """
        Step that guide users through the proximity authentication process.
        """
        self.ensure_flow_is_set_up()

        span_api = create_api_controller(self.hass, self.host or "")
        panel_status = await span_api.get_status_data()

        # Check if running firmware newer or older than r202342
        if panel_status.proximity_proven is not None:
            # Reprompt until we are able to do proximity auth for new firmware
            proximity_verified = panel_status.proximity_proven
            if proximity_verified is False:
                return self.async_show_form(step_id="auth_proximity")
        else:
            # Reprompt until we are able to do proximity auth for old firmware
            remaining_presses = panel_status.remaining_auth_unlock_button_presses
            if (remaining_presses != 0):
                return self.async_show_form(
                    step_id="auth_proximity",
                )

        # Ensure host is set
        if not self.host:
            return self.async_abort(reason="host_not_set")

        # Ensure token is valid using authenticated validation
        self.access_token = await span_api.get_access_token()
        if not await validate_auth_token(self.hass, self.host, self.access_token):
            return self.async_abort(reason="invalid_access_token")

        return await self.async_step_resolve_entity(entry_data)

    async def async_step_auth_token(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """
        Step that prompts user for access token.
        """
        self.ensure_flow_is_set_up()

        if user_input is None:
            # Show the form to prompt for the access token
            return self.async_show_form(
                step_id="auth_token", data_schema=STEP_AUTH_TOKEN_DATA_SCHEMA
            )

        # Extract access token from user input
        access_token = user_input.get(CONF_ACCESS_TOKEN)
        if access_token:
            self.access_token = access_token

            # Ensure host is set
            if not self.host:
                return self.async_abort(reason="host_not_set")

            # Validate the provided token
            if not await validate_auth_token(self.hass, self.host, access_token):
                return self.async_show_form(
                    step_id="auth_token",
                    data_schema=STEP_AUTH_TOKEN_DATA_SCHEMA,
                    errors={"base": "invalid_access_token"},
                )

            # Proceed to the next step upon successful validation
            return await self.async_step_resolve_entity(user_input)

        # If no access token was provided, abort or show the form again
        return self.async_abort(reason="missing_access_token")

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
                    raise ValueError(
                        "Access token cannot be None when updating an entry"
                    )
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
        """
        Creates a new SPAN panel entry.
        """
        return self.async_create_entry(
            title=serial_number, data={CONF_HOST: host, CONF_ACCESS_TOKEN: access_token}
        )

    def update_existing_entry(
        self,
        entry_id: str,
        host: str,
        access_token: str,
        entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """
        Updates an existing entry with new configurations.
        """
        # Update the existing data with reauthed data
        # Create a new mutable copy of the entry data (Mapping is immutable)
        updated_data = dict(entry_data)
        updated_data[CONF_HOST] = host
        updated_data[CONF_ACCESS_TOKEN] = access_token

        # An existing entry must exist before we can update it
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise AssertionError("Entry does not exist")

        self.hass.config_entries.async_update_entry(entry, data=updated_data)
        self.hass.async_create_task(self.hass.config_entries.async_reload(entry_id))
        return self.async_abort(reason="reauth_successful")

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow by passing entry_id."""
        return OptionsFlowHandler(entry_id=config_entry.entry_id)


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCAN_INTERVAL): vol.All(
            int, vol.Range(min=5)
        ),
        vol.Optional(BATTERY_ENABLE): bool,
        vol.Optional(INVERTER_ENABLE): bool,
        vol.Optional(INVERTER_LEG1): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
        vol.Optional(INVERTER_LEG2): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
    }
)

class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow for Span Panel without storing config_entry."""

    def __init__(self, entry_id: str) -> None:
        """Initialize with entry_id only."""
        self._entry_id = entry_id

    @property
    def entry(self) -> config_entries.ConfigEntry:
        """Get the config entry using the stored entry_id."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if not entry:
            raise ValueError("Config entry not found")
        return entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        defaults = {
            CONF_SCAN_INTERVAL: self.entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds
            ),
            BATTERY_ENABLE: self.entry.options.get("enable_battery_percentage", False),
            INVERTER_ENABLE: self.entry.options.get("enable_solar_circuit", False),
            INVERTER_LEG1: self.entry.options.get(INVERTER_LEG1, 0),
            INVERTER_LEG2: self.entry.options.get(INVERTER_LEG2, 0),
        }

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, defaults
            ),
        )
