"""Validation utilities for Span Panel config flow."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.core import HomeAssistant
from homeassistant.util.network import is_ipv4_address
from span_panel_api import V2AuthResponse, detect_api_version, register_v2

from custom_components.span_panel.const import (
    ISO_DATETIME_FORMAT,
    TIME_ONLY_FORMATS,
)

_LOGGER = logging.getLogger(__name__)


async def validate_host(
    hass: HomeAssistant,
    host: str,
    access_token: str | None = None,
) -> bool:
    """Validate the host connection by probing the panel's status endpoint."""
    try:
        result = await detect_api_version(host)
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


async def validate_v2_passphrase(host: str, passphrase: str) -> V2AuthResponse:
    """Validate a v2 panel passphrase and return MQTT credentials.

    Raises:
        SpanPanelAuthError: on invalid passphrase (401/403).
        SpanPanelConnectionError: on network/timeout failures.
        SpanPanelTimeoutError: on request timeout.

    """
    return await register_v2(host, "Home Assistant", passphrase)
