"""Every enum sensor must declare the states it can actually report.

Home Assistant renders `options` as the entity's "Possible states" and validates the
state against it. Eight sensors declared `["unknown"]` and nothing else, so a panel
reporting `dsm_on_grid` showed a live state that its own entity said was impossible.

`sensor_base` tried to fix this at runtime, appending each value as it was first
observed. That does not work, and could not: options would only ever list states the
panel had already reached, so `dsm_off_grid` stays absent until the day of an actual
outage, and the advertised set differs between two identical panels depending on what
each has lived through. A value domain that is known when the code is written should
be declared there.

The translations are the authority rather than a second hand-maintained list: a state
with no translation renders to a user as a raw key like `dsm_on_grid`, and an option
with no state to match is dead weight. Both are the same defect seen from either end,
so one list is derived from the other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.span_panel.sensor_definitions import (
    BESS_METADATA_SENSORS,
    CIRCUIT_SENSORS,
    EVSE_SENSORS,
    MID_SENSORS,
    PANEL_DATA_STATUS_SENSORS,
    PV_METADATA_SENSORS,
    STATUS_SENSORS,
    UNMAPPED_SENSORS,
)

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"


def _translated_states() -> dict[str, set[str]]:
    """The state keys `en.json` renders, per sensor translation key."""
    entities = json.loads((_COMPONENT / "translations" / "en.json").read_text())
    sensors = entities["entity"]["sensor"]
    return {key: set(body.get("state", {})) for key, body in sensors.items()}


def _enum_descriptions() -> list[Any]:
    # Every published group, so a new enum in any of them is covered the day it
    # lands rather than the day someone remembers this file.
    groups = (
        PANEL_DATA_STATUS_SENSORS,
        STATUS_SENSORS,
        UNMAPPED_SENSORS,
        MID_SENSORS,
        BESS_METADATA_SENSORS,
        PV_METADATA_SENSORS,
        CIRCUIT_SENSORS,
        EVSE_SENSORS,
    )
    return [
        description
        for group in groups
        for description in group
        if description.device_class is SensorDeviceClass.ENUM
    ]


def test_there_are_enum_sensors_to_check() -> None:
    """Guard the guard.

    Every assertion below is a loop over discovered descriptions, so an import that
    silently stopped finding any would leave this file passing while checking nothing.
    """
    assert len(_enum_descriptions()) >= 8


@pytest.mark.parametrize("description", _enum_descriptions(), ids=lambda d: str(d.key))
def test_declared_options_match_the_states_the_ui_can_render(description: Any) -> None:
    """Declared options and translated states are the same set.

    Not a subset in either direction. An option with no translation reaches a user as
    a raw key; a translated state absent from options is one Home Assistant will
    reject when the panel reports it -- which is how `dsm_grid_state` came to show
    "Possible states: Unknown" while sitting at `dsm_on_grid`.
    """
    translated = _translated_states().get(description.translation_key)
    assert translated, f"{description.key} has no translated states to compare against"

    declared = set(description.options or [])

    assert declared == translated, (
        f"{description.key}: options {sorted(declared)} do not match the states "
        f"en.json renders {sorted(translated)}. Add the missing ones to whichever "
        "side is short; the two lists describe the same thing."
    )


@pytest.mark.parametrize("description", _enum_descriptions(), ids=lambda d: str(d.key))
def test_unknown_is_offered_as_a_fallback(description: Any) -> None:
    """Every enum can be unknown, so every enum must say so.

    The value functions fall back to `"unknown"` when the panel publishes nothing, and
    Home Assistant rejects a state outside `options`. A set that omits it is one
    missing reading away from an error.
    """
    assert "unknown" in (description.options or []), (
        f"{description.key} can report 'unknown' but does not declare it"
    )
