"""Config flow for Span Panel integration."""
from __future__ import annotations

import logging
from typing import Any

from .span_panel import SpanPanel
import httpx
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.network import is_ipv4_address

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_SERIAL = "serial"

# TODO adjust the data schema to the data that you need
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("host"): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> SpanPanel:
    """Validate the user input allows us to connect."""
    span_panel = SpanPanel(
        data[CONF_HOST],
        async_client=get_async_client(hass),
    )

    try:
        await span_panel.getStatusData()
    except (RuntimeError, httpx.HTTPError) as err:
        raise CannotConnect from err

    return span_panel


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Span Panel."""

    VERSION = 1

    def __init__(self):
        """Initialize an Span Panel flow."""
        self.host: str | None = None
        self.sn: str | None = None

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle a flow initialized by zeroconf discovery."""
        _LOGGER.debug("Zeroconf discovered: %s", discovery_info)

        if not is_ipv4_address(discovery_info.host):
            return self.async_abort(reason="not_ipv4_address")

        panel = await validate_input(self.hass, {CONF_HOST: discovery_info.host})

        self.host = discovery_info.host

        self.sn = panel.serial_number
        _LOGGER.debug("SN: %s ip %s" % (self.sn, self.host))

        await self.async_set_unique_id(self.sn)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})

        for entry in self._async_current_entries(include_ignore=False):
            _LOGGER.warning("entry loop")

        return await self.async_step_confirm_discovery()

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm a discovered powerwall."""
        assert self.host is not None
        assert self.unique_id is not None
        assert self.sn is not None
        if user_input is not None:
            return self.async_create_entry(
                title = f"Span Panel {self.sn}",
                data = {
                    CONF_HOST: self.host,
                },
            )

        self._set_confirm_only()
        self.context["title_placeholders"] = {
            CONF_SERIAL: self.sn,
            CONF_HOST: self.host,
        }
        return self.async_show_form(
            step_id="confirm_discovery",
            description_placeholders={
                "serial": self.sn,
                "host": self.host,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""

        if user_input is None:
            if self.host is None:
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_DATA_SCHEMA,
                )
            else:
                self._set_confirm_only()
                return self.async_show_form(step_id="confirm")

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info.serial_number, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
