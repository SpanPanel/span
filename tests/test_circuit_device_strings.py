"""The text circuit devices brought, in every language the integration ships.

Two repair notices, the reworded naming option, and where the new-entities
notification sends the user for an entity that is switched off. The English is approved
verbatim and pinned here. The sync hook holds each other language to the same
keys and nothing more, so a notice copied across in English, a placeholder
lost in translation, or a label left on a retired term passes it. These hold
each language to what the frontend needs to render the text and to the terms
the English settled on.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from string import Formatter

import pytest

from custom_components.span_panel.additions import _FALLBACK
from custom_components.span_panel.notices import read_translations

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "span_panel"

_LANGUAGE_FILES: dict[str, Path] = {
    "en": _COMPONENT / "strings.json",
    "es": _COMPONENT / "translations" / "es.json",
    "fr": _COMPONENT / "translations" / "fr.json",
    "ja": _COMPONENT / "translations" / "ja.json",
    "pt": _COMPONENT / "translations" / "pt.json",
}
"""English is read from `strings.json`, the source `translations/en.json` is generated from."""

_TRANSLATED = [language for language in _LANGUAGE_FILES if language != "en"]

_CONVERTED_TITLE = ("issues", "circuit_devices_converted", "title")
_CONVERTED = ("issues", "circuit_devices_converted", "description")
_SWITCH_TITLE = ("issues", "breaker_switch_in_area", "title")
_SWITCH = ("issues", "breaker_switch_in_area", "description")

_NAMING_HELP = (
    "config",
    "step",
    "choose_entity_naming_initial",
    "data_description",
    "entity_naming_pattern",
)
_NAMING_STEP = ("config", "step", "choose_entity_naming_initial", "description")
_CIRCUIT_NAMES = ("selector", "entity_naming_pattern", "options", "friendly_names")
_CIRCUIT_NUMBERS = ("selector", "entity_naming_pattern", "options", "circuit_numbers")

_APPROVED_ENGLISH: dict[tuple[str, ...], str] = {
    _CONVERTED_TITLE: "SPAN Panel circuits now have their own devices",
    _CONVERTED: (
        "Each circuit of {panel} is now a device of its own under the panel, named by the"
        " circuit's name in the SPAN app, and its entities are named by what they measure,"
        " such as Power. No entity ID has changed, so automations, scripts and dashboards"
        " that use entity IDs keep working."
        "\n\n**Displayed names that changed:**"
        "\n\n{renamed}"
        "\n\n**Areas moved to the circuit's device**, so a circuit's entities now share one"
        " area:"
        "\n\n{area_changes}"
        "\n\n**Circuits whose areas were left as they were**, because their breaker switch"
        " has no area. Their entities show without the circuit's name. To restore it, set an"
        " area on the circuit's device and clear the entities' own areas, knowing that the"
        " breaker switch then joins that area too, where an area-wide turn-off reaches it."
        "\n\n{circuits_left_in_areas}"
        "\n\nTemplates that read an entity's device, such as `device_name` or"
        " `device_entities`, now see the circuit's device instead of the panel. Dismiss this"
        " notice once you have read it."
    ),
    _SWITCH_TITLE: "SPAN Panel breaker switch joined an area",
    _SWITCH: (
        "{switch} is a new breaker switch on a circuit whose device is in {area}, so the"
        " switch is in {area} too. An area-wide action, such as turning off everything in"
        " {area}, now turns this breaker off. To keep it out of such actions, give the switch"
        " a different area of its own or remove the area from its circuit's device. Dismiss"
        " this notice once you have read it."
    ),
    _NAMING_HELP: (
        "Both choices build each new entity's ID once, when it is created: circuit names from"
        " the circuit's name in the SPAN app, circuit numbers from its breaker's position."
        " Displayed names follow the SPAN app either way."
    ),
    _NAMING_STEP: "Choose how the entity IDs of new circuit entities are built.",
    _CIRCUIT_NAMES: "Circuit names (e.g., span_panel_kitchen_outlets_power)",
    _CIRCUIT_NUMBERS: "Circuit numbers (e.g., span_panel_circuit_15_power)",
}

_NOTICE_TEXTS = [_CONVERTED_TITLE, _CONVERTED, _SWITCH_TITLE, _SWITCH]
_NAMING_TEXTS = [_NAMING_HELP, _NAMING_STEP, _CIRCUIT_NAMES, _CIRCUIT_NUMBERS]

_HOW_TO_ENABLE = (
    "Switched-off entities record nothing until you turn them on. Open the device page of"
    " the circuit they belong to, or the panel's for panel-wide readings, show its disabled"
    " entities, and enable the ones you want."
)
"""Approved verbatim. A notification is not a category Home Assistant translates,
so it is read from the component's own `notifications/` files, not `strings.json`."""

_CIRCUIT_WORD: dict[str, str] = {
    "es": "circuito",
    "fr": "circuit",
    "ja": "回路",
    "pt": "circuito",
}
"""Each language's word for a circuit, as its other strings spell it."""

_ENTITY_ID_WORDS: dict[str, str] = {
    "en": "entity IDs",
    "es": "ID de entidad",
    "fr": "identifiants d'entité",
    "ja": "エンティティID",
    "pt": "IDs de entidade",
}
"""How each language's other strings write "entity ID"."""

_RETIRED_NAMING_TERMS = (
    "friendly",
    "amigabl",
    "amigáve",
    "convivia",
    "フレンドリ",
    "tab-based",
    "pestaña",
    "onglet",
    "タブ",
)
"""The words the option's two choices went by before, in each language's own."""


def _strings(language: str) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(_LANGUAGE_FILES[language].read_text(encoding="utf-8"))
    return loaded


def _text(language: str, location: tuple[str, ...]) -> str:
    node: object = _strings(language)
    for key in location:
        assert isinstance(node, dict), location
        node = node[key]
    assert isinstance(node, str), location
    return node


def _every_text(node: object, location: tuple[str, ...] = ()) -> Iterator[tuple[str, str]]:
    """Yield each string in a language file, with the dotted key it is under."""
    if isinstance(node, str):
        yield ".".join(location), node
    elif isinstance(node, dict):
        for key, child in node.items():
            yield from _every_text(child, (*location, key))


def _placeholders(text: str) -> list[str]:
    """Return the placeholders a text names, in order, one entry for each use."""
    return [name for _, name, _, _ in Formatter().parse(text) if name is not None]


@pytest.mark.parametrize("location", list(_APPROVED_ENGLISH), ids=".".join)
def test_the_english_is_the_approved_text(location: tuple[str, ...]) -> None:
    assert _text("en", location) == _APPROVED_ENGLISH[location]


@pytest.mark.parametrize("language", _TRANSLATED)
@pytest.mark.parametrize("location", [*_NOTICE_TEXTS, *_NAMING_TEXTS], ids=".".join)
def test_every_language_has_its_own_text(language: str, location: tuple[str, ...]) -> None:
    """A text left in English reads as a notice the integration forgot to translate."""
    assert _text(language, location) != _APPROVED_ENGLISH[location]


@pytest.mark.parametrize("language", _TRANSLATED)
@pytest.mark.parametrize("location", _NOTICE_TEXTS, ids=".".join)
def test_every_language_names_the_placeholders_the_notice_is_raised_with(
    language: str, location: tuple[str, ...]
) -> None:
    """Each placeholder as often as the English uses it: `{area}` is named three times."""
    assert sorted(_placeholders(_text(language, location))) == sorted(
        _placeholders(_APPROVED_ENGLISH[location])
    )


@pytest.mark.parametrize("language", _TRANSLATED)
def test_the_conversion_notice_keeps_its_layout_in_every_language(language: str) -> None:
    """The lists are markdown the integration formats, so each stands as its own paragraph.

    The three headings stay bold, each in the paragraph ahead of its list, and
    the template names stay as the code they are.
    """
    english = _APPROVED_ENGLISH[_CONVERTED].split("\n\n")
    paragraphs = _text(language, _CONVERTED).split("\n\n")

    assert len(paragraphs) == len(english)
    for ours, theirs in zip(paragraphs, english, strict=True):
        if theirs.startswith("{"):
            assert ours == theirs
        assert ours.startswith("**") == theirs.startswith("**")
        assert ours.count("**") == theirs.count("**")
    assert "`device_name`" in paragraphs[-1]
    assert "`device_entities`" in paragraphs[-1]
    assert "Power" in paragraphs[0]


@pytest.mark.parametrize("language", _TRANSLATED)
def test_the_breaker_switch_notice_is_one_paragraph_in_every_language(language: str) -> None:
    text = _text(language, _SWITCH)
    assert "\n" not in text
    assert "**" not in text


@pytest.mark.parametrize("language", list(_LANGUAGE_FILES))
def test_no_text_puts_an_apostrophe_against_a_brace(language: str) -> None:
    """The frontend formats these with ICU MessageFormat, where `'{` starts quoted text.

    A French elision written `l'{area}` would print the placeholder's name in
    place of the area. A space or a typographic apostrophe in front is safe.
    """
    offenders = [
        key for key, text in _every_text(_strings(language)) if "'{" in text or "'}" in text
    ]
    assert offenders == []


@pytest.mark.parametrize("language", list(_LANGUAGE_FILES))
@pytest.mark.parametrize("location", _NAMING_TEXTS, ids=".".join)
def test_the_naming_option_no_longer_uses_the_retired_terms(
    language: str, location: tuple[str, ...]
) -> None:
    """The two choices are circuit names and circuit numbers, in every language."""
    text = _text(language, location).casefold()
    assert [term for term in _RETIRED_NAMING_TERMS if term in text] == []


@pytest.mark.parametrize("language", list(_LANGUAGE_FILES))
def test_the_naming_labels_still_show_an_entity_id(language: str) -> None:
    """Each label ends with an example, and the circuit-number one is the same everywhere."""
    assert "span_panel_circuit_15_power" in _text(language, _CIRCUIT_NUMBERS)
    assert "span_panel_" in _text(language, _CIRCUIT_NAMES)
    assert _text(language, _CIRCUIT_NAMES).endswith(("_power)", "_power）"))


def test_the_notification_sends_the_user_to_the_circuit_s_device_page() -> None:
    """A circuit's switched-off entity is on its circuit's device, not the panel's."""
    assert read_translations("en", "new_entities")["how_to_enable"] == _HOW_TO_ENABLE


def test_the_notification_s_fallback_is_the_approved_text() -> None:
    """The English kept in code, for a file that cannot be read, says the same."""
    assert _FALLBACK["how_to_enable"] == _HOW_TO_ENABLE


@pytest.mark.parametrize("language", _TRANSLATED)
def test_every_language_sends_the_user_to_the_circuit_s_device_page(language: str) -> None:
    """Each language has its own text, and none still names the panel's page alone."""
    english = read_translations("en", "new_entities")["how_to_enable"]
    text = read_translations(language, "new_entities")["how_to_enable"]

    assert text != english
    assert _placeholders(text) == []
    assert _CIRCUIT_WORD[language] in text.casefold()


@pytest.mark.parametrize("language", list(_LANGUAGE_FILES))
def test_the_naming_step_says_the_choice_builds_entity_ids(language: str) -> None:
    """The choice builds IDs; it names nothing, so no language says it names entities."""
    assert _ENTITY_ID_WORDS[language] in _text(language, _NAMING_STEP)
