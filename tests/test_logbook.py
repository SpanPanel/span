"""The logbook names the automation or script that operated a breaker.

A control command issued with no logged-in user is attributed to the automation
or script whose context caused it. Its name is the full name Home Assistant
composes from the entity registry, not the `friendly_name` attribute on its
state, which Home Assistant plans to retire.

An automation written in YAML without an `id` has no registry entry. For that
one case the name comes from `State.name`, which is Core's accessor, so Core
decides what it answers once the attribute is gone.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import Context, Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.span_panel.const import EVENT_CONTROL_COMMAND
from custom_components.span_panel.logbook import async_describe_events

CALLER_CONTEXT_ID = "01JCALLERCONTEXT0000000000"


def _described_message(hass: HomeAssistant) -> str:
    """Return the logbook sentence for a breaker opened by `CALLER_CONTEXT_ID`."""
    describers: list[Callable[[Event], dict[str, str]]] = []

    def register(
        _domain: str, _event_type: str, describer: Callable[[Event], dict[str, str]]
    ) -> None:
        describers.append(describer)

    async_describe_events(hass, register)
    (describer,) = describers
    described = describer(
        Event(
            EVENT_CONTROL_COMMAND,
            {
                "user_id": None,
                "parent_id": CALLER_CONTEXT_ID,
                "command": "circuit-1/relay",
                "value": "OPEN",
                "outcome": "confirmed",
            },
        )
    )
    return described["message"]


def _caused_by(hass: HomeAssistant, entity_id: str, attributes: dict[str, str]) -> None:
    """Write the state of the automation or script whose run issued the command."""
    hass.states.async_set(entity_id, "on", attributes, context=Context(id=CALLER_CONTEXT_ID))


@pytest.mark.parametrize(
    ("domain", "name_by_user", "expected"),
    [
        pytest.param("automation", None, "Evening Load Shed", id="automation-as-configured"),
        pytest.param("script", None, "Evening Load Shed", id="script-as-configured"),
        pytest.param("automation", "Shed The Big Loads", "Shed The Big Loads", id="renamed"),
    ],
)
async def test_logbook_names_a_registered_automation_by_its_full_name(
    hass: HomeAssistant, domain: str, name_by_user: str | None, expected: str
) -> None:
    """The name is the registry's, read without the state's `friendly_name`.

    The state carries no `friendly_name` here, as every state will once Home
    Assistant retires the attribute. `State.name` then answers the object ID
    with spaces, "evening load shed", so only the registry gives the name the
    user knows the automation by.
    """
    registry = er.async_get(hass)
    row = registry.async_get_or_create(
        domain,
        domain,
        "evening-load-shed",
        suggested_object_id="evening_load_shed",
        original_name="Evening Load Shed",
    )
    if name_by_user is not None:
        registry.async_update_entity(row.entity_id, name=name_by_user)
    _caused_by(hass, row.entity_id, {})

    assert _described_message(hass) == (
        f"circuit-1/relay was set to OPEN by {expected} and the panel confirmed it"
    )


async def test_logbook_falls_back_to_state_name_without_a_registry_entry(
    hass: HomeAssistant,
) -> None:
    """A YAML automation without an `id` is named by `State.name`, as before."""
    _caused_by(hass, "automation.night_setback", {"friendly_name": "Night Setback"})

    assert _described_message(hass) == (
        "circuit-1/relay was set to OPEN by Night Setback and the panel confirmed it"
    )


async def test_logbook_names_a_registered_automation_without_a_name_by_its_entity_id(
    hass: HomeAssistant,
) -> None:
    """A registry entry that composes to no name is named by its entity ID.

    The state's `friendly_name` is not consulted once a registry entry exists,
    so it cannot stand in for the name here.
    """
    row = er.async_get(hass).async_get_or_create(
        "automation", "automation", "nameless", suggested_object_id="nameless"
    )
    _caused_by(hass, row.entity_id, {"friendly_name": "Only On The State"})

    assert _described_message(hass) == (
        f"circuit-1/relay was set to OPEN by {row.entity_id} and the panel confirmed it"
    )
