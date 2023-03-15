from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.network import is_ipv4_address

from .const import DOMAIN
from .span_panel_api import SpanPanelApi

_LOGGER = logging.getLogger(__name__)

CONF_SERIAL = "serial"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
    }
)


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
        self.host: str | None = None
        self.serial_number: str | None = None

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """
        Handle a flow initialized by zeroconf discovery.
        """
        _LOGGER.debug("Zeroconf discovered: %s", discovery_info)

        if not is_ipv4_address(discovery_info.host):
            return self.async_abort(reason="not_ipv4_address")

        # Do not probe the device if the host is already configured
        self._async_abort_entries_match({CONF_HOST: self.host})

        if not await validate_host(self.hass, discovery_info.host):
            return self.async_abort(reason="not_span_panel")

        self.host = discovery_info.host

        span_api = create_api_controller(self.hass, self.host)
        panel_status = await span_api.get_status_data()
        self.serial_number = panel_status.serial_number

        _LOGGER.debug("SN: %s ip %s", self.serial_number, self.host)

        await self.async_set_unique_id(self.serial_number)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

        for _ in self._async_current_entries(include_ignore=False):
            _LOGGER.debug("entry loop")

        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        Confirm a discovered Span Panel.
        """
        assert self.host is not None
        assert self.unique_id is not None
        assert self.serial_number is not None

        if user_input is not None:
            return self.async_create_entry(
                title=f"Span Panel {self.serial_number}",
                data={CONF_HOST: self.host},
            )

        self._set_confirm_only()
        self.context["title_placeholders"] = {
            CONF_SERIAL: self.serial_number,
            CONF_HOST: self.host,
        }
        return self.async_show_form(
            step_id="confirm_discovery",
            description_placeholders={
                "serial": self.serial_number,
                "host": self.host,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        Handle the initial step.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        host_valid = await validate_host(self.hass, user_input[CONF_HOST])
        if host_valid is False:
            errors = {}
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
            )

        span_api = create_api_controller(self.hass, user_input[CONF_HOST])
        panel_status = await span_api.get_status_data()

        self.host = user_input[CONF_HOST]
        self.serial_number = panel_status.serial_number

        remaining_presses = panel_status.remaining_auth_unlock_button_presses
        if remaining_presses != 0:
            return self.async_show_form(
                step_id="user",
                description_placeholders={"remaining": remaining_presses},
            )

        access_token = await span_api.get_access_token()
        user_input[CONF_ACCESS_TOKEN] = access_token
        return self.async_create_entry(
            title=panel_status.serial_number, data=user_input
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """
        Handle configuration by re-auth.
        """
        return await self.async_step_reauth_confirm(entry_data)

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """
        Dialog that informs the user that reauth is required.
        """
        host = user_input.get(CONF_HOST, self.host)
        assert host
        span_api = create_api_controller(self.hass, host)
        panel_status = await span_api.get_status_data()

        self.context["title_placeholders"] = {
            CONF_SERIAL: panel_status.serial_number,
            CONF_HOST: host,
        }

        remaining_presses = panel_status.remaining_auth_unlock_button_presses
        if remaining_presses != 0:
            return self.async_show_form(
                step_id="reauth_confirm",
                description_placeholders={"remaining": remaining_presses},
            )

        access_token = span_api.get_access_token()
        user_input[CONF_HOST] = host
        user_input[CONF_ACCESS_TOKEN] = access_token

        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry
        self.hass.config_entries.async_update_entry(entry, data=user_input)
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self.context["entry_id"])
        )
        return self.async_abort(reason="reauth_successful")
