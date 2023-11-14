from __future__ import annotations

import enum
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.network import is_ipv4_address

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .span_panel_api import SpanPanelApi

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)

STEP_AUTH_TOKEN_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): str,
    }
)


class TriggerFlowType(enum.Enum):
    CREATE_ENTRY = enum.auto()
    UPDATE_ENTRY = enum.auto()


def create_api_controller(
    hass: HomeAssistant, host: str, access_token: str | None = None
) -> SpanPanelApi:
    params = {"host": host, "async_client": get_async_client(hass)}
    if access_token is not None:
        params["access_token"] = access_token
    return SpanPanelApi(**params)


async def validate_host(
    hass: HomeAssistant, host: str, access_token: str | None = None
) -> bool:
    span_api = create_api_controller(hass, host, access_token)
    return await span_api.ping()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
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
        assert self._is_flow_setup is False

        span_api = create_api_controller(self.hass, host)
        panel_status = await span_api.get_status_data()

        self.trigger_flow_type = trigger_type
        self.host = host
        self.serial_number = panel_status.serial_number

        self.context.setdefault("title_placeholders", {})[CONF_HOST] = self.host

        self._is_flow_setup = True

    def ensure_flow_is_set_up(self):
        assert self._is_flow_setup is True

    async def ensure_not_already_configured(self):
        self.ensure_flow_is_set_up()

        # Abort if we had already set this panel up
        await self.async_set_unique_id(self.serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
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
    ) -> FlowResult:
        """
        Handle a flow initiated by the user.
        """
        # Prompt the user for input if haven't done so
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        # Validate host is a valid Span Panel, prompt user again
        if not await validate_host(self.hass, user_input[CONF_HOST]):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        await self.setup_flow(TriggerFlowType.CREATE_ENTRY, user_input[CONF_HOST])
        await self.ensure_not_already_configured()
        return await self.async_step_choose_auth_type()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """
        Handle a flow initiated by re-auth.
        """

        await self.setup_flow(TriggerFlowType.UPDATE_ENTRY, entry_data[CONF_HOST])
        return await self.async_step_auth_proximity(entry_data)

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
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

        return await self.async_step_choose_auth_type()

    async def async_step_choose_auth_type(
        self,
    ) -> FlowResult:
        self.ensure_flow_is_set_up()

        return self.async_show_menu(
            step_id="choose_auth_type",
            menu_options={
                "auth_proximity": "Manual",
                "auth_token": "Auth Token",
            },
        )

    async def async_step_auth_proximity(
        self,
        entry_data: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Step that guide users through the proximity authentication process.
        """
        self.ensure_flow_is_set_up()

        span_api = create_api_controller(self.hass, self.host)
        panel_status = await span_api.get_status_data()

        # Reprompt until we are able to do proximity auth
        proximity_verified = panel_status.proximity_proven
        if proximity_verified is False:
            return self.async_show_form(
                step_id="auth_proximity"
            )

        # Ensure token is valid
        self.access_token = await span_api.get_access_token()
        if not await validate_host(self.hass, self.host, self.access_token):
            return self.async_abort(reason="invalid_access_token")

        return await self.async_step_resolve_entity(entry_data)

    async def async_step_auth_token(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Step that prompts user for access token.
        """
        self.ensure_flow_is_set_up()

        if user_input is None:
            return self.async_show_form(
                step_id="auth_token", data_schema=STEP_AUTH_TOKEN_DATA_SCHEMA
            )

        # Ensure token is valid
        self.access_token = user_input[CONF_ACCESS_TOKEN]
        if not await validate_host(self.hass, self.host, self.access_token):
            return self.async_abort(reason="invalid_access_token")

        return await self.async_step_resolve_entity(user_input)

    async def async_step_resolve_entity(
        self,
        entry_data: dict[str, Any] | None = None,
    ) -> FlowResult:
        self.ensure_flow_is_set_up()

        # Continue based on flow trigger type
        match self.trigger_flow_type:
            case TriggerFlowType.CREATE_ENTRY:
                return self.create_new_entry(
                    self.host, self.serial_number, self.access_token
                )
            case TriggerFlowType.UPDATE_ENTRY:
                return self.update_existing_entry(
                    self.context["entry_id"], self.host, self.access_token, entry_data
                )
            case _:
                raise NotImplementedError()

    def create_new_entry(
        self, host: str, serial_number: str, access_token: str
    ) -> FlowResult:
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
    ) -> FlowResult:
        """
        Updates an existing entry with new configurations.
        """
        # Update the existing data with reauthed data
        entry_data[CONF_HOST] = host
        entry_data[CONF_ACCESS_TOKEN] = access_token

        # An existing entry must exist before we can update it
        entry = self.hass.config_entries.async_get_entry(entry_id)
        assert entry

        self.hass.config_entries.async_update_entry(entry, data=entry_data)
        self.hass.async_create_task(self.hass.config_entries.async_reload(entry_id))
        return self.async_abort(reason="reauth_successful")

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        curr_scan_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=curr_scan_interval
                    ): vol.All(int, vol.Range(min=5)),
                }
            ),
        )
