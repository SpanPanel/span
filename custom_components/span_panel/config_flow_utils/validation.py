"""Validation utilities for Span Panel config flow."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util.network import is_ipv4_address
from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError
from span_panel_api.phase_validation import (
    are_tabs_opposite_phase,
    get_tab_phase,
    validate_solar_tabs,
)

from custom_components.span_panel.const import (
    CONFIG_API_RETRIES,
    CONFIG_API_RETRY_BACKOFF_MULTIPLIER,
    CONFIG_API_RETRY_TIMEOUT,
    CONFIG_TIMEOUT,
    COORDINATOR,
    DOMAIN,
    ISO_DATETIME_FORMAT,
    TIME_ONLY_FORMATS,
)

_LOGGER = logging.getLogger(__name__)


async def get_available_unmapped_tabs(hass: HomeAssistant, config_entry: ConfigEntry) -> list[int]:
    """Get list of available unmapped tab numbers from panel data.

    Args:
        hass: Home Assistant instance
        config_entry: Configuration entry for this integration

    Returns:
        List of unmapped tab numbers available for solar configuration

    """
    try:
        # Get the coordinator from the integration's data
        coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
        panel_data = coordinator.data

        if not panel_data or not hasattr(panel_data, "circuits"):
            return []

        # Get all tab numbers from circuits that start with "unmapped_tab_"
        unmapped_tabs = []
        for circuit_id in panel_data.circuits:
            if circuit_id.startswith("unmapped_tab_"):
                try:
                    tab_number = int(circuit_id.replace("unmapped_tab_", ""))
                    unmapped_tabs.append(tab_number)
                except ValueError:
                    continue

        return sorted(unmapped_tabs)

    except (KeyError, AttributeError) as e:
        _LOGGER.warning("Could not get unmapped tabs from panel data: %s", e)
        return []


def validate_solar_tab_selection(
    tab1: int, tab2: int, available_tabs: list[int]
) -> tuple[bool, str]:
    """Validate solar tab selection for proper 240V configuration.

    Args:
        tab1: First selected tab number
        tab2: Second selected tab number
        available_tabs: List of available unmapped tab numbers

    Returns:
        tuple of (is_valid, error_message) where:
        - is_valid: True if selection is valid for 240V solar
        - error_message: Description of validation result or error

    """
    # Check if both tabs are provided
    if tab1 == 0 or tab2 == 0:
        return (
            False,
            "Both solar legs must be selected. Single leg configuration is not supported for proper 240V measurement.",
        )

    # Check if tabs are the same
    if tab1 == tab2:
        return (
            False,
            f"Solar legs cannot use the same tab ({tab1}). Two different tabs are required for 240V measurement.",
        )

    # Check if both tabs are available (unmapped)
    if tab1 not in available_tabs:
        return False, f"Tab {tab1} is not available or is already mapped to a circuit."

    if tab2 not in available_tabs:
        return False, f"Tab {tab2} is not available or is already mapped to a circuit."

    # Use phase validation from the API package
    is_valid, message = validate_solar_tabs(tab1, tab2, available_tabs)

    # If validation failed due to same phase, provide more detailed error
    if not is_valid and "both on" in message:
        try:
            phase1 = get_tab_phase(tab1)
            phase2 = get_tab_phase(tab2)
            return False, (
                f"Invalid selection: Tab {tab1} ({phase1}) and Tab {tab2} ({phase2}) are both on the same phase. "
                f"For proper 240V measurement, tabs must be on opposite phases (L1 + L2)."
            )
        except ValueError:
            pass

    return is_valid, message


def get_filtered_tab_options(
    selected_tab: int, available_tabs: list[int], include_none: bool = True
) -> dict[int, str]:
    """Get filtered tab options based on opposite phase requirement.

    Args:
        selected_tab: Currently selected tab (0 for none)
        available_tabs: List of all available unmapped tabs
        include_none: Whether to include "None (Disabled)" option

    Returns:
        Dictionary mapping tab numbers to display names, filtered to show only
        tabs on the opposite phase of the selected tab (or all if no tab selected)

    """
    tab_options = {}

    # Always include "None (Disabled)" option if requested
    if include_none:
        tab_options[0] = "None (Disabled)"

    # If no tab is selected (0), show all available tabs with phase info
    if selected_tab == 0:
        for tab in available_tabs:
            try:
                phase = get_tab_phase(tab)
                tab_options[tab] = f"Tab {tab} ({phase})"
            except ValueError:
                tab_options[tab] = f"Tab {tab}"
        return tab_options

    # Filter to show only tabs on the opposite phase using the API function
    for tab in available_tabs:
        if are_tabs_opposite_phase(selected_tab, tab, available_tabs):
            try:
                phase = get_tab_phase(tab)
                tab_options[tab] = f"Tab {tab} ({phase})"
            except ValueError:
                tab_options[tab] = f"Tab {tab}"

    return tab_options


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
        # Parse the time
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


def validate_solar_configuration(
    solar_enabled: bool, leg1: int, leg2: int, available_tabs: list[int]
) -> tuple[bool, str]:
    """Validate complete solar configuration.

    Args:
        solar_enabled: Whether solar is enabled
        leg1: First leg tab number
        leg2: Second leg tab number
        available_tabs: List of available unmapped tabs

    Returns:
        Tuple of (is_valid, error_message)

    """
    if not solar_enabled:
        return True, ""

    # Only validate when we actually have available tabs information
    if available_tabs:
        return validate_solar_tab_selection(leg1, leg2, available_tabs)

    return True, ""
