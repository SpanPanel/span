from __future__ import annotations

from collections.abc import Mapping
import enum
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.network import is_ipv4_address

from .const import DOMAIN
from .span_panel_api import SpanPanelApi

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
    }
)


class FlowType(enum.Enum):
    CREATE_ENTRY = enum.auto()
    UPDATE_ENTRY = enum.auto()


def create_api_controller(hass: HomeAssistant, host: str) -> SpanPanelApi:
    return SpanPanelApi(host=host, async_client=get_async_client(hass))


async def validate_host(hass: HomeAssistant, host: str) -> bool:
    span_api = create_api_controller(hass, host)
    return await span_api.ping()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Handle a config flow for Span Panel.
    """

    VERSION = 1

    def __init__(self) -> None:
        self.trigger_flow_type: FlowType | None = None
        self.host: str | None = None

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

        self.trigger_flow_type = FlowType.CREATE_ENTRY
        self.host = discovery_info.host

        # Abort if we had already set this panel up
        span_api = create_api_controller(self.hass, self.host)
        panel_status = await span_api.get_status_data()
        await self.async_set_unique_id(panel_status.serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        Confirm a discovered Span Panel.
        """
        assert self.trigger_flow_type is not None
        assert self.host is not None

        if user_input is None:
            self._set_confirm_only()
            self.context["title_placeholders"] = {CONF_HOST: self.host}
            return self.async_show_form(
                step_id="confirm_discovery",
                description_placeholders={
                    "host": self.host,
                },
            )

        return await self.async_step_reauth_guidance(user_input)

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
        host_valid = await validate_host(self.hass, user_input[CONF_HOST])
        if host_valid is False:
            errors = {}
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
            )

        self.trigger_flow_type = FlowType.CREATE_ENTRY
        self.host = user_input[CONF_HOST]

        # Abort if we had already set this panel up
        span_api = create_api_controller(self.hass, self.host)
        panel_status = await span_api.get_status_data()
        await self.async_set_unique_id(panel_status.serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

        return await self.async_step_reauth_guidance(user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """
        Handle a flow initiated by re-auth.
        """
        self.trigger_flow_type = FlowType.UPDATE_ENTRY
        self.host = entry_data[CONF_HOST]

        return await self.async_step_reauth_guidance(entry_data)

    async def async_step_reauth_guidance(
        self,
        entry_data: dict[str, Any] | None = None,
    ) -> FlowResult:
        """
        Step that informs the user that reauth is required and guide them
        through the proximity authentication process.
        """

        # Trigger flow must be set before this function is called
        assert self.trigger_flow_type is not None

        # Host must be known before this function is called
        host = entry_data.get(CONF_HOST, self.host)
        assert host

        span_api = create_api_controller(self.hass, host)
        panel_status = await span_api.get_status_data()

        # Reprompt until we are able to do proximity auth
        remaining_presses = panel_status.remaining_auth_unlock_button_presses
        if remaining_presses != 0:
            self.context["title_placeholders"] = {CONF_HOST: host}
            return self.async_show_form(
                step_id="reauth_guidance",
                description_placeholders={"remaining": remaining_presses},
            )

        # Attempt to get the access token
        access_token = await span_api.get_access_token()

        # Continue based on flow trigger type
        match self.trigger_flow_type:
            case FlowType.CREATE_ENTRY:
                return self.create_new_entry(
                    host, panel_status.serial_number, access_token
                )
            case FlowType.UPDATE_ENTRY:
                return self.update_existing_entry(
                    self.context["entry_id"], host, access_token, entry_data
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
