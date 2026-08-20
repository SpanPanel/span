"""An entity this integration adds must not arrive silently, switched on or off.

`battery.part_number` shipped with `entity_registry_enabled_default=False` so
upgrading would not grow anybody's entity list uninvited. It worked, and the cost
was that nothing told the user the sensor existed -- they found it by opening the
device's disabled-entity list on a hunch.

The first fix covered only *disabled* additions, on the reasoning that an enabled
one is already visible in the entity list and its history. That reasoning does
not survive contact with how anyone uses Home Assistant: nobody watches their
entity count, so an addition that breaks nothing is indistinguishable from no
addition at all. These cover both, and the four ways the announcement could be
worse than nothing -- shouting on a first install, shouting a release's worth of
history on upgrade, nagging on every restart, or naming a hundred entities at
once.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.additions import (
    _read_translations,
    async_announce_new_entities,
    async_forget_announcements,
)
from custom_components.span_panel.const import DOMAIN

_PART_NUMBER = "sp3-001_bess_part_number"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry in hass. No conftest fixture exists for this."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-001")
    mock.add_to_hass(hass)
    return mock


def _register(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    unique_id: str,
    *,
    disabled: bool = True,
    name: str | None = None,
    device_id: str | None = None,
) -> er.RegistryEntry:
    """Register one entity the way a platform would."""
    return er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        original_name=name,
        device_id=device_id,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION if disabled else None,
    )


def _notifications(hass: HomeAssistant) -> dict[str, Any]:
    """Every persistent notification currently standing, by id."""
    return dict(hass.data.get("persistent_notification", {}))


def _announcement(hass: HomeAssistant, entry: MockConfigEntry) -> Any | None:
    return _notifications(hass).get(f"{DOMAIN}_new_entities_{entry.entry_id}")


def _text(notification: Any) -> str:
    message = notification.message if hasattr(notification, "message") else notification["message"]
    return str(message)


# -- Silence where silence is right ------------------------------------------


async def test_a_first_install_announces_nothing(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Every entity is new on a first install, so the notice would name them all.

    Which would teach the user that this category is noise, and cost them the
    real additions later.
    """
    _register(hass, entry, _PART_NUMBER, name="Part Number")

    await async_announce_new_entities(hass, entry)

    assert _announcement(hass, entry) is None


async def test_an_install_that_predates_the_record_announces_nothing_once(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Entities that were never announced are not therefore new.

    The first pass adopts what is already registered as known. Without it, the
    release that ships this mechanism would announce every entity the integration
    has ever created.
    """
    for index in range(5):
        _register(hass, entry, f"sp3-001_existing_{index}", name=f"Existing {index}")

    await async_announce_new_entities(hass, entry)

    assert _announcement(hass, entry) is None


async def test_a_restart_that_adds_nothing_announces_nothing(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The failure that would make the whole thing worse than useless."""
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    await async_announce_new_entities(hass, entry)

    for _ in range(3):
        await async_announce_new_entities(hass, entry)

    assert _announcement(hass, entry) is None


# -- What it says ------------------------------------------------------------


async def test_a_disabled_addition_is_announced_and_says_it_needs_enabling(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    _register(hass, entry, _PART_NUMBER, name="Part Number")
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Part Number" in message
    assert "Added but switched off" in message
    assert "enable the ones you want" in message


async def test_an_enabled_addition_is_announced_too(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The gap this replaced the Repair to close.

    An enabled entity is visible in the entity list and starts recording, which is
    only an announcement to somebody already looking at the entity list.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    _register(hass, entry, "sp3-001_grid_state", name="Grid State", disabled=False)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Grid State" in message
    assert "Added and ready to use" in message


async def test_both_kinds_are_split_rather_than_pooled(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The split is the actionable part: one kind needs an action, the other does not."""
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    _register(hass, entry, "sp3-001_on", name="Switched On", disabled=False)
    _register(hass, entry, "sp3-001_off", name="Switched Off")
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert message.index("Added and ready to use") < message.index("Switched On")
    assert message.index("Added but switched off") < message.index("Switched Off")


async def test_every_added_entity_is_named_rather_than_sampled(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """"What exactly was added" means all of it.

    The Repair this replaced showed a count plus three examples, which tells a
    user that something happened and not what.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    names = [f"Reading {index}" for index in range(8)]
    for index, name in enumerate(names):
        _register(hass, entry, f"sp3-001_new_{index}", name=name)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert all(name in message for name in names)


# -- Adopted devices are counted, not listed ---------------------------------


async def test_an_adopted_device_contributes_one_line_with_a_count(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A vendor device declaring a dozen properties must not consume the message.

    Listing them would spend the whole notification on one device and teach the
    user to skip it -- costing them the curated additions in the same message.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-001_adopted_generator-1")},
        name="Backup Generator",
    )
    for index in range(6):
        _register(hass, entry, f"sp3-001_adopted_{index}", name=f"Adopted {index}", device_id=device.id)
    _register(hass, entry, "sp3-001_curated", name="Curated Addition")
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Backup Generator (6 entities)" in message
    assert "Adopted 0" not in message
    assert "Curated Addition" in message


# -- Translations ------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "es", "fr", "ja", "pt"])
def test_every_shipped_locale_carries_the_notification_strings(language: str) -> None:
    """The notification is assembled here, so nothing else checks these keys.

    Home Assistant's translation helper filters to the categories it defines and a
    persistent notification is not one of them, so these are read from this
    component's own files. That is precisely why a missing key would fail silently
    into English rather than being caught by the platform.
    """
    text = _read_translations(language)
    assert set(text) >= {
        "title",
        "intro_one",
        "intro_many",
        "enabled_heading",
        "disabled_heading",
        "how_to_enable",
        "nothing_broken",
    }


def test_an_unknown_language_falls_back_to_english_rather_than_to_nothing() -> None:
    assert _read_translations("xx")["title"] == _read_translations("en")["title"]


def test_a_regional_language_resolves_to_its_base(hass: HomeAssistant) -> None:
    """`pt-BR` is not shipped; `pt` is, and is a better answer than English."""
    assert _read_translations("pt-BR") == _read_translations("pt")


# -- Removal -----------------------------------------------------------------


async def test_removing_the_entry_forgets_what_was_announced(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Otherwise re-adding the same panel announces none of the entities it recreates."""
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    await async_announce_new_entities(hass, entry)

    await async_forget_announcements(hass, entry)
    _register(hass, entry, "sp3-001_new", name="New One")
    await async_announce_new_entities(hass, entry)

    assert _announcement(hass, entry) is None
