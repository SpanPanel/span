"""Tests for the manager that stores monitoring ratings and announces changes."""

from __future__ import annotations

from collections.abc import Mapping
from string import Formatter
import threading
from typing import get_args
from unittest.mock import patch

from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from span_panel_api import FieldMetadata, SpanPanelSnapshot

from custom_components.span_panel import async_remove_entry
from custom_components.span_panel.const import DOMAIN, EVENT_MONITORING_RATING_CHANGED
from custom_components.span_panel.monitoring_rating_manager import MonitoringRatingManager
from custom_components.span_panel.monitoring_ratings import ChangeReason
from custom_components.span_panel.notices import async_restore, is_standing, read_translations

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

_NOTICE = "monitoring_rating_changed"
SERVICE_A = 100


def _store_key(entry: MockConfigEntry) -> str:
    return f"span_panel_monitoring_ratings.{entry.entry_id}"


def _notification_id(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}_{_NOTICE}_{entry.entry_id}"


def _snapshot(
    ratings: Mapping[str, float | None] | None = None,
    main: int | None = SERVICE_A,
    declared: Mapping[str, bool | None] | None = None,
) -> SpanPanelSnapshot:
    ratings = {"circuit-2": 20.0} if ratings is None else ratings
    declared = declared or {}
    return SpanPanelSnapshotFactory.create(
        circuits={
            cid: SpanCircuitSnapshotFactory.create(
                circuit_id=cid,
                name=f"Name of {cid}",
                breaker_rating_a=rating,
                breaker_rating_declared=declared.get(cid, True),
            )
            for cid, rating in ratings.items()
        },
        main_breaker_rating_a=main,
    )


_DECLARED = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}
_UNDECLARED: dict[str, FieldMetadata] = {}


def _declared() -> Mapping[str, FieldMetadata]:
    """Answer field metadata declaring a service rating, as the client does once ready."""
    return _DECLARED


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry in hass."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-001")
    mock.add_to_hass(hass)
    return mock


@pytest.fixture
def other(hass: HomeAssistant) -> MockConfigEntry:
    """Return a second panel."""
    mock = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-002")
    mock.add_to_hass(hass)
    return mock


async def _begun(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    snapshot: SpanPanelSnapshot | None = None,
) -> MonitoringRatingManager:
    """Return a loaded manager whose first reconcile has run against a declared service rating."""
    await async_restore(hass, entry)
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot or _snapshot(), _DECLARED)
    await hass.async_block_till_done()
    return manager


async def _stored(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, dict[str, object]]:
    """Read back what the manager last saved, through a store of its own."""
    data = await Store[dict[str, dict[str, object]]](hass, 1, _store_key(entry)).async_load()
    assert data is not None
    return data


def _events(hass: HomeAssistant) -> list[Event]:
    events: list[Event] = async_capture_events(hass, EVENT_MONITORING_RATING_CHANGED)
    return events


def _message(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return str(hass.data["persistent_notification"][_notification_id(entry)]["message"])


# -- Store -------------------------------------------------------------------


async def test_a_set_rating_is_saved_and_loaded_by_the_next_manager(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    await hass.async_block_till_done()

    stored = hass_storage[_store_key(entry)]
    assert isinstance(stored, dict)
    assert stored["version"] == 1
    assert stored["data"] == {
        "ratings": {
            "circuit-2": {"rating_a": 50, "panel_a_at_set": 20.0, "label": "Name of circuit-2"}
        },
        "unacknowledged": {},
    }

    reloaded = MonitoringRatingManager(hass, entry)
    await reloaded.async_load()
    reloaded.reconcile(_snapshot(), _declared)  # inert; resolve uses the loaded ratings
    rating = reloaded.resolve("circuit-2", _snapshot())
    # The declaration is unknown until `begin`, so the monitoring rating is pending: unrated.
    assert (rating.judged_a, rating.panel_a, rating.user_set) == (None, 20.0, False)
    reloaded.begin(_snapshot(), _DECLARED)
    rating = reloaded.resolve("circuit-2", _snapshot())
    assert (rating.judged_a, rating.panel_a, rating.user_set) == (50.0, 20.0, True)


async def test_a_malformed_store_loads_as_empty(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "data": {
            "ratings": {
                "good": {"rating_a": 30, "panel_a_at_set": 15.0, "label": "Good"},
                "bad": {"rating_a": "x"},
            },
            "unacknowledged": "nonsense",
        },
    }
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot({"good": 15.0, "bad": 10.0}), _DECLARED)
    assert manager.resolve("good", _snapshot({"good": 15.0})).user_set
    assert not manager.resolve("bad", _snapshot({"bad": 10.0})).user_set


async def test_removing_the_config_entry_deletes_its_ratings(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    """Keyed by wire circuit ids, the store would be picked up by the next entry for the same panel."""
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    await hass.async_block_till_done()
    assert _store_key(entry) in hass_storage

    await async_remove_entry(hass, entry)

    assert _store_key(entry) not in hass_storage
    reloaded = MonitoringRatingManager(hass, entry)
    await reloaded.async_load()
    reloaded.begin(_snapshot(), _DECLARED)
    assert not reloaded.resolve("circuit-2", _snapshot()).user_set


# -- Notice text ---------------------------------------------------------------

_LANGUAGES = ("en", "es", "fr", "ja", "pt")

_LINE_PLACEHOLDERS: dict[str, set[str]] = {
    "panel_changed": {"circuit", "rating", "before", "after"},
    "panel_agrees": {"circuit", "rating", "before", "after"},
    "rating_undeclared": {"circuit", "rating", "before"},
    "circuit_removed": {"circuit", "rating", "before"},
    "above_service_rating": {"circuit", "rating", "before", "after", "service"},
    "service_rating_undeclared": {"circuit", "rating", "before"},
}
"""What each line names: the user's rating and the panel's before, and after where there is one.

A circuit that has left, or no longer declares a rating, has no rating after;
neither may a circuit when the service rating goes, since that removes every
stored, present or not.
"""

_MONITORING_SECTION = {
    "en": "**Monitoring** section",
    "es": "sección **Monitoreo**",
    "fr": "section **Surveillance**",
    "ja": "**モニタリング** セクション",
    "pt": "secção **Monitoramento**",
}
"""The card's Monitoring section by its title, `sidepanel.monitoring` in the card's i18n."""

_MONITORING_RATING = {
    "en": "monitoring rating",
    "es": "capacidad de monitoreo",
    "fr": "calibre de surveillance",
    "ja": "モニタリング定格",
    "pt": "capacidade de monitorização",
}
"""What each language calls the user's value; every line that names it uses these words."""

_BREAKER = {"en": "breaker", "es": "interruptor", "fr": "disjoncteur", "ja": "ブレーカー", "pt": "disjuntor"}
"""What each language calls a breaker. The intro heads every reason, and most are no breaker change."""

_SHOWN_WHILE_ON = {
    "en": "while monitoring is on for that circuit",
    "es": "mientras el monitoreo está activado para ese circuito",
    "fr": "tant que la surveillance est activée pour ce circuit",
    "ja": "その回路のモニタリングがオンの間",
    "pt": "enquanto a monitorização estiver ativada para esse circuito",
}
"""The card shows the editor only while the circuit's monitoring is on."""


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field}


def test_the_line_placeholders_cover_every_reason() -> None:
    assert set(_LINE_PLACEHOLDERS) == set(get_args(ChangeReason.__value__))


@pytest.mark.parametrize("language", _LANGUAGES)
def test_every_line_names_the_users_rating_and_the_panels_before_and_after(
    language: str,
) -> None:
    text = read_translations(language, _NOTICE)
    assert {reason: _placeholders(text[reason]) for reason in _LINE_PLACEHOLDERS} == (
        _LINE_PLACEHOLDERS
    )


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_notice_points_to_the_cards_monitoring_section(language: str) -> None:
    intro = read_translations(language, _NOTICE)["intro"]
    assert _MONITORING_SECTION[language] in intro
    assert _placeholders(intro) == set()


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_intro_fits_every_reason_the_notice_reports(language: str) -> None:
    """A circuit removed, or a service rating gone or exceeded, is no breaker rating changing on a circuit."""
    intro = read_translations(language, _NOTICE)["intro"]
    assert _BREAKER[language] not in intro.lower()


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_intro_says_the_section_shows_while_monitoring_is_on(language: str) -> None:
    """With the circuit's monitoring off the editor is hidden, so the user must turn it on to find it."""
    assert _SHOWN_WHILE_ON[language] in read_translations(language, _NOTICE)["intro"]


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_notice_calls_the_users_value_a_monitoring_rating(language: str) -> None:
    text = read_translations(language, _NOTICE)
    assert _MONITORING_RATING[language] in text["intro"]
    for reason in _LINE_PLACEHOLDERS:
        assert _MONITORING_RATING[language] in text[reason], reason


# -- set_rating and clear_rating ------------------------------------------


async def test_set_refuses_a_circuit_the_panel_does_not_have(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    with pytest.raises(ServiceValidationError) as raised:
        manager.set_rating("circuit-9", _snapshot(), 50)
    assert raised.value.translation_key == "monitoring_rating_circuit_unknown"


async def test_set_refuses_a_circuit_with_no_panel_rating(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    snapshot = _snapshot({"circuit-2": None})
    manager = await _begun(hass, entry, snapshot)
    with pytest.raises(ServiceValidationError) as raised:
        manager.set_rating("circuit-2", snapshot, 50)
    assert raised.value.translation_key == "monitoring_rating_not_published"


async def test_set_refuses_when_the_panel_declares_no_service_rating(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await async_restore(hass, entry)
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot(), _UNDECLARED)
    with pytest.raises(ServiceValidationError) as raised:
        manager.set_rating("circuit-2", _snapshot(), 50)
    assert raised.value.translation_key == "monitoring_rating_no_service_rating"


async def test_set_refuses_when_the_declared_service_rating_has_no_value(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    snapshot = _snapshot(main=None)
    manager = await _begun(hass, entry, snapshot)
    with pytest.raises(ServiceValidationError) as raised:
        manager.set_rating("circuit-2", snapshot, 50)
    assert raised.value.translation_key == "monitoring_rating_no_service_rating"


async def test_set_refuses_a_rating_above_the_service_rating_and_accepts_it_equal(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    with pytest.raises(ServiceValidationError) as raised:
        manager.set_rating("circuit-2", _snapshot(), SERVICE_A + 1)
    assert raised.value.translation_key == "monitoring_rating_above_service_rating"
    assert raised.value.translation_placeholders == {"service_rating": "100"}

    manager.set_rating("circuit-2", _snapshot(), SERVICE_A)
    assert manager.resolve("circuit-2", _snapshot()).judged_a == float(SERVICE_A)


async def test_set_to_the_panel_rating_clears(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    manager.set_rating("circuit-2", _snapshot(), 20)
    await hass.async_block_till_done()
    rating = manager.resolve("circuit-2", _snapshot())
    assert (rating.judged_a, rating.user_set) == (20.0, False)
    assert (await _stored(hass, entry))["ratings"] == {}


async def test_clear_refuses_a_circuit_not_in_the_snapshot(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    with pytest.raises(ServiceValidationError) as raised:
        manager.clear_rating("circuit-9", _snapshot())
    assert raised.value.translation_key == "monitoring_rating_circuit_unknown"


async def test_clear_with_nothing_stored_is_quiet(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    events = _events(hass)
    manager.clear_rating("circuit-2", _snapshot())
    await hass.async_block_till_done()
    assert events == []


# -- Events --------------------------------------------------------------------


async def test_set_and_clear_fire_the_event_for_their_entry(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    events = _events(hass)

    manager.set_rating("circuit-2", _snapshot(), 50)
    manager.clear_rating("circuit-2", _snapshot())
    await hass.async_block_till_done()

    assert [event.data for event in events] == [
        {"config_entry_id": entry.entry_id, "circuit_ids": ["circuit-2"]},
        {"config_entry_id": entry.entry_id, "circuit_ids": ["circuit-2"]},
    ]


async def test_a_reconcile_change_fires_the_event_and_a_no_op_does_not(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_rating("circuit-2", _snapshot(ratings), 50)
    manager.set_rating("circuit-3", _snapshot(ratings), 40)
    await hass.async_block_till_done()
    events = _events(hass)

    manager.reconcile(_snapshot(ratings), _declared)
    await hass.async_block_till_done()
    assert events == []

    manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 40.0}), _declared)
    await hass.async_block_till_done()
    assert [event.data for event in events] == [
        {"config_entry_id": entry.entry_id, "circuit_ids": ["circuit-2", "circuit-3"]}
    ]


# -- begin ---------------------------------------------------------------------


async def test_reconcile_is_inert_until_begin(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    """A snapshot that lacks the circuit must not delete its stored before the first one is judged."""
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "data": {
            "ratings": {"circuit-2": {"rating_a": 50, "panel_a_at_set": 20.0, "label": "Oven"}},
            "unacknowledged": {},
        },
    }
    await async_restore(hass, entry)
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    events = _events(hass)

    manager.reconcile(_snapshot({}), _declared)
    await hass.async_block_till_done()
    assert events == []
    assert not is_standing(hass, entry, _NOTICE)

    manager.begin(_snapshot({"circuit-2": 20.0}), _DECLARED)
    assert manager.resolve("circuit-2", _snapshot()).user_set

    manager.reconcile(_snapshot({}), _declared)
    await hass.async_block_till_done()
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"]]
    assert is_standing(hass, entry, _NOTICE)
    assert "**Name of circuit-2**" in _message(hass, entry)  # the label begin refreshed


# -- The service rating declaration ----------------------------------------------


class _Metadata:
    """A field metadata source that counts its reads."""

    def __init__(self, value: Mapping[str, FieldMetadata] | None) -> None:
        self.value = value
        self.reads = 0

    def __call__(self) -> Mapping[str, FieldMetadata] | None:
        self.reads += 1
        return self.value


async def test_missing_metadata_after_a_known_declaration_changes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A rebuild hands out no metadata; the declaration made at `begin` stands for the session."""
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    await hass.async_block_till_done()
    events = _events(hass)

    manager.reconcile(_snapshot(), _Metadata(None))
    await hass.async_block_till_done()

    rating = manager.resolve("circuit-2", _snapshot())
    assert (rating.judged_a, rating.user_set) == (50.0, True)
    manager.set_rating("circuit-2", _snapshot(), 60)  # still bounded, not refused
    assert manager.resolve("circuit-2", _snapshot()).judged_a == 60.0
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"]]


@pytest.mark.parametrize("at_begin", [_DECLARED, _UNDECLARED])
async def test_a_known_declaration_is_not_read_again_on_a_snapshot(
    hass: HomeAssistant, entry: MockConfigEntry, at_begin: Mapping[str, FieldMetadata]
) -> None:
    """The library documents the metadata as a once-per-session read; it costs a schema walk."""
    await async_restore(hass, entry)
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot(), at_begin)
    source = _Metadata(_DECLARED)

    for _ in range(3):
        manager.reconcile(_snapshot(), source)

    assert source.reads == 0


async def test_an_unknown_declaration_is_read_on_each_snapshot_until_it_is_known(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await async_restore(hass, entry)
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot(), None)
    with pytest.raises(ServiceValidationError):  # refused until the declaration is known
        manager.set_rating("circuit-2", _snapshot(), 50)
    source = _Metadata(None)

    manager.reconcile(_snapshot(), source)
    manager.reconcile(_snapshot(), source)
    source.value = _DECLARED
    manager.reconcile(_snapshot(), source)
    manager.reconcile(_snapshot(), source)

    assert source.reads == 3
    manager.set_rating("circuit-2", _snapshot(), 50)
    assert manager.resolve("circuit-2", _snapshot()).user_set


# -- Notices -------------------------------------------------------------------


async def test_a_change_raises_one_notice_naming_the_circuit(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)

    manager.reconcile(_snapshot({"circuit-2": 30.0}), _declared)
    await hass.async_block_till_done()

    message = _message(hass, entry)
    assert (
        "**Name of circuit-2**: the panel now reports 30 A (was 20 A). Your 50 A monitoring rating still applies."
        in message
    )
    notification = hass.data["persistent_notification"][_notification_id(entry)]
    assert notification["title"] == "SPAN Panel monitoring ratings changed"


async def test_changes_accumulate_while_the_notice_stands_and_reset_after_dismissal(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_rating("circuit-2", _snapshot(ratings), 50)
    manager.set_rating("circuit-3", _snapshot(ratings), 40)

    manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 15.0}), _declared)
    await hass.async_block_till_done()
    assert "circuit-2" in _message(hass, entry)
    assert "circuit-3" not in _message(hass, entry)

    manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 25.0}), _declared)
    await hass.async_block_till_done()
    assert "circuit-2" in _message(hass, entry)
    assert "circuit-3" in _message(hass, entry)

    async_dismiss(hass, _notification_id(entry))
    await hass.async_block_till_done()
    manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 35.0}), _declared)
    await hass.async_block_till_done()
    assert "circuit-2" not in _message(hass, entry)
    assert "circuit-3" in _message(hass, entry)


async def test_a_change_and_change_back_before_the_user_looks_is_not_reported(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """50 A set against 20 A; the panel says 25 A, then 20 A again, before the notice is dismissed.

    As it stands nothing changed, so the line goes; it must not report "now 20 A
    (was 25 A)", a value the user never saw. With no other line the notice has
    nothing left to say, and is taken down.
    """
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    manager.reconcile(_snapshot({"circuit-2": 25.0}), _declared)
    await hass.async_block_till_done()
    assert "now reports 25 A (was 20 A)" in _message(hass, entry)

    manager.reconcile(_snapshot({"circuit-2": 20.0}), _declared)
    await hass.async_block_till_done()

    assert not is_standing(hass, entry, _NOTICE)
    assert _notification_id(entry) not in hass.data["persistent_notification"]
    stored = await _stored(hass, entry)
    assert stored["unacknowledged"] == {}
    assert stored["ratings"]["circuit-2"] == {
        "rating_a": 50,
        "panel_a_at_set": 20.0,
        "label": "Name of circuit-2",
    }
    assert manager.resolve("circuit-2", _snapshot()).user_set


async def test_a_change_and_change_back_leaves_the_other_circuits_lines(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_rating("circuit-2", _snapshot(ratings), 50)
    manager.set_rating("circuit-3", _snapshot(ratings), 40)
    manager.reconcile(_snapshot({"circuit-2": 25.0, "circuit-3": 30.0}), _declared)
    await hass.async_block_till_done()

    manager.reconcile(_snapshot({"circuit-2": 20.0, "circuit-3": 30.0}), _declared)
    await hass.async_block_till_done()

    message = _message(hass, entry)
    assert "circuit-2" not in message
    assert "**Name of circuit-3**: the panel now reports 30 A (was 15 A)" in message
    assert list((await _stored(hass, entry))["unacknowledged"]) == ["circuit-3"]


async def test_a_change_then_a_removal_is_reported_from_the_rating_the_user_last_saw(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """20 A, then 25 A, then 50 A, which matches the monitoring rating: removed, and "was 20 A"."""
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    manager.reconcile(_snapshot({"circuit-2": 25.0}), _declared)
    await hass.async_block_till_done()

    manager.reconcile(_snapshot({"circuit-2": 50.0}), _declared)
    await hass.async_block_till_done()

    change = (await _stored(hass, entry))["unacknowledged"]["circuit-2"]
    assert isinstance(change, dict)
    assert (change["reason"], change["removed"]) == ("panel_agrees", True)
    assert (change["panel_before_a"], change["panel_after_a"]) == (20.0, 50.0)
    assert (
        "**Name of circuit-2**: the panel now reports 50 A (was 20 A), matching your 50 A "
        "monitoring rating, so the monitoring rating was removed."
    ) in _message(hass, entry)


async def test_a_removal_stands_even_when_the_rating_ends_where_it_began(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """20 A, then 25 A, then 20 A with the service rating cut to 40 A: removed, so the user is told.

    The panel's rating is back where the user last saw it, which would drop a
    line for a monitoring rating still in force; this one is gone.
    """
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    manager.reconcile(_snapshot({"circuit-2": 25.0}), _declared)
    await hass.async_block_till_done()

    manager.reconcile(_snapshot({"circuit-2": 20.0}, main=40), _declared)
    await hass.async_block_till_done()

    change = (await _stored(hass, entry))["unacknowledged"]["circuit-2"]
    assert isinstance(change, dict)
    assert (change["reason"], change["removed"]) == ("above_service_rating", True)
    assert (change["panel_before_a"], change["panel_after_a"]) == (20.0, 20.0)
    assert is_standing(hass, entry, _NOTICE)
    assert (
        "**Name of circuit-2**: your 50 A monitoring rating is above the panel's service rating of "
        "40 A, so it was removed. The panel reported 20 A and now reports 20 A."
    ) in _message(hass, entry)


async def test_two_changes_before_the_notice_is_raised_are_both_kept(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The text is read off the loop, so a second change can land before the first notice does.

    The read is held until both changes are in, which is the only way to make the
    interleaving certain: left to the executor it sometimes finishes between them.
    """
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_rating("circuit-2", _snapshot(ratings), 50)
    manager.set_rating("circuit-3", _snapshot(ratings), 40)
    release = threading.Event()

    def held(language: str, section: str) -> dict[str, str]:
        release.wait(timeout=5)
        return read_translations(language, section)

    with patch(
        "custom_components.span_panel.monitoring_rating_manager.notices.read_translations", held
    ):
        manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 15.0}), _declared)
        manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 25.0}), _declared)
        release.set()
        await hass.async_block_till_done()

    assert "circuit-2" in _message(hass, entry)
    assert "circuit-3" in _message(hass, entry)


async def test_a_change_and_change_back_before_the_notice_is_raised_raises_nothing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Both land while the first notice's text is still being read: no empty notice follows."""
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    release = threading.Event()

    def held(language: str, section: str) -> dict[str, str]:
        release.wait(timeout=5)
        return read_translations(language, section)

    with patch(
        "custom_components.span_panel.monitoring_rating_manager.notices.read_translations", held
    ):
        manager.reconcile(_snapshot({"circuit-2": 25.0}), _declared)
        manager.reconcile(_snapshot({"circuit-2": 20.0}), _declared)
        release.set()
        await hass.async_block_till_done()

    assert not is_standing(hass, entry, _NOTICE)
    assert _notification_id(entry) not in hass.data.get("persistent_notification", {})


async def test_unacknowledged_changes_are_stored_and_a_reloaded_manager_adds_to_them(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    manager.reconcile(_snapshot({}), _declared)  # the circuit leaves: removed, told
    await hass.async_block_till_done()

    stored = await _stored(hass, entry)
    assert stored["ratings"] == {}
    assert stored["unacknowledged"] == {
        "circuit-2": {
            "circuit_id": "circuit-2",
            "label": "Name of circuit-2",
            "reason": "circuit_removed",
            "rating_a": 50,
            "panel_before_a": 20.0,
            "panel_after_a": None,
            "service_a": 100.0,
            "removed": True,
        }
    }


@pytest.mark.parametrize("reason", get_args(ChangeReason.__value__))
async def test_a_stored_change_of_every_reason_round_trips(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object], reason: str
) -> None:
    """A reason the loader does not accept would drop the line as malformed."""
    row = {
        "circuit_id": "circuit-2",
        "label": "Oven",
        "reason": reason,
        "rating_a": 50,
        "panel_before_a": 20.0,
        "panel_after_a": 30.0,
        "service_a": 100.0,
        "removed": reason != "panel_changed",
    }
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "data": {"ratings": {}, "unacknowledged": {"circuit-2": row}},
    }
    manager = MonitoringRatingManager(hass, entry)
    await manager.async_load()
    await manager.async_save()

    assert (await _stored(hass, entry))["unacknowledged"] == {"circuit-2": row}


async def test_a_reloaded_manager_keeps_the_stored_changes_and_adds_a_new_one(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The stored changes round trip, reason included; `notices.async_restore` re-raises the notice."""
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_rating("circuit-2", _snapshot(ratings), 50)
    manager.set_rating("circuit-3", _snapshot(ratings), 40)
    manager.reconcile(_snapshot({"circuit-3": 15.0}), _declared)  # circuit-2 leaves
    await hass.async_block_till_done()

    reloaded = MonitoringRatingManager(hass, entry)
    await reloaded.async_load()
    reloaded.begin(_snapshot({"circuit-3": 25.0}), _DECLARED)  # circuit-3 changes
    await hass.async_block_till_done()

    message = _message(hass, entry)
    assert (
        "**Name of circuit-2**: this circuit is no longer on the panel (it reported 20 A), "
        "so your 50 A monitoring rating was removed."
    ) in message
    assert "**Name of circuit-3**: the panel now reports 25 A (was 15 A)" in message


@pytest.mark.parametrize("how", ["clear", "set"])
async def test_a_user_change_drops_that_circuit_from_the_standing_notice(
    hass: HomeAssistant, entry: MockConfigEntry, how: str
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_rating("circuit-2", _snapshot(ratings), 50)
    manager.set_rating("circuit-3", _snapshot(ratings), 40)
    changed = _snapshot({"circuit-2": 30.0, "circuit-3": 25.0})
    manager.reconcile(changed, _declared)
    await hass.async_block_till_done()
    assert "circuit-2" in _message(hass, entry)

    if how == "clear":
        manager.clear_rating("circuit-2", changed)
    else:
        manager.set_rating("circuit-2", changed, 60)
    await hass.async_block_till_done()

    assert "circuit-2" not in _message(hass, entry)
    assert "circuit-3" in _message(hass, entry)
    assert "circuit-2" not in (await _stored(hass, entry))["unacknowledged"]


async def test_a_user_change_to_the_only_listed_circuit_leaves_the_notice_standing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    manager.set_rating("circuit-2", _snapshot(), 50)
    changed = _snapshot({"circuit-2": 30.0})
    manager.reconcile(changed, _declared)
    await hass.async_block_till_done()

    manager.clear_rating("circuit-2", changed)
    await hass.async_block_till_done()

    assert is_standing(hass, entry, _NOTICE)
    assert (await _stored(hass, entry))["unacknowledged"] == {}


async def test_two_panels_with_the_same_circuit_id_stay_independent(
    hass: HomeAssistant, entry: MockConfigEntry, other: MockConfigEntry
) -> None:
    first = await _begun(hass, entry)
    second = await _begun(hass, other)
    events = _events(hass)

    first.set_rating("circuit-2", _snapshot(), 50)
    await hass.async_block_till_done()

    assert first.resolve("circuit-2", _snapshot()).user_set
    assert not second.resolve("circuit-2", _snapshot()).user_set
    assert [event.data["config_entry_id"] for event in events] == [entry.entry_id]

    first.reconcile(_snapshot({}), _declared)
    await hass.async_block_till_done()
    assert is_standing(hass, entry, _NOTICE)
    assert not is_standing(hass, other, _NOTICE)
