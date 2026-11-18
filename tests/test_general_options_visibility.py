"""The General Options form hides the settings that decide nothing, and saving it keeps what they hold.

The admin-only choice is shown only while the sidebar panel is on, and
contextless control, the lock auto-relock and the relay debounce only while
control is. With control disabled no control entity is created, the lock switch
included, so none of the three decides anything.

Core leaves a hidden field out of what the form submits, and strips it when a
client sends it anyway. The stored value survives because saving merges into
the stored options and the stored panel settings.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.span_panel.const import DOMAIN, PANEL_ADMIN_ONLY, PANEL_SHOW_SIDEBAR
from custom_components.span_panel.control_gate import ControlMode
from custom_components.span_panel.options import (
    ALLOW_CONTEXTLESS_CONTROL,
    CONTROL_LOCK_TIMEOUT,
    CONTROL_MODE,
    RELAY_DEBOUNCE_SECONDS,
)

from .helpers import seed_panel_settings, shown_values, stored_panel_settings, suggested_values

_SHOWN_WHILE_THE_SIDEBAR_PANEL_IS_ON = {
    "field": PANEL_SHOW_SIDEBAR,
    "operator": "not_eq",
    "value": False,
}
_SHOWN_WHILE_CONTROL_IS_ON = {
    "field": CONTROL_MODE,
    "operator": "not_eq",
    "value": ControlMode.DISABLED.value,
}


@pytest.fixture(autouse=True)
def panel_registration() -> Iterator[AsyncMock]:
    """Stand in for re-registering the sidebar panel, which saving the options does."""
    with patch(
        "custom_components.span_panel.async_apply_panel_registration", new_callable=AsyncMock
    ) as apply:
        yield apply


def _entry(hass: HomeAssistant, options: Mapping[str, object]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options, entry_id="entry-visibility")
    entry.add_to_hass(hass)
    return entry


async def _open(hass: HomeAssistant, entry: MockConfigEntry) -> tuple[str, dict[str, object]]:
    """Open the form, and return its flow and what saving it unchanged submits."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]
    assert schema is not None
    return result["flow_id"], shown_values(schema)


async def _save(hass: HomeAssistant, flow_id: str, submitted: dict[str, object]) -> None:
    result = await hass.config_entries.options.async_configure(flow_id, user_input=submitted)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _suggested(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, object]:
    """Open the form again, and return every value it suggests, the hidden fields' included."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    assert schema is not None
    hass.config_entries.options.async_abort(result["flow_id"])
    return suggested_values(schema)


async def test_the_frontend_is_told_when_each_field_is_shown(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """The form the frontend receives carries each condition beside the value it suggests.

    Read from Core's own HTTP view, because that is where a marker's `visible`
    is written into the serialized field. The form is built through
    `add_suggested_values_to_schema`, which copies each marker it gives a value,
    so both arriving together proves the copy kept the condition.
    """
    entry = _entry(
        hass,
        {
            CONTROL_MODE: ControlMode.ALL_USERS.value,
            ALLOW_CONTEXTLESS_CONTROL: True,
            CONTROL_LOCK_TIMEOUT: 10.0,
            RELAY_DEBOUNCE_SECONDS: 5.0,
        },
    )
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()

    response = await client.post(
        "/api/config/config_entries/options/flow", json={"handler": entry.entry_id}
    )
    assert response.status == 200
    fields = {field["name"]: field for field in (await response.json())["data_schema"]}

    assert {name: field["visible"] for name, field in fields.items() if "visible" in field} == {
        PANEL_ADMIN_ONLY: _SHOWN_WHILE_THE_SIDEBAR_PANEL_IS_ON,
        ALLOW_CONTEXTLESS_CONTROL: _SHOWN_WHILE_CONTROL_IS_ON,
        CONTROL_LOCK_TIMEOUT: _SHOWN_WHILE_CONTROL_IS_ON,
        RELAY_DEBOUNCE_SECONDS: _SHOWN_WHILE_CONTROL_IS_ON,
    }
    assert fields[PANEL_SHOW_SIDEBAR]["description"] == {"suggested_value": True}
    assert fields[PANEL_ADMIN_ONLY]["description"] == {"suggested_value": False}
    assert fields[CONTROL_MODE]["description"] == {"suggested_value": ControlMode.ALL_USERS.value}
    assert fields[ALLOW_CONTEXTLESS_CONTROL]["description"] == {"suggested_value": True}
    assert fields[CONTROL_LOCK_TIMEOUT]["description"] == {"suggested_value": 10.0}
    assert fields[RELAY_DEBOUNCE_SECONDS]["description"] == {"suggested_value": 5.0}


@pytest.mark.parametrize(
    "sent_for_the_hidden_fields",
    [
        pytest.param({}, id="left_out_as_the_frontend_does"),
        pytest.param(
            {
                ALLOW_CONTEXTLESS_CONTROL: False,
                CONTROL_LOCK_TIMEOUT: -1.0,
                RELAY_DEBOUNCE_SECONDS: 0.0,
            },
            id="sent_anyway_by_a_client",
        ),
    ],
)
async def test_disabling_control_keeps_the_settings_it_hides(
    hass: HomeAssistant, sent_for_the_hidden_fields: dict[str, object]
) -> None:
    """Switching control off saves the mode and keeps every control setting as stored.

    Contextless control, the lock auto-relock and the debounce, so turning
    control back on finds the lock switch, and the policy, as they were.
    """
    entry = _entry(
        hass,
        {
            CONTROL_MODE: ControlMode.ALL_USERS.value,
            ALLOW_CONTEXTLESS_CONTROL: True,
            CONTROL_LOCK_TIMEOUT: 10.0,
            RELAY_DEBOUNCE_SECONDS: 5.0,
        },
    )
    flow_id, submitted = await _open(hass, entry)
    submitted[CONTROL_MODE] = ControlMode.DISABLED.value
    for hidden in (ALLOW_CONTEXTLESS_CONTROL, CONTROL_LOCK_TIMEOUT, RELAY_DEBOUNCE_SECONDS):
        del submitted[hidden]
    submitted |= sent_for_the_hidden_fields

    await _save(hass, flow_id, submitted)

    assert entry.options[CONTROL_MODE] == ControlMode.DISABLED.value
    assert entry.options[ALLOW_CONTEXTLESS_CONTROL] is True
    assert entry.options[CONTROL_LOCK_TIMEOUT] == 10.0
    assert entry.options[RELAY_DEBOUNCE_SECONDS] == 5.0


async def test_saving_a_disabled_entry_unchanged_keeps_its_control_settings(
    hass: HomeAssistant,
) -> None:
    """A form opened with control off hides every control setting, and saving it leaves them as stored."""
    entry = _entry(
        hass,
        {
            CONTROL_MODE: ControlMode.DISABLED.value,
            ALLOW_CONTEXTLESS_CONTROL: True,
            CONTROL_LOCK_TIMEOUT: 10.0,
            RELAY_DEBOUNCE_SECONDS: 5.0,
        },
    )
    flow_id, submitted = await _open(hass, entry)
    assert ALLOW_CONTEXTLESS_CONTROL not in submitted
    assert CONTROL_LOCK_TIMEOUT not in submitted
    assert RELAY_DEBOUNCE_SECONDS not in submitted

    await _save(hass, flow_id, submitted)

    assert entry.options[ALLOW_CONTEXTLESS_CONTROL] is True
    assert entry.options[CONTROL_LOCK_TIMEOUT] == 10.0
    assert entry.options[RELAY_DEBOUNCE_SECONDS] == 5.0
    suggested = await _suggested(hass, entry)
    assert suggested[ALLOW_CONTEXTLESS_CONTROL] is True
    assert suggested[CONTROL_LOCK_TIMEOUT] == 10.0
    assert suggested[RELAY_DEBOUNCE_SECONDS] == 5.0


@pytest.mark.parametrize(
    "sent_for_the_hidden_field",
    [
        pytest.param({}, id="left_out_as_the_frontend_does"),
        pytest.param({PANEL_ADMIN_ONLY: False}, id="sent_anyway_by_a_client"),
    ],
)
async def test_hiding_the_sidebar_panel_keeps_the_admin_only_choice(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    sent_for_the_hidden_field: dict[str, object],
) -> None:
    """Turning the sidebar panel off saves that and keeps the stored admin-only choice.

    Turning the panel back on then suggests the choice the user had made.
    """
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: True, PANEL_ADMIN_ONLY: True})
    entry = _entry(hass, {})
    flow_id, submitted = await _open(hass, entry)
    submitted[PANEL_SHOW_SIDEBAR] = False
    del submitted[PANEL_ADMIN_ONLY]
    submitted |= sent_for_the_hidden_field

    await _save(hass, flow_id, submitted)

    assert stored_panel_settings(hass_storage) == {
        PANEL_SHOW_SIDEBAR: False,
        PANEL_ADMIN_ONLY: True,
    }
    assert (await _suggested(hass, entry))[PANEL_ADMIN_ONLY] is True


async def test_with_every_field_shown_each_change_is_saved(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The conditions hide nothing while the sidebar panel and control are both on."""
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: True, PANEL_ADMIN_ONLY: False})
    entry = _entry(
        hass,
        {
            CONTROL_MODE: ControlMode.ADMIN_ONLY.value,
            ALLOW_CONTEXTLESS_CONTROL: False,
            CONTROL_LOCK_TIMEOUT: -1.0,
            RELAY_DEBOUNCE_SECONDS: 2.0,
        },
    )
    flow_id, submitted = await _open(hass, entry)
    submitted |= {
        PANEL_ADMIN_ONLY: True,
        CONTROL_MODE: ControlMode.ALL_USERS.value,
        ALLOW_CONTEXTLESS_CONTROL: True,
        CONTROL_LOCK_TIMEOUT: 15.0,
        RELAY_DEBOUNCE_SECONDS: 4.0,
    }

    await _save(hass, flow_id, submitted)

    assert stored_panel_settings(hass_storage) == {
        PANEL_SHOW_SIDEBAR: True,
        PANEL_ADMIN_ONLY: True,
    }
    assert entry.options[CONTROL_MODE] == ControlMode.ALL_USERS.value
    assert entry.options[ALLOW_CONTEXTLESS_CONTROL] is True
    assert entry.options[CONTROL_LOCK_TIMEOUT] == 15.0
    assert entry.options[RELAY_DEBOUNCE_SECONDS] == 4.0


async def test_a_submission_without_the_control_mode_saves_the_control_settings(
    hass: HomeAssistant,
) -> None:
    """Core resolves visibility from what is submitted, so a mode left out hides nothing.

    The mode keeps its stored value, and the settings it would have hidden are
    saved as sent, as they were before the form had conditions. Under the stored
    `disabled` they decide nothing until control is turned back on.
    """
    entry = _entry(
        hass,
        {
            CONTROL_MODE: ControlMode.DISABLED.value,
            ALLOW_CONTEXTLESS_CONTROL: False,
            CONTROL_LOCK_TIMEOUT: -1.0,
            RELAY_DEBOUNCE_SECONDS: 2.0,
        },
    )
    flow_id, _ = await _open(hass, entry)

    await _save(
        hass,
        flow_id,
        {ALLOW_CONTEXTLESS_CONTROL: True, CONTROL_LOCK_TIMEOUT: 5.0, RELAY_DEBOUNCE_SECONDS: 4.0},
    )

    assert entry.options[CONTROL_MODE] == ControlMode.DISABLED.value
    assert entry.options[ALLOW_CONTEXTLESS_CONTROL] is True
    assert entry.options[CONTROL_LOCK_TIMEOUT] == 5.0
    assert entry.options[RELAY_DEBOUNCE_SECONDS] == 4.0


async def test_a_submission_without_the_sidebar_setting_saves_the_admin_only_choice(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The admin-only choice is hidden only by a submitted `False`, not by an absent sidebar setting.

    The same rule as the control settings: what a client sends is saved, and the
    sidebar setting keeps its stored value.
    """
    seed_panel_settings(hass_storage, {PANEL_SHOW_SIDEBAR: False, PANEL_ADMIN_ONLY: False})
    entry = _entry(hass, {})
    flow_id, _ = await _open(hass, entry)

    await _save(hass, flow_id, {PANEL_ADMIN_ONLY: True})

    assert stored_panel_settings(hass_storage) == {
        PANEL_SHOW_SIDEBAR: False,
        PANEL_ADMIN_ONLY: True,
    }
