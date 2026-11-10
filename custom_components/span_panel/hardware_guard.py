"""What the panel's hardware version decides: entity creation, and how it registers.

Everything here reads the REST `hardwareVersion` from `GET /api/v2/status` only, never
MQTT `info/hardware-version`, which is a free string an emulator may set to anything.
The values allowed are the hardware version strings this release has been validated
with. A panel that reports `UNKNOWN` is judged again once its model is known;
`MAIN_32` always proceeds.
"""

from __future__ import annotations

import asyncio
from enum import Enum, StrEnum, auto
import logging
from typing import Final, Literal

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
import httpx
from span_panel_api import get_v2_status
from span_panel_api.exceptions import SpanPanelError

from .config_flow_validation import PanelRestTransport

_LOGGER = logging.getLogger(__name__)

VALIDATED_HARDWARE_VERSIONS: Final = frozenset({"1.2", "2.0", "3.0"})
UNDETERMINED_HARDWARE_VERSION: Final = "UNKNOWN"
VALIDATED_MODEL: Final = "MAIN_32"

# Hardware version strings that register by passphrase only.
PASSPHRASE_ONLY_HARDWARE_VERSIONS: Final = frozenset({"3.0"})

HARDWARE_READ_TIMEOUT_S: Final = 5.0
"""How long a caller waits for the panel's status before going on without it."""


class _HardwareRead(Enum):
    """A status read that failed, so nothing is known about the hardware."""

    UNREAD = auto()


HARDWARE_UNREAD: Final = _HardwareRead.UNREAD
"""What `async_read_hardware_version` answers when the status cannot be read."""

type HardwareRead = str | None | Literal[_HardwareRead.UNREAD]
"""A hardware version as published, None where the firmware publishes none, or `HARDWARE_UNREAD`."""


class HardwareVerdict(StrEnum):
    """What the REST hardware version alone allows."""

    PROCEED = "proceed"
    CHECK_MODEL = "check_model"
    REFUSE = "refuse"


def hardware_verdict(hardware_version: str | None) -> HardwareVerdict:
    """Judge the REST value: absent or empty proceeds, so firmware before r202639 is never refused.

    Surrounding whitespace and the case of `UNKNOWN` are ignored, so a spelling
    the changelog did not show cannot alone make the terminal refusal.
    """
    version = (hardware_version or "").strip()
    if not version or version in VALIDATED_HARDWARE_VERSIONS:
        return HardwareVerdict.PROCEED
    if version.upper() == UNDETERMINED_HARDWARE_VERSION:
        return HardwareVerdict.CHECK_MODEL
    return HardwareVerdict.REFUSE


def refuses_entities(verdict: HardwareVerdict, model: str | None) -> bool:
    """Whether setup refuses, once the panel's model is known."""
    if verdict is HardwareVerdict.PROCEED or model == VALIDATED_MODEL:
        return False
    if not model:
        return verdict is HardwareVerdict.REFUSE
    return True


def registers_by_passphrase_only(hardware_version: HardwareRead) -> bool:
    """Whether the panel registers by passphrase only, so proximity is never offered.

    Unknown hardware answers False: offering proximity to a panel that turns out
    not to support it costs a retry, while withholding it from one that does
    could leave a user with no way back in.
    """
    return hardware_version in PASSPHRASE_ONLY_HARDWARE_VERSIONS


async def async_read_hardware_version(
    hass: HomeAssistant, host: str, transport: PanelRestTransport
) -> HardwareRead:
    """Read the panel's REST `hardwareVersion`, or `HARDWARE_UNREAD` when it cannot be had.

    Fail-open by design: a timeout, a refused or reset connection, a certificate
    the pin rejects, an unusable URL and a body that cannot be read all answer
    `HARDWARE_UNREAD`. Every caller proceeds on it as on firmware that does not
    publish the field, but setup, unlike there, leaves a standing refusal's Repair
    up: an unread status is no evidence the hardware changed. The read only ever
    refines what a caller does, so it must never be the reason a panel is locked
    out or a service fails; the caller's own path reports every one of these
    failures in its own terms.

    Over the entry's own transport -- the pinned anchor on the HTTPS port when
    it holds one -- and Home Assistant's shared client otherwise.
    """
    try:
        async with asyncio.timeout(HARDWARE_READ_TIMEOUT_S):
            status = await get_v2_status(
                host,
                port=transport.port,
                httpx_client=get_async_client(hass),
                ssl_context=transport.ssl_context,
            )
    except (TimeoutError, SpanPanelError, httpx.HTTPError, httpx.InvalidURL) as err:
        _LOGGER.debug("Could not read the hardware version of SPAN panel %s: %s", host, err)
        return HARDWARE_UNREAD
    return status.hardware_version
