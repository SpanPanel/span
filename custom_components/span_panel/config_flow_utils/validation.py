"""Validation utilities for Span Panel config flow."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.util.network import is_ipv4_address
from span_panel_api import V2AuthResponse, detect_api_version, register_v2

_LOGGER = logging.getLogger(__name__)


async def validate_host(
    hass: HomeAssistant,
    host: str,
    access_token: str | None = None,
    port: int = 80,
) -> bool:
    """Validate the host connection by probing the panel's status endpoint."""
    try:
        result = await detect_api_version(host, port=port)
        return result.api_version in ("v1", "v2")
    except Exception:
        return False


async def validate_auth_token(hass: HomeAssistant, host: str, access_token: str) -> bool:
    """Validate an auth token.

    For v2 panels, token validation is not applicable (passphrase auth is used).
    This function exists for backward compatibility with v1 config flow paths.
    """
    # v1 token validation is no longer supported since SpanPanelClient was removed.
    # v2 panels authenticate via passphrase → register_v2().
    _LOGGER.warning("validate_auth_token called but v1 REST validation is no longer available")
    return False


def validate_ipv4_address(host: str) -> bool:
    """Validate that the host is an IPv4 address."""
    return is_ipv4_address(host)


async def validate_v2_passphrase(host: str, passphrase: str, port: int = 80) -> V2AuthResponse:
    """Validate a v2 panel passphrase and return MQTT credentials.

    Raises:
        SpanPanelAuthError: on invalid passphrase (401/403).
        SpanPanelConnectionError: on network/timeout failures.
        SpanPanelTimeoutError: on request timeout.

    """
    return await register_v2(host, "Home Assistant", passphrase, port=port)


async def validate_v2_proximity(host: str, port: int = 80) -> V2AuthResponse:
    """Validate v2 panel proximity (door bypass) and return MQTT credentials.

    Calls register_v2 without a passphrase, which triggers door-bypass
    registration. The panel accepts this when the user opens/closes the
    door 3 times within the proximity window.

    Raises:
        SpanPanelAuthError: if proximity was not proven (door not opened).
        SpanPanelConnectionError: on network/timeout failures.
        SpanPanelTimeoutError: on request timeout.

    """
    return await register_v2(host, "Home Assistant", port=port)
