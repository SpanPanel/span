"""Tests for the services that set and clear the package ratings copied from the SPAN app for a circuit."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import FieldMetadata, SpanPanelSnapshot
import voluptuous as vol

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.entity_resolver import resolve_circuit_id
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.runtime import SpanPanelRuntimeData
from custom_components.span_panel.services import (
    _async_register_package_rating_services,
    _build_clear_circuit_package_ratings_schema,
    _build_set_circuit_package_ratings_schema,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for

SERVICE_A = 100
_DECLARED = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}


def _snapshot(ratings: Mapping[str, float | None] | None = None, tabs: list[int] | None = None) -> SpanPanelSnapshot:
    ratings = {"circuit-2": 20.0} if ratings is None else ratings
    return SpanPanelSnapshotFactory.create(
        circuits={
            cid: SpanCircuitSnapshotFactory.create(
                circuit_id=cid,
                name=f"Name of {cid}",
                tabs=tabs if tabs is not None else [2],
                breaker_rating_a=rating,
                breaker_rating_declared=True,
            )
            for cid, rating in ratings.items()
        },
        main_breaker_rating_a=SERVICE_A,
    )


async def _loaded_panel(
    hass: HomeAssistant,
    unique_id: str,
    snapshot: SpanPanelSnapshot,
) -> tuple[MockConfigEntry, PackageRatingManager]:
    """Return a loaded entry whose coordinator holds `snapshot` and a begun manager."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=unique_id)
    entry.add_to_hass(hass)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.package_rating_manager = manager
    coordinator.current_monitor = None
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=f"{unique_id}-device",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry, manager


def _entity(hass: HomeAssistant, entry: MockConfigEntry, circuit_id: str) -> str:
    """Register one of the circuit's current sensors under `entry` and return its entity id."""
    registry = er.async_get(hass)
    created = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"span_{entry.unique_id}_{circuit_id}_current",
        config_entry=entry,
        suggested_object_id=f"{entry.unique_id}_{circuit_id}_current",
    )
    return created.entity_id


@pytest.fixture(autouse=True)
def _services(hass: HomeAssistant) -> None:
    _async_register_package_rating_services(hass)


async def _set(hass: HomeAssistant, **data: object) -> None:
    await hass.services.async_call(DOMAIN, "set_circuit_package_ratings", data, blocking=True)


async def _clear(hass: HomeAssistant, **data: object) -> None:
    await hass.services.async_call(DOMAIN, "clear_circuit_package_ratings", data, blocking=True)


class TestSchemas:
    """The set schema takes a list of whole-amp ratings of at least 1; how many is the manager's to judge."""

    def test_set_accepts_an_id_and_ratings(self) -> None:
        """Set accepts an id and the ratings."""
        schema = _build_set_circuit_package_ratings_schema()
        assert schema({"circuit_id": "circuit-2", "package_ratings_a": [20, 30]}) == {
            "circuit_id": "circuit-2",
            "package_ratings_a": [20, 30],
        }

    def test_set_accepts_a_config_entry_id(self) -> None:
        """Set accepts a config entry id."""
        schema = _build_set_circuit_package_ratings_schema()
        assert schema({"circuit_id": "c", "package_ratings_a": [1, 1], "config_entry_id": "e"})

    @pytest.mark.parametrize("ratings", [[], [20], [20, 20, 20]])
    def test_set_leaves_the_count_to_the_manager(self, ratings: list[int]) -> None:
        """A refusal for the count names what to enter, which a schema error cannot."""
        schema = _build_set_circuit_package_ratings_schema()
        assert schema({"circuit_id": "circuit-2", "package_ratings_a": ratings})["package_ratings_a"] == ratings

    @pytest.mark.parametrize("rating", [0, -1, "20", 20.5, None, True])
    def test_set_rejects_a_rating_that_is_not_a_positive_whole_number(self, rating: object) -> None:
        """Set rejects a rating that is not a positive whole number, a bool included."""
        schema = _build_set_circuit_package_ratings_schema()
        with pytest.raises(vol.Invalid):
            schema({"circuit_id": "circuit-2", "package_ratings_a": [20, rating]})

    @pytest.mark.parametrize("ratings", [20, "20-20", None])
    def test_set_rejects_ratings_that_are_not_a_list(self, ratings: object) -> None:
        """Set rejects ratings that are not a list."""
        schema = _build_set_circuit_package_ratings_schema()
        with pytest.raises(vol.Invalid):
            schema({"circuit_id": "circuit-2", "package_ratings_a": ratings})

    def test_set_rejects_a_missing_circuit(self) -> None:
        """Set rejects a missing circuit."""
        schema = _build_set_circuit_package_ratings_schema()
        with pytest.raises(vol.Invalid):
            schema({"package_ratings_a": [20, 20]})

    def test_set_rejects_missing_ratings(self) -> None:
        """Set rejects missing ratings."""
        schema = _build_set_circuit_package_ratings_schema()
        with pytest.raises(vol.Invalid):
            schema({"circuit_id": "circuit-2"})

    def test_clear_accepts_an_id_and_an_entry(self) -> None:
        """Clear accepts an id and an entry."""
        schema = _build_clear_circuit_package_ratings_schema()
        assert schema({"circuit_id": "circuit-2", "config_entry_id": "e"})

    def test_clear_rejects_a_missing_circuit(self) -> None:
        """Clear rejects a missing circuit."""
        schema = _build_clear_circuit_package_ratings_schema()
        with pytest.raises(vol.Invalid):
            schema({})


class TestSet:
    """set_circuit_package_ratings stores the copy a circuit's space is judged against."""

    async def test_by_entity_id(self, hass: HomeAssistant) -> None:
        """By entity id."""
        entry, manager = await _loaded_panel(hass, "sp3-001", _snapshot())
        entity_id = _entity(hass, entry, "circuit-2")

        await _set(hass, circuit_id=entity_id, package_ratings_a=[20, 30])

        assert manager.resolve("circuit-2", _snapshot()).judged_a == 50

    async def test_by_circuit_id(self, hass: HomeAssistant) -> None:
        """By circuit id."""
        _entry, manager = await _loaded_panel(hass, "sp3-001", _snapshot())

        await _set(hass, circuit_id="circuit-2", package_ratings_a=[20, 30])

        assert manager.resolve("circuit-2", _snapshot()).judged_a == 50

    async def test_works_with_monitoring_off(self, hass: HomeAssistant) -> None:
        """Works with monitoring off."""
        entry, manager = await _loaded_panel(hass, "sp3-001", _snapshot())
        assert entry.runtime_data.coordinator.current_monitor is None

        await _set(hass, circuit_id="circuit-2", package_ratings_a=[20, 30])

        assert manager.resolve("circuit-2", _snapshot()).judged_a == 50

    @pytest.mark.parametrize(
        ("snapshot", "circuit_id", "ratings", "key"),
        [
            (_snapshot(), "circuit-9", [20, 30], "package_ratings_circuit_unknown"),
            (_snapshot({"circuit-2": None}), "circuit-2", [20, 30], "package_ratings_not_arrived"),
            (_snapshot(), "circuit-2", [20, SERVICE_A], "package_ratings_above_service_rating"),
            (_snapshot(tabs=[2, 4, 6]), "circuit-2", [20, 30], "package_ratings_spaces"),
            (_snapshot(), "circuit-2", [20, 20, 20], "package_ratings_not_two"),
            (_snapshot(), "circuit-2", [30, 20], "package_ratings_first_not_panel"),
            (
                replace(_snapshot(), publishes_shared_meters=True),
                "circuit-2",
                [20, 30],
                "package_ratings_panel_describes_breakers",
            ),
        ],
    )
    async def test_a_refusal_reaches_the_caller(
        self,
        hass: HomeAssistant,
        snapshot: SpanPanelSnapshot,
        circuit_id: str,
        ratings: list[int],
        key: str,
    ) -> None:
        """A refusal reaches the caller."""
        await _loaded_panel(hass, "sp3-001", snapshot)

        with pytest.raises(ServiceValidationError) as raised:
            await _set(hass, circuit_id=circuit_id, package_ratings_a=ratings)

        assert raised.value.translation_domain == DOMAIN
        assert raised.value.translation_key == key

    async def test_a_service_rating_not_yet_arrived_refuses_as_not_arrived(self, hass: HomeAssistant) -> None:
        """Declared with no value yet: the refusal says to wait, not that the panel has none."""
        snapshot = SpanPanelSnapshotFactory.create(
            circuits={
                "circuit-2": SpanCircuitSnapshotFactory.create(
                    circuit_id="circuit-2", breaker_rating_a=20.0, breaker_rating_declared=True
                )
            },
            main_breaker_rating_a=None,
        )
        await _loaded_panel(hass, "sp3-001", snapshot)

        with pytest.raises(ServiceValidationError) as raised:
            await _set(hass, circuit_id="circuit-2", package_ratings_a=[20, 30])

        assert raised.value.translation_key == "package_ratings_not_arrived"


class TestClear:
    """clear_circuit_package_ratings removes a circuit's copy."""

    async def test_by_entity_id(self, hass: HomeAssistant) -> None:
        """By entity id."""
        entry, manager = await _loaded_panel(hass, "sp3-001", _snapshot())
        entity_id = _entity(hass, entry, "circuit-2")
        await _set(hass, circuit_id="circuit-2", package_ratings_a=[20, 30])

        await _clear(hass, circuit_id=entity_id)

        assert manager.resolve("circuit-2", _snapshot()).judged_a == 20

    async def test_by_circuit_id(self, hass: HomeAssistant) -> None:
        """By circuit id."""
        _entry, manager = await _loaded_panel(hass, "sp3-001", _snapshot())
        await _set(hass, circuit_id="circuit-2", package_ratings_a=[20, 30])

        await _clear(hass, circuit_id="circuit-2")

        assert manager.resolve("circuit-2", _snapshot()).judged_a == 20

    async def test_works_with_monitoring_off(self, hass: HomeAssistant) -> None:
        """Works with monitoring off."""
        _entry, manager = await _loaded_panel(hass, "sp3-001", _snapshot())
        await _set(hass, circuit_id="circuit-2", package_ratings_a=[20, 30])

        await _clear(hass, circuit_id="circuit-2")

        assert manager.resolve("circuit-2", _snapshot()).judged_a == 20

    async def test_an_entity_whose_circuit_has_left_is_refused(self, hass: HomeAssistant) -> None:
        """A circuit the panel no longer publishes has no copy to clear."""
        entry, _manager = await _loaded_panel(hass, "sp3-001", _snapshot())
        entity_id = _entity(hass, entry, "circuit-gone")

        with pytest.raises(ServiceValidationError) as raised:
            await _clear(hass, circuit_id=entity_id)

        assert raised.value.translation_key == "package_ratings_circuit_unknown"


class TestPanelChoice:
    """With two panels publishing the same circuit id, an entity's own panel is the one acted on."""

    async def _two_panels(
        self, hass: HomeAssistant
    ) -> tuple[MockConfigEntry, PackageRatingManager, MockConfigEntry, PackageRatingManager]:
        entry_a, manager_a = await _loaded_panel(hass, "sp3-001", _snapshot())
        entry_b, manager_b = await _loaded_panel(hass, "sp3-002", _snapshot())
        return entry_a, manager_a, entry_b, manager_b

    async def test_an_entity_of_panel_b_without_an_entry_lands_on_panel_b(
        self, hass: HomeAssistant
    ) -> None:
        """An entity of panel b without an entry lands on panel b."""
        _a, manager_a, entry_b, manager_b = await self._two_panels(hass)
        entity_id = _entity(hass, entry_b, "circuit-2")

        await _set(hass, circuit_id=entity_id, package_ratings_a=[20, 30])

        assert manager_b.resolve("circuit-2", _snapshot()).judged_a == 50
        assert manager_a.resolve("circuit-2", _snapshot()).judged_a == 20

    async def test_an_entity_cleared_without_an_entry_lands_on_its_panel(
        self, hass: HomeAssistant
    ) -> None:
        """An entity cleared without an entry lands on its panel."""
        _a, manager_a, entry_b, manager_b = await self._two_panels(hass)
        entity_id = _entity(hass, entry_b, "circuit-2")
        await _set(hass, circuit_id=entity_id, package_ratings_a=[20, 30])

        await _clear(hass, circuit_id=entity_id)

        assert manager_b.resolve("circuit-2", _snapshot()).judged_a == 20

    async def test_a_raw_id_goes_to_the_panel_the_call_names(self, hass: HomeAssistant) -> None:
        """A raw id goes to the panel the call names."""
        _a, manager_a, entry_b, manager_b = await self._two_panels(hass)

        await _set(
            hass, circuit_id="circuit-2", package_ratings_a=[20, 30], config_entry_id=entry_b.entry_id
        )

        assert manager_b.resolve("circuit-2", _snapshot()).judged_a == 50
        assert manager_a.resolve("circuit-2", _snapshot()).judged_a == 20

    @pytest.mark.parametrize(
        "service", ["set_circuit_package_ratings", "clear_circuit_package_ratings"]
    )
    async def test_an_entry_that_is_not_the_entitys_panel_is_refused(
        self, hass: HomeAssistant, service: str
    ) -> None:
        """An entry that is not the entitys panel is refused."""
        entry_a, manager_a, entry_b, manager_b = await self._two_panels(hass)
        entity_id = _entity(hass, entry_b, "circuit-2")
        data: dict[str, object] = {"circuit_id": entity_id, "config_entry_id": entry_a.entry_id}
        if service == "set_circuit_package_ratings":
            data["package_ratings_a"] = [20, 30]

        with pytest.raises(ServiceValidationError) as raised:
            await hass.services.async_call(DOMAIN, service, data, blocking=True)

        assert raised.value.translation_key == "monitoring_entity_on_another_panel"
        assert manager_a.resolve("circuit-2", _snapshot()).judged_a == 20
        assert manager_b.resolve("circuit-2", _snapshot()).judged_a == 20

    async def test_an_entity_whose_panel_is_not_loaded_says_so(self, hass: HomeAssistant) -> None:
        """An entity whose panel is not loaded says so."""
        entry_a, _manager_a = await _loaded_panel(hass, "sp3-001", _snapshot())
        entry_b = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-002")
        entry_b.add_to_hass(hass)
        entity_id = _entity(hass, entry_b, "circuit-2")

        with pytest.raises(ServiceValidationError) as raised:
            await _set(hass, circuit_id=entity_id, package_ratings_a=[20, 30])

        assert raised.value.translation_key == "favorite_panel_not_loaded"
        assert entry_a.state is ConfigEntryState.LOADED

    @pytest.mark.parametrize("service", ["set", "clear"])
    async def test_with_no_panel_loaded_the_call_says_so(
        self, hass: HomeAssistant, service: str
    ) -> None:
        """With no panel loaded either call says so, with the one message that fits both."""
        with pytest.raises(ServiceValidationError) as raised:
            if service == "set":
                await _set(hass, circuit_id="circuit-2", package_ratings_a=[20, 30])
            else:
                await _clear(hass, circuit_id="circuit-2")

        assert raised.value.translation_key == "package_ratings_panel_not_loaded"


class TestResolveCircuitId:
    """An entity resolves to the circuit its unique id names, among the ids given."""

    def test_one_of_this_integrations_entities(self, hass: HomeAssistant) -> None:
        """The circuit id is found among the ids given."""
        entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="sp3-001")
        entry.add_to_hass(hass)
        entity_id = _entity(hass, entry, "circuit-2")

        assert resolve_circuit_id(hass, entity_id, {"circuit-2"}) == "circuit-2"

    def test_another_integrations_entity_is_not_read(self, hass: HomeAssistant) -> None:
        """Its unique id could hold a segment equal to a short opaque id."""
        other = MockConfigEntry(domain="other", data={})
        other.add_to_hass(hass)
        created = er.async_get(hass).async_get_or_create(
            "sensor", "other", "other_circuit-2_x", config_entry=other
        )

        assert resolve_circuit_id(hass, created.entity_id, {"circuit-2"}) == created.entity_id

    def test_a_raw_id_is_returned_as_it_is(self, hass: HomeAssistant) -> None:
        """A name that is no entity is taken to be the circuit id."""
        assert resolve_circuit_id(hass, "circuit-2", {"circuit-2"}) == "circuit-2"
