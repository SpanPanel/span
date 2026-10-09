"""Whether this release may create entities for a panel's hardware.

The verdict reads the REST `hardwareVersion` from `GET /api/v2/status` only, never
MQTT `info/hardware-version`, which is a free string an emulator may set to anything.
The values allowed are the ones the public r202639 changelog lists. A panel that
reports `UNKNOWN` is judged again once its model is known; `MAIN_32` always proceeds.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

VALIDATED_HARDWARE_VERSIONS: Final = frozenset({"1.2", "2.0"})
UNDETERMINED_HARDWARE_VERSION: Final = "UNKNOWN"
VALIDATED_MODEL: Final = "MAIN_32"


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
