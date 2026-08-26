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

from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_panel.additions import (
    _ANNOUNCED,
    _SECTION,
    _STORE_VERSION,
    COLLAPSE_ABOVE,
    async_announce_new_entities,
    async_forget_announcements,
)
from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.notices import read_translations

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


async def test_a_first_install_announces_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
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


async def test_a_restart_that_adds_nothing_announces_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
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


async def test_an_enabled_addition_is_announced_too(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
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


async def test_both_kinds_are_split_rather_than_pooled(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The split is the actionable part: one kind needs an action, the other does not."""
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    _register(hass, entry, "sp3-001_on", name="Switched On", disabled=False)
    _register(hass, entry, "sp3-001_off", name="Switched Off")
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert message.index("Added and ready to use") < message.index("Switched On")
    assert message.index("Added but switched off") < message.index("Switched Off")


async def test_every_added_entity_is_named_rather_than_sampled(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Naming what was added means naming all of it.

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
        _register(
            hass, entry, f"sp3-001_adopted_{index}", name=f"Adopted {index}", device_id=device.id
        )
    _register(hass, entry, "sp3-001_curated", name="Curated Addition")
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Backup Generator (6 entities)" in message
    assert "Adopted 0" not in message
    assert "Curated Addition" in message


async def test_an_adopted_device_is_listed_as_switched_off(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Every adopted entity registers disabled, so it must not read as ready to use.

    `AdoptedEntity` sets `_attr_entity_registry_enabled_default = False` for all of
    them. Listing them beside the enabled additions said the opposite of the truth.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-001_adopted_generator-1")},
        name="Backup Generator",
    )
    _register(hass, entry, "sp3-001_adopted_0", name="Adopted 0", device_id=device.id)
    _register(hass, entry, "sp3-001_ready", name="Ready Sensor", disabled=False)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    enabled_at = message.index("Added and ready to use")
    disabled_at = message.index("Added but switched off")
    assert enabled_at < message.index("Ready Sensor") < disabled_at
    assert disabled_at < message.index("Backup Generator (1 entities)")


async def test_an_adopted_only_release_still_says_how_to_switch_them_on(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The case adoption exists for was the case that told the user least.

    A vendor device appearing is often the *only* addition in a release. Counting
    adopted entities apart from the disabled ones meant no heading rendered and,
    worse, `how_to_enable` was suppressed -- the one string in the message with an
    action attached.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-001_adopted_generator-1")},
        name="Backup Generator",
    )
    _register(hass, entry, "sp3-001_adopted_0", name="Adopted 0", device_id=device.id)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Added but switched off" in message
    assert "show its disabled entities" in message
    assert "Added and ready to use" not in message


async def test_an_announced_entity_is_not_announced_again_after_a_restart(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The record must grow by what was registered, not be replaced by what it held.

    Recording only the previously-announced set would leave every entity announced
    in this pass still absent from the record, so the next startup would find them
    new again -- and every startup after that, forever. The nothing-added path
    returns before the record is written, so it cannot catch this.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    _register(hass, entry, _PART_NUMBER, name="Part Number")
    await async_announce_new_entities(hass, entry)
    assert _announcement(hass, entry) is not None

    async_dismiss(hass, f"{DOMAIN}_new_entities_{entry.entry_id}")
    await async_announce_new_entities(hass, entry)

    assert _announcement(hass, entry) is None


# -- Translations ------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "es", "fr", "ja", "pt"])
def test_every_shipped_locale_carries_the_notification_strings(language: str) -> None:
    """The notification is assembled here, so nothing else checks these keys.

    Home Assistant's translation helper filters to the categories it defines and a
    persistent notification is not one of them, so these are read from this
    component's own files. That is precisely why a missing key would fail silently
    into English rather than being caught by the platform.
    """
    text = read_translations(language, _SECTION)
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
    assert read_translations("xx", _SECTION)["title"] == read_translations("en", _SECTION)["title"]


def test_a_regional_language_resolves_to_its_base(hass: HomeAssistant) -> None:
    """`pt-BR` is not shipped; `pt` is, and is a better answer than English."""
    assert read_translations("pt-BR", _SECTION) == read_translations("pt", _SECTION)


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


# -- Sub-device entities are told apart --------------------------------------


def _sub_device(hass: HomeAssistant, entry: MockConfigEntry, name: str, ident: str) -> str:
    """Return a device hanging off the panel, the way every SPAN sub-device does."""
    devices = dr.async_get(hass)
    panel = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "sp3-001")}, name="SPAN Panel"
    )
    child = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, ident)},
        name=name,
        via_device_id=panel.id,
    )
    return str(child.id)


async def test_two_sub_devices_with_one_entity_name_are_told_apart(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The defect this fixes, in the shape it actually shipped in.

    Two commissioned chargers each gain a charge-current limit. Every platform
    sets `_attr_has_entity_name`, so the registry stores only "Charge Current
    Limit" for both and Home Assistant prepends the device in its own UI. A flat
    list does not, so the notification showed the same row twice with nothing to
    tell them apart -- which is what teaches somebody to skip the category.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    first = _sub_device(hass, entry, "SPAN Drive 1", "sp3-001_evse_1")
    second = _sub_device(hass, entry, "SPAN Drive 2", "sp3-001_evse_2")
    _register(hass, entry, "sp3-001_evse_1_limit", name="Charge Current Limit", device_id=first)
    _register(hass, entry, "sp3-001_evse_2_limit", name="Charge Current Limit", device_id=second)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "SPAN Drive 1 Charge Current Limit" in message
    assert "SPAN Drive 2 Charge Current Limit" in message
    assert message.count("Charge Current Limit") == 2


async def test_a_panel_entity_is_not_prefixed(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The panel needs no prefix, and adding one would be noise.

    A notification that already says which panel it is about does not need the
    panel's name on every line. The prefix exists to break collisions, and the
    panel is the one device that cannot collide with a sibling.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    devices = dr.async_get(hass)
    panel = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "sp3-001")}, name="SPAN Panel"
    )
    _register(hass, entry, "sp3-001_dsm", name="DSM State", device_id=panel.id)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "DSM State" in message
    assert "SPAN Panel DSM State" not in message


async def test_vendor_extensions_on_a_curated_device_collapse_to_one_line(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Fifteen new vendor properties are one line, not fifteen.

    These sit on a *curated* card, so the adopted-device detector cannot see
    them: the card is the battery's, shared with curated entities that must
    still be listed individually. Their unique_id is what says what they are.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    battery = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-001_bess")},
        name="Span Panel Battery",
    )
    for index in range(15):
        _register(
            hass,
            entry,
            f"span_sp3-001_adopted_bess/battery-2/reading-{index}",
            name=f"Battery 2 Reading {index}",
            device_id=battery.id,
        )
    # A curated entity added to the same device in the same release is still
    # named: collapsing is for the vendor surface, not for the card.
    _register(hass, entry, "sp3-001_bess_meter_power", name="Meter Power", device_id=battery.id)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Span Panel Battery (15 entities)" in message
    assert "Battery 2 Reading 0" not in message
    assert "Meter Power" in message


async def test_a_few_vendor_extensions_are_named_rather_than_collapsed(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Collapsing two tells the reader less than naming them.

    The collapse exists for a firmware update adding fifteen properties at once.
    An adopted *device*'s line at least names a device that did not exist
    before; a curated card is one the user already has, so its name alone says
    nothing about what appeared on it -- which is what a live install showed,
    reading "Span Panel (2 entities)" for a postal code and a time zone.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-001")},
        name="Span Panel",
    )
    for wire_property, label in (
        ("postal-code", "Status Postal Code"),
        ("time-zone", "Status Time Zone"),
    ):
        _register(
            hass,
            entry,
            f"span_sp3-001_adopted_panel/status/{wire_property}",
            name=label,
            device_id=panel.id,
        )
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Status Postal Code" in message
    assert "Status Time Zone" in message
    assert "(2 entities)" not in message


async def test_at_the_threshold_they_are_still_named(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Exactly the threshold is named, so the boundary is asserted from both sides."""
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-001")},
        name="Span Panel",
    )
    for index in range(COLLAPSE_ABOVE):
        _register(
            hass,
            entry,
            f"span_sp3-001_adopted_panel/acme/reading-{index}",
            name=f"Acme Reading {index}",
            device_id=panel.id,
        )
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Acme Reading 0" in message
    assert "entities)" not in message


async def test_one_past_the_threshold_collapses(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """One more than the threshold, in the same update, and the names give way to a count.

    Counted per notification rather than per device lifetime, because the
    message describes this update: five readings announced last month and one
    today is a one-line update, not a flood.
    """
    _register(hass, entry, "sp3-001_existing", name="Existing")
    await async_announce_new_entities(hass, entry)

    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "sp3-001")},
        name="Span Panel",
    )
    for index in range(COLLAPSE_ABOVE + 1):
        _register(
            hass,
            entry,
            f"span_sp3-001_adopted_panel/acme/reading-{index}",
            name=f"Acme Reading {index}",
            device_id=panel.id,
        )
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert f"Span Panel ({COLLAPSE_ABOVE + 1} entities)" in message
    assert "Acme Reading 0" not in message


# -- A store file that is not what we wrote ------------------------------------
#
# `notices` narrows its own store for this reason and has three tests for it;
# this one had none. The consequence here is worse, because this coroutine is
# awaited from inside `async_setup_entry`: a wrong-shaped file raised out of it,
# the coordinator was shut down, and the entry failed with no retry -- so a panel
# stayed dead until somebody found and deleted a file about *notifications*.


def _seed(hass_storage: dict[str, Any], entry: MockConfigEntry, data: object) -> None:
    key = f"{DOMAIN}.announced.{entry.entry_id}"
    hass_storage[key] = {"version": _STORE_VERSION, "key": key, "data": data}


@pytest.mark.parametrize(
    ("shape", "description"),
    [
        ({_ANNOUNCED: 7}, "a number where the list should be"),
        ({_ANNOUNCED: {"a": 1}}, "a mapping where the list should be"),
        ({_ANNOUNCED: ["fine", 3]}, "a list holding something that is not an id"),
        ({_ANNOUNCED: None}, "an explicit null"),
        ({}, "an object with no announced key"),
        (["announced_unique_ids"], "a list at the top level"),
        ("nonsense", "a bare string"),
    ],
)
async def test_a_malformed_store_does_not_take_the_integration_down(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    entry: MockConfigEntry,
    shape: object,
    description: str,
) -> None:
    """Setup must survive every one of these; the record is recoverable, the entry is not."""
    _seed(hass_storage, entry, shape)
    _register(hass, entry, _PART_NUMBER, name="Part Number")

    await async_announce_new_entities(hass, entry)

    assert _announcement(hass, entry) is None


async def test_a_malformed_store_is_re_seeded_rather_than_left_broken(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    """Self-healing is what makes re-seeding the right trade.

    The bad file is replaced by a valid one holding what is registered now, so
    the *next* addition is announced normally and the user never has to find the
    file. The cost is one pass of silence, which is the same trade a first
    install already makes.
    """
    _seed(hass_storage, entry, {_ANNOUNCED: 7})
    _register(hass, entry, _PART_NUMBER, name="Part Number")
    await async_announce_new_entities(hass, entry)

    _register(hass, entry, "sp3-001_grid_state", name="Grid State", disabled=False)
    await async_announce_new_entities(hass, entry)

    message = _text(_announcement(hass, entry))
    assert "Grid State" in message
    assert "Part Number" not in message
