"""Tests for the manager that stores package ratings copied from the SPAN app and announces their removal."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
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
from custom_components.span_panel.const import DOMAIN, EVENT_PACKAGE_RATINGS_CHANGED
from custom_components.span_panel.notices import async_restore, is_standing, read_translations
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.package_ratings import ChangeReason, MonitoringRating

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

_NOTICE = "package_ratings_changed"
SERVICE_A = 100


def _store_key(entry: MockConfigEntry) -> str:
    return f"span_panel_package_ratings.{entry.entry_id}"


def _notification_id(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}_{_NOTICE}_{entry.entry_id}"


def _space(circuit_id: str) -> list[int]:
    """Each synthetic circuit sits in the space its id names: circuit-2 in space 2."""
    return [int(circuit_id.rsplit("-", 1)[1])]


def _snapshot(
    ratings: Mapping[str, float | None] | None = None,
    main: int | None = SERVICE_A,
    declared: Mapping[str, bool | None] | None = None,
    tabs: Mapping[str, list[int]] | None = None,
) -> SpanPanelSnapshot:
    ratings = {"circuit-2": 20.0} if ratings is None else ratings
    declared = declared or {}
    tabs = tabs or {}
    return SpanPanelSnapshotFactory.create(
        circuits={
            cid: SpanCircuitSnapshotFactory.create(
                circuit_id=cid,
                name=f"Name of {cid}",
                tabs=tabs.get(cid, _space(cid)),
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
) -> PackageRatingManager:
    """Return a loaded manager whose first reconcile has run against a declared service rating."""
    await async_restore(hass, entry)
    manager = PackageRatingManager(hass, entry)
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
    events: list[Event] = async_capture_events(hass, EVENT_PACKAGE_RATINGS_CHANGED)
    return events


def _message(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return str(hass.data["persistent_notification"][_notification_id(entry)]["message"])


def _refused(manager: PackageRatingManager, snapshot: SpanPanelSnapshot, ratings: Sequence[int], circuit: str = "circuit-2") -> ServiceValidationError:
    with pytest.raises(ServiceValidationError) as raised:
        manager.set_package_ratings(circuit, snapshot, ratings)
    return raised.value


# -- Store -------------------------------------------------------------------


async def test_copied_ratings_are_saved_and_loaded_by_the_next_manager(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 30])
    await hass.async_block_till_done()

    stored = hass_storage[_store_key(entry)]
    assert isinstance(stored, dict)
    assert stored["version"] == 1
    assert stored["data"] == {
        "packages": {
            "circuit-2": {"package_ratings_a": [20, 30], "space": 2, "label": "Name of circuit-2"}
        },
        "unacknowledged": {},
    }

    reloaded = PackageRatingManager(hass, entry)
    await reloaded.async_load()
    reloaded.reconcile(_snapshot(), _declared)  # inert; resolve uses the loaded entries
    # The declaration is unknown until `begin`, so the entry is pending: unrated, and kept.
    assert reloaded.resolve("circuit-2", _snapshot()) == MonitoringRating(None, 20.0, False, (20, 30), "pending")
    reloaded.begin(_snapshot(), _DECLARED)
    assert reloaded.resolve("circuit-2", _snapshot()) == MonitoringRating(50.0, 20.0, True, (20, 30), "applies")


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"package_ratings_a": "x", "space": 3, "label": "Bad"}, id="ratings not a list"),
        pytest.param({"package_ratings_a": [10], "space": 3, "label": "Bad"}, id="one rating"),
        pytest.param({"package_ratings_a": [10, 10, 10], "space": 3, "label": "Bad"}, id="three ratings"),
        pytest.param({"package_ratings_a": [10, True], "space": 3, "label": "Bad"}, id="a bool rating"),
        pytest.param({"package_ratings_a": [10, 0], "space": 3, "label": "Bad"}, id="a zero rating"),
        pytest.param({"package_ratings_a": [10, 15.5], "space": 3, "label": "Bad"}, id="a fractional rating"),
        pytest.param({"package_ratings_a": [10, 10], "label": "Bad"}, id="no space"),
        pytest.param({"package_ratings_a": [10, 10], "space": True, "label": "Bad"}, id="a bool space"),
        pytest.param({"package_ratings_a": [10, 10], "space": 3}, id="no label"),
        pytest.param({"rating_a": 20, "panel_a_at_set": 10.0, "label": "Bad"}, id="a row of an older shape"),
        pytest.param("nonsense", id="not a row"),
    ],
)
async def test_a_malformed_row_is_dropped_and_the_rest_load(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object], bad: object
) -> None:
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "data": {
            "packages": {
                "circuit-1": {"package_ratings_a": [15, 20], "space": 1, "label": "Good"},
                "circuit-3": bad,
            },
            "unacknowledged": "nonsense",
        },
    }
    snapshot = _snapshot({"circuit-1": 15.0, "circuit-3": 10.0})
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    assert manager.resolve("circuit-1", snapshot).user_set
    assert manager.resolve("circuit-3", snapshot) == MonitoringRating(10.0, 10.0, False, None, None)


async def test_a_store_without_packages_loads_as_empty(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    hass_storage[_store_key(entry)] = {"version": 1, "data": {"packages": "nonsense"}}
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot(), _DECLARED)
    assert not manager.resolve("circuit-2", _snapshot()).user_set


async def test_removing_the_config_entry_deletes_its_entries(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    """Keyed by wire circuit ids, the store would be picked up by the next entry for the same panel."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    await hass.async_block_till_done()
    assert _store_key(entry) in hass_storage

    await async_remove_entry(hass, entry)

    assert _store_key(entry) not in hass_storage
    reloaded = PackageRatingManager(hass, entry)
    await reloaded.async_load()
    reloaded.begin(_snapshot(), _DECLARED)
    assert not reloaded.resolve("circuit-2", _snapshot()).user_set


# -- Notice text ---------------------------------------------------------------

_LANGUAGES = ("en", "es", "fr", "ja", "pt")

_LINE_PLACEHOLDERS: dict[str, set[str]] = {
    "panel_rating_changed": {"circuit", "package", "before", "after"},
    "spaces_changed": {"circuit", "package"},
    "rating_undeclared": {"circuit", "package"},
    "circuit_removed": {"circuit", "package"},
    "above_service_rating": {"circuit", "package", "service"},
    "service_rating_undeclared": {"circuit", "package"},
    "panel_describes_breakers": {"circuit", "package"},
}
"""What each line names: the circuit and the ratings copied, and what changed where a figure says it.

The package already names the rating the panel published when it was copied,
its first, so only a changed panel rating names it again, beside the new one.
"""

_MONITORING_SECTION = {
    "en": "**Monitoring** section",
    "es": "sección **Monitoreo**",
    "fr": "section **Surveillance**",
    "ja": "**モニタリング** セクション",
    "pt": "secção **Monitoramento**",
}
"""The card's Monitoring section by its title, `sidepanel.monitoring` in the card's i18n."""

_COPIED = {
    "en": "copied from the SPAN app",
    "es": "copiaste de la app de SPAN",
    "fr": "copiés depuis l'application SPAN",
    "ja": "SPAN アプリからコピーした",
    "pt": "copiou da aplicação SPAN",
}
"""How each language says where the ratings came from; the intro and every line say it."""

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
def test_every_line_names_the_circuit_the_copy_and_what_changed(language: str) -> None:
    text = read_translations(language, _NOTICE)
    assert {reason: _placeholders(text[reason]) for reason in _LINE_PLACEHOLDERS} == _LINE_PLACEHOLDERS
    assert set(text) == {"title", "intro", *_LINE_PLACEHOLDERS}


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_notice_points_to_the_cards_monitoring_section(language: str) -> None:
    intro = read_translations(language, _NOTICE)["intro"]
    assert _MONITORING_SECTION[language] in intro
    assert _placeholders(intro) == set()


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_intro_fits_every_reason_the_notice_reports(language: str) -> None:
    """A circuit removed, or a service rating gone or exceeded, is no breaker changing on a circuit."""
    intro = read_translations(language, _NOTICE)["intro"]
    assert _BREAKER[language] not in intro.lower()


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_intro_says_the_section_shows_while_monitoring_is_on(language: str) -> None:
    """With the circuit's monitoring off the editor is hidden, so the user must turn it on to find it."""
    assert _SHOWN_WHILE_ON[language] in read_translations(language, _NOTICE)["intro"]


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_notice_says_the_ratings_were_copied_from_the_span_app(language: str) -> None:
    text = read_translations(language, _NOTICE)
    assert _COPIED[language] in text["intro"]
    for reason in _LINE_PLACEHOLDERS:
        assert _COPIED[language] in text[reason], reason


# -- set_package_ratings and clear_package_ratings -----------------------------


async def test_set_refuses_a_circuit_the_panel_does_not_have(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await _begun(hass, entry)
    assert _refused(manager, _snapshot(), [20, 20], "circuit-9").translation_key == "package_ratings_circuit_unknown"


async def test_set_refuses_on_a_panel_that_describes_its_breakers(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Each circuit there publishes its own rating, and shared meters are judged without the user's help."""
    snapshot = replace(_snapshot(), publishes_shared_meters=True)
    manager = await _begun(hass, entry, snapshot)
    assert _refused(manager, snapshot, [20, 20]).translation_key == "package_ratings_panel_describes_breakers"
    assert not manager.resolve("circuit-2", snapshot).user_set


async def test_a_panel_that_describes_its_breakers_removes_a_stored_entry_and_says_so(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An entry copied while the panel published no shared-meter list goes once it does, with a notice line."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    events = _events(hass)
    describing = replace(_snapshot(), publishes_shared_meters=True)

    assert not manager.resolve("circuit-2", describing).user_set
    manager.reconcile(describing, _declared)
    await hass.async_block_till_done()

    assert (await _stored(hass, entry))["packages"] == {}
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"]]
    assert (
        "**Name of circuit-2**: the panel now publishes each circuit of a multi-load breaker with its own rating, "
        "which current monitoring uses, so the 20-20 ratings you copied from the SPAN app were removed."
    ) in _message(hass, entry)


async def test_set_refuses_a_circuit_the_panel_declares_no_rating_for(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    snapshot = _snapshot({"circuit-2": None}, declared={"circuit-2": False})
    manager = await _begun(hass, entry, snapshot)
    assert _refused(manager, snapshot, [20, 20]).translation_key == "package_ratings_not_published"


@pytest.mark.parametrize("declared", [True, None], ids=["declared", "declaration not known"])
async def test_set_refuses_a_panel_rating_not_yet_arrived_as_not_arrived(
    hass: HomeAssistant, entry: MockConfigEntry, declared: bool | None
) -> None:
    """The panel has not said the circuit has no rating, only not sent it yet, so the refusal says to wait."""
    snapshot = _snapshot({"circuit-2": None}, declared={"circuit-2": declared})
    manager = await _begun(hass, entry, snapshot)
    assert _refused(manager, snapshot, [20, 20]).translation_key == "package_ratings_not_arrived"


async def test_set_refuses_when_the_panel_declares_no_service_rating(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await async_restore(hass, entry)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot(), _UNDECLARED)
    assert _refused(manager, _snapshot(), [20, 20]).translation_key == "package_ratings_no_service_rating"


async def test_set_refuses_a_declared_service_rating_not_yet_arrived_as_not_arrived(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    snapshot = _snapshot(main=None)
    manager = await _begun(hass, entry, snapshot)
    assert _refused(manager, snapshot, [20, 20]).translation_key == "package_ratings_not_arrived"


async def test_set_refuses_while_the_service_rating_declaration_is_not_known_as_not_arrived(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    await async_restore(hass, entry)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot(), None)
    assert _refused(manager, _snapshot(), [20, 20]).translation_key == "package_ratings_not_arrived"


async def test_set_refuses_a_circuit_in_more_than_two_spaces(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A copy is for a package in one space or across two, so a circuit listing more takes none."""
    snapshot = _snapshot(tabs={"circuit-2": [2, 4, 6]})
    manager = await _begun(hass, entry, snapshot)
    assert _refused(manager, snapshot, [20, 20]).translation_key == "package_ratings_spaces"


async def test_a_two_space_package_set_and_reconciled_unchanged_fires_once(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The set records what the entry is, kept on averaged legs, so the next reconcile finds nothing changed."""
    snapshot = _snapshot(tabs={"circuit-2": [2, 4]})
    manager = await _begun(hass, entry, snapshot)
    events = _events(hass)

    manager.set_package_ratings("circuit-2", snapshot, [20, 30])
    manager.reconcile(snapshot, _declared)
    await hass.async_block_till_done()

    assert manager.resolve("circuit-2", snapshot) == MonitoringRating(20.0, 20.0, False, (20, 30), "averaged_legs")
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"]]


async def test_set_refuses_a_circuit_whose_space_has_not_arrived_as_not_arrived(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A circuit always sits in a space, so one listing none has not had it arrive."""
    snapshot = _snapshot(tabs={"circuit-2": []})
    manager = await _begun(hass, entry, snapshot)
    assert _refused(manager, snapshot, [20, 20]).translation_key == "package_ratings_not_arrived"


@pytest.mark.parametrize("ratings", [[], [20, 20, 20]], ids=["none", "three"])
async def test_set_refuses_other_than_two_ratings(hass: HomeAssistant, entry: MockConfigEntry, ratings: list[int]) -> None:
    """A space holds at most a tandem; a single rating is the clear, tested below."""
    manager = await _begun(hass, entry)
    assert _refused(manager, _snapshot(), ratings).translation_key == "package_ratings_not_two"


@pytest.mark.parametrize("ratings", [[30, 20], [25]], ids=["a package", "a single breaker"])
async def test_set_refuses_a_first_rating_that_is_not_the_panels_and_names_it(
    hass: HomeAssistant, entry: MockConfigEntry, ratings: list[int]
) -> None:
    """The panel publishes the package's first rating, so a copy that starts elsewhere was not copied in order."""
    manager = await _begun(hass, entry)
    refusal = _refused(manager, _snapshot(), ratings)
    assert refusal.translation_key == "package_ratings_first_not_panel"
    assert refusal.translation_placeholders == {"panel_rating": "20"}


async def test_set_refuses_a_capacity_above_the_service_rating_and_accepts_it_equal(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    refusal = _refused(manager, _snapshot(), [20, 81])
    assert refusal.translation_key == "package_ratings_above_service_rating"
    assert refusal.translation_placeholders == {"service_rating": "100"}

    manager.set_package_ratings("circuit-2", _snapshot(), [20, 80])
    assert manager.resolve("circuit-2", _snapshot()).judged_a == float(SERVICE_A)


async def test_set_to_the_panels_rating_alone_clears(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A single breaker in the space: nothing to add, so there is no entry."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    manager.set_package_ratings("circuit-2", _snapshot(), [20])
    await hass.async_block_till_done()
    assert manager.resolve("circuit-2", _snapshot()) == MonitoringRating(20.0, 20.0, False, None, None)
    assert (await _stored(hass, entry))["packages"] == {}


@pytest.mark.parametrize(
    "later",
    [
        pytest.param(_snapshot(main=None), id="service rating not yet arrived"),
        pytest.param(_snapshot({"circuit-2": None}), id="panel rating not yet arrived"),
        pytest.param(_snapshot(tabs={"circuit-2": []}), id="space not yet arrived"),
    ],
)
async def test_a_single_rating_clears_wherever_the_clear_service_would(
    hass: HomeAssistant, entry: MockConfigEntry, later: SpanPanelSnapshot
) -> None:
    """Clearing needs only the circuit; a value not yet arrived does not stand in its way."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    manager.reconcile(later, _declared)  # kept, pending
    await hass.async_block_till_done()

    manager.set_package_ratings("circuit-2", later, [20])
    await hass.async_block_till_done()

    assert (await _stored(hass, entry))["packages"] == {}


async def test_a_single_rating_clears_where_the_panel_publishes_none_and_on_a_panel_that_describes_its_breakers(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """With no panel rating to contradict it, a lone breaker's rating is the clear; nothing is refused."""
    undeclared = _snapshot({"circuit-2": None}, declared={"circuit-2": False})
    manager = await _begun(hass, entry, undeclared)
    manager.set_package_ratings("circuit-2", undeclared, [25])
    describing = replace(_snapshot(), publishes_shared_meters=True)
    manager.set_package_ratings("circuit-2", describing, [20])


async def test_setting_the_same_copy_again_is_quiet(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    await hass.async_block_till_done()
    events = _events(hass)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    await hass.async_block_till_done()
    assert events == []


async def test_clear_refuses_a_circuit_not_in_the_snapshot(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await _begun(hass, entry)
    with pytest.raises(ServiceValidationError) as raised:
        manager.clear_package_ratings("circuit-9", _snapshot())
    assert raised.value.translation_key == "package_ratings_circuit_unknown"


async def test_clear_with_nothing_stored_is_quiet(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await _begun(hass, entry)
    events = _events(hass)
    manager.clear_package_ratings("circuit-2", _snapshot())
    await hass.async_block_till_done()
    assert events == []


# -- Events --------------------------------------------------------------------


async def test_set_and_clear_fire_the_event_for_their_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await _begun(hass, entry)
    events = _events(hass)

    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    manager.clear_package_ratings("circuit-2", _snapshot())
    await hass.async_block_till_done()

    assert [event.data for event in events] == [
        {"config_entry_id": entry.entry_id, "circuit_ids": ["circuit-2"]},
        {"config_entry_id": entry.entry_id, "circuit_ids": ["circuit-2"]},
    ]


async def test_a_reconcile_removal_fires_the_event_and_a_no_op_does_not(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_package_ratings("circuit-2", _snapshot(ratings), [20, 20])
    manager.set_package_ratings("circuit-3", _snapshot(ratings), [15, 20])
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


async def test_an_entry_that_goes_pending_or_applies_again_fires_the_event_and_tells_no_one(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """What a circuit is judged against changes with it, so a reader re-reads; nothing was removed, so no notice."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    await hass.async_block_till_done()
    events = _events(hass)

    manager.reconcile(_snapshot(main=None), _declared)  # the service rating's value is not yet back
    await hass.async_block_till_done()
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"]]
    assert manager.resolve("circuit-2", _snapshot(main=None)).judged_a is None

    manager.reconcile(_snapshot(main=None), _declared)  # still waiting: nothing changed
    await hass.async_block_till_done()
    assert len(events) == 1

    manager.reconcile(_snapshot(), _declared)  # back: it applies again
    await hass.async_block_till_done()
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"], ["circuit-2"]]
    assert not is_standing(hass, entry, _NOTICE)
    assert (await _stored(hass, entry))["packages"] != {}


async def test_a_removal_and_a_circuit_going_pending_fire_one_event_naming_both(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_package_ratings("circuit-2", _snapshot(ratings), [20, 20])
    manager.set_package_ratings("circuit-3", _snapshot(ratings), [15, 20])
    await hass.async_block_till_done()
    events = _events(hass)

    manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": None}), _declared)
    await hass.async_block_till_done()

    assert [event.data["circuit_ids"] for event in events] == [["circuit-2", "circuit-3"]]


# -- begin ---------------------------------------------------------------------


async def test_reconcile_is_inert_until_begin(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object]
) -> None:
    """A snapshot that lacks the circuit must not delete its entry before the first one is judged."""
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "data": {
            "packages": {"circuit-2": {"package_ratings_a": [20, 20], "space": 2, "label": "Oven"}},
            "unacknowledged": {},
        },
    }
    await async_restore(hass, entry)
    manager = PackageRatingManager(hass, entry)
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
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    await hass.async_block_till_done()
    events = _events(hass)

    manager.reconcile(_snapshot(), _Metadata(None))
    await hass.async_block_till_done()

    assert manager.resolve("circuit-2", _snapshot()) == MonitoringRating(40.0, 20.0, True, (20, 20), "applies")
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 30])  # still bounded, not refused
    assert manager.resolve("circuit-2", _snapshot()).judged_a == 50.0
    assert [event.data["circuit_ids"] for event in events] == [["circuit-2"]]


@pytest.mark.parametrize("at_begin", [_DECLARED, _UNDECLARED])
async def test_a_known_declaration_is_not_read_again_on_a_snapshot(
    hass: HomeAssistant, entry: MockConfigEntry, at_begin: Mapping[str, FieldMetadata]
) -> None:
    """The library documents the metadata as a once-per-session read; it costs a schema walk."""
    await async_restore(hass, entry)
    manager = PackageRatingManager(hass, entry)
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
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(_snapshot(), None)
    _refused(manager, _snapshot(), [20, 20])  # refused until the declaration is known
    source = _Metadata(None)

    manager.reconcile(_snapshot(), source)
    manager.reconcile(_snapshot(), source)
    source.value = _DECLARED
    manager.reconcile(_snapshot(), source)
    manager.reconcile(_snapshot(), source)

    assert source.reads == 3
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    assert manager.resolve("circuit-2", _snapshot()).user_set


# -- Diagnostics -----------------------------------------------------------------


async def test_diagnostics_show_each_entry_its_space_and_what_the_rule_makes_of_it(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_package_ratings("circuit-2", _snapshot(ratings), [20, 20])
    manager.set_package_ratings("circuit-3", _snapshot(ratings), [15, 20])
    manager.reconcile(_snapshot({"circuit-2": 20.0, "circuit-3": 30.0}), _declared)
    await hass.async_block_till_done()

    pending = _snapshot({"circuit-2": None})
    assert manager.diagnostics(pending) == {
        "panel_describes_breakers": False,
        "service_rating_declaration": "declared",
        "packages": {
            "circuit-2": {
                "label": "Name of circuit-2",
                "package_ratings_a": [20, 20],
                "space": 2,
                "state": "pending",
            }
        },
        "unacknowledged": {
            "circuit-3": {
                "circuit_id": "circuit-3",
                "label": "Name of circuit-3",
                "reason": "panel_rating_changed",
                "package_ratings_a": [15, 20],
                "panel_after_a": 30.0,
                "service_a": 100.0,
            }
        },
    }
    assert manager.diagnostics(_snapshot())["packages"]["circuit-2"]["state"] == "applies"


async def test_diagnostics_list_a_removal_only_while_its_notice_stands(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Dismissing the notice spends its lines, so the download no longer lists them as unacknowledged."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    manager.reconcile(_snapshot({"circuit-2": 30.0}), _declared)
    await hass.async_block_till_done()
    assert list(manager.diagnostics(_snapshot({"circuit-2": 30.0}))["unacknowledged"]) == ["circuit-2"]

    async_dismiss(hass, _notification_id(entry))
    await hass.async_block_till_done()

    assert manager.diagnostics(_snapshot({"circuit-2": 30.0}))["unacknowledged"] == {}


async def test_diagnostics_list_a_removal_whose_notice_is_still_being_raised(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A notice waiting on its text counts as on screen, as it does for the notice's own lines."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    release = threading.Event()

    def held(language: str, section: str) -> dict[str, str]:
        release.wait(timeout=5)
        return read_translations(language, section)

    with patch("custom_components.span_panel.package_rating_manager.notices.read_translations", held):
        manager.reconcile(_snapshot({"circuit-2": 30.0}), _declared)
        assert list(manager.diagnostics(_snapshot({"circuit-2": 30.0}))["unacknowledged"]) == ["circuit-2"]
        release.set()
        await hass.async_block_till_done()


# -- Notices -------------------------------------------------------------------


async def test_a_changed_panel_rating_raises_one_notice_naming_the_circuit(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 30])

    manager.reconcile(_snapshot({"circuit-2": 30.0}), _declared)
    await hass.async_block_till_done()

    assert (
        "**Name of circuit-2**: the rating your panel publishes for this circuit changed from 20 A to 30 A, "
        "so the 20-30 ratings you copied from the SPAN app were removed."
    ) in _message(hass, entry)
    notification = hass.data["persistent_notification"][_notification_id(entry)]
    assert notification["title"] == "SPAN Panel ratings copied from the SPAN app removed"


async def test_a_circuit_in_another_space_loses_its_copy_and_is_told(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Same id, same first rating, but the space no longer holds the package that was copied."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 30])

    manager.reconcile(_snapshot(tabs={"circuit-2": [4]}), _declared)
    await hass.async_block_till_done()

    assert (await _stored(hass, entry))["packages"] == {}
    assert (
        "**Name of circuit-2**: this circuit's breaker spaces changed, "
        "so the 20-30 ratings you copied from the SPAN app were removed."
    ) in _message(hass, entry)


async def test_a_capacity_above_a_lowered_service_rating_is_removed_and_told(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 30])

    manager.reconcile(_snapshot(main=40), _declared)
    await hass.async_block_till_done()

    assert (
        "**Name of circuit-2**: the 20-30 ratings you copied from the SPAN app add up to more than the panel's "
        "service rating of 40 A, so they were removed."
    ) in _message(hass, entry)


async def test_removals_accumulate_while_the_notice_stands_and_reset_after_dismissal(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0, "circuit-4": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    for circuit_id in ratings:
        manager.set_package_ratings(circuit_id, _snapshot(ratings), [int(ratings[circuit_id]), 20])

    manager.reconcile(_snapshot({**ratings, "circuit-2": 30.0}), _declared)
    await hass.async_block_till_done()
    assert "circuit-2" in _message(hass, entry)
    assert "circuit-3" not in _message(hass, entry)

    manager.reconcile(_snapshot({**ratings, "circuit-2": 30.0, "circuit-3": 25.0}), _declared)
    await hass.async_block_till_done()
    assert "circuit-2" in _message(hass, entry)
    assert "circuit-3" in _message(hass, entry)

    async_dismiss(hass, _notification_id(entry))
    await hass.async_block_till_done()
    manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 25.0, "circuit-4": 35.0}), _declared)
    await hass.async_block_till_done()
    assert "circuit-2" not in _message(hass, entry)
    assert "circuit-3" not in _message(hass, entry)
    assert "circuit-4" in _message(hass, entry)


async def test_two_removals_before_the_notice_is_raised_are_both_kept(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The text is read off the loop, so a second removal can land before the first notice does.

    The read is held until both are in, which is the only way to make the
    interleaving certain: left to the executor it sometimes finishes between them.
    """
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_package_ratings("circuit-2", _snapshot(ratings), [20, 20])
    manager.set_package_ratings("circuit-3", _snapshot(ratings), [15, 20])
    release = threading.Event()

    def held(language: str, section: str) -> dict[str, str]:
        release.wait(timeout=5)
        return read_translations(language, section)

    with patch("custom_components.span_panel.package_rating_manager.notices.read_translations", held):
        manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 15.0}), _declared)
        manager.reconcile(_snapshot({"circuit-2": 30.0, "circuit-3": 25.0}), _declared)
        release.set()
        await hass.async_block_till_done()

    assert "circuit-2" in _message(hass, entry)
    assert "circuit-3" in _message(hass, entry)


async def test_a_user_change_while_the_text_is_read_raises_no_empty_notice(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The user copies the package again before the notice for its removal is raised."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    changed = _snapshot({"circuit-2": 30.0})
    release = threading.Event()

    def held(language: str, section: str) -> dict[str, str]:
        release.wait(timeout=5)
        return read_translations(language, section)

    with patch("custom_components.span_panel.package_rating_manager.notices.read_translations", held):
        manager.reconcile(changed, _declared)
        manager.set_package_ratings("circuit-2", changed, [30, 20])
        release.set()
        await hass.async_block_till_done()

    assert _notification_id(entry) not in hass.data.get("persistent_notification", {})
    assert (await _stored(hass, entry))["unacknowledged"] == {}


async def test_unacknowledged_removals_are_stored(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 30])
    manager.reconcile(_snapshot({}), _declared)  # the circuit leaves: removed, told
    await hass.async_block_till_done()

    stored = await _stored(hass, entry)
    assert stored["packages"] == {}
    assert stored["unacknowledged"] == {
        "circuit-2": {
            "circuit_id": "circuit-2",
            "label": "Name of circuit-2",
            "reason": "circuit_removed",
            "package_ratings_a": [20, 30],
            "panel_after_a": None,
            "service_a": 100.0,
        }
    }


@pytest.mark.parametrize("reason", get_args(ChangeReason.__value__))
async def test_a_stored_removal_of_every_reason_round_trips(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object], reason: str
) -> None:
    """A reason the loader does not accept would drop the line as malformed."""
    row = {
        "circuit_id": "circuit-2",
        "label": "Oven",
        "reason": reason,
        "package_ratings_a": [20, 30],
        "panel_after_a": 30.0,
        "service_a": 100.0,
    }
    hass_storage[_store_key(entry)] = {"version": 1, "data": {"packages": {}, "unacknowledged": {"circuit-2": row}}}
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    await manager.async_save()

    assert (await _stored(hass, entry))["unacknowledged"] == {"circuit-2": row}


_GOOD_CHANGE: dict[str, object] = {
    "circuit_id": "circuit-2",
    "label": "Oven",
    "reason": "circuit_removed",
    "package_ratings_a": [20, 30],
    "panel_after_a": None,
    "service_a": None,
}


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({**_GOOD_CHANGE, "reason": "panel_changed"}, id="a reason no longer given"),
        pytest.param({**_GOOD_CHANGE, "package_ratings_a": [20]}, id="one rating"),
        pytest.param({**_GOOD_CHANGE, "label": 7}, id="label not text"),
        pytest.param({**_GOOD_CHANGE, "panel_after_a": "30"}, id="panel rating not a number"),
        pytest.param({**_GOOD_CHANGE, "service_a": True}, id="service rating a bool"),
        pytest.param("nonsense", id="not a row"),
    ],
)
async def test_a_malformed_stored_removal_is_dropped(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, object], bad: object
) -> None:
    hass_storage[_store_key(entry)] = {
        "version": 1,
        "data": {"packages": {}, "unacknowledged": {"circuit-2": _GOOD_CHANGE, "circuit-3": bad}},
    }
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    await manager.async_save()

    assert (await _stored(hass, entry))["unacknowledged"] == {"circuit-2": _GOOD_CHANGE}


async def test_a_reloaded_manager_keeps_the_stored_removals_and_adds_a_new_one(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The stored removals round trip, reason included; `notices.async_restore` re-raises the notice."""
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_package_ratings("circuit-2", _snapshot(ratings), [20, 30])
    manager.set_package_ratings("circuit-3", _snapshot(ratings), [15, 20])
    manager.reconcile(_snapshot({"circuit-3": 15.0}), _declared)  # circuit-2 leaves
    await hass.async_block_till_done()

    reloaded = PackageRatingManager(hass, entry)
    await reloaded.async_load()
    reloaded.begin(_snapshot({"circuit-3": 25.0}), _DECLARED)  # circuit-3's panel rating changes
    await hass.async_block_till_done()

    message = _message(hass, entry)
    assert (
        "**Name of circuit-2**: this circuit is no longer on the panel, "
        "so the 20-30 ratings you copied from the SPAN app were removed."
    ) in message
    assert "**Name of circuit-3**: the rating your panel publishes for this circuit changed from 15 A to 25 A" in message


async def test_copying_again_drops_that_circuit_from_the_standing_notice(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The line says the copy was removed; once the user copies the package again it no longer holds."""
    ratings = {"circuit-2": 20.0, "circuit-3": 15.0}
    manager = await _begun(hass, entry, _snapshot(ratings))
    manager.set_package_ratings("circuit-2", _snapshot(ratings), [20, 20])
    manager.set_package_ratings("circuit-3", _snapshot(ratings), [15, 20])
    changed = _snapshot({"circuit-2": 30.0, "circuit-3": 25.0})
    manager.reconcile(changed, _declared)
    await hass.async_block_till_done()
    assert "circuit-2" in _message(hass, entry)

    manager.set_package_ratings("circuit-2", changed, [30, 20])
    await hass.async_block_till_done()

    assert "circuit-2" not in _message(hass, entry)
    assert "circuit-3" in _message(hass, entry)
    assert "circuit-2" not in (await _stored(hass, entry))["unacknowledged"]


async def test_copying_again_the_only_listed_circuit_withdraws_the_notice(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """With no line left the notice has nothing true to say, so it goes, as the download's list of removals does."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    changed = _snapshot({"circuit-2": 30.0})
    manager.reconcile(changed, _declared)
    await hass.async_block_till_done()
    assert is_standing(hass, entry, _NOTICE)

    manager.set_package_ratings("circuit-2", changed, [30, 20])
    await hass.async_block_till_done()

    assert not is_standing(hass, entry, _NOTICE)
    assert _notification_id(entry) not in hass.data["persistent_notification"]
    assert manager.diagnostics(changed)["unacknowledged"] == {}
    assert (await _stored(hass, entry))["unacknowledged"] == {}


async def test_clearing_a_removed_entry_leaves_its_line(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """There is nothing left to clear, and the line, that the copy was removed, still holds."""
    manager = await _begun(hass, entry)
    manager.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    changed = _snapshot({"circuit-2": 30.0})
    manager.reconcile(changed, _declared)
    await hass.async_block_till_done()

    manager.clear_package_ratings("circuit-2", changed)
    await hass.async_block_till_done()

    assert "circuit-2" in _message(hass, entry)
    assert list((await _stored(hass, entry))["unacknowledged"]) == ["circuit-2"]


async def test_two_panels_with_the_same_circuit_id_stay_independent(
    hass: HomeAssistant, entry: MockConfigEntry, other: MockConfigEntry
) -> None:
    first = await _begun(hass, entry)
    second = await _begun(hass, other)
    events = _events(hass)

    first.set_package_ratings("circuit-2", _snapshot(), [20, 20])
    await hass.async_block_till_done()

    assert first.resolve("circuit-2", _snapshot()).user_set
    assert not second.resolve("circuit-2", _snapshot()).user_set
    assert [event.data["config_entry_id"] for event in events] == [entry.entry_id]

    first.reconcile(_snapshot({}), _declared)
    await hass.async_block_till_done()
    assert is_standing(hass, entry, _NOTICE)
    assert not is_standing(hass, other, _NOTICE)
