"""Tests for cross-panel favorites storage helpers and services."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.frontend import (
    async_get_favorites,
    async_set_favorite,
)
from custom_components.span_panel.services import _async_register_favorites_services


class _FakeStore:
    """In-memory stand-in for homeassistant.helpers.storage.Store.

    One shared backing dict keyed by storage key, so multiple Store(...) calls
    in the same test see a consistent view of the data.
    """

    _shared_state: dict[str, Any] = {}

    def __init__(self, _hass: Any, _version: int, key: str) -> None:
        self._key = key

    async def async_load(self) -> Any:
        return _FakeStore._shared_state.get(self._key)

    async def async_save(self, data: Any) -> None:
        _FakeStore._shared_state[self._key] = data

    @classmethod
    def reset(cls) -> None:
        cls._shared_state = {}

    @classmethod
    def preload(cls, key: str, data: Any) -> None:
        cls._shared_state[key] = data


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    _FakeStore.reset()


@pytest.fixture
def _patched_store() -> Any:
    with patch(
        "custom_components.span_panel.frontend.Store",
        _FakeStore,
    ):
        yield


def _panel_entry(circuits: list[str] | None = None, sub_devices: list[str] | None = None) -> dict[str, list[str]]:
    return {"circuits": circuits or [], "sub_devices": sub_devices or []}


class TestAsyncGetFavorites:
    """Tests for ``async_get_favorites`` helper."""

    @pytest.mark.asyncio
    async def test_empty_storage_returns_empty_dict(self, _patched_store: Any) -> None:
        hass = MagicMock()
        result = await async_get_favorites(hass)
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_stored_favorites_new_shape(self, _patched_store: Any) -> None:
        _FakeStore.preload(
            "span_panel_settings",
            {
                "show_panel": True,
                "favorites": {
                    "panel_a": {"circuits": ["c1", "c2"], "sub_devices": ["bess1"]},
                    "panel_b": {"circuits": ["c3"], "sub_devices": []},
                },
            },
        )
        hass = MagicMock()
        result = await async_get_favorites(hass)
        assert result == {
            "panel_a": _panel_entry(["c1", "c2"], ["bess1"]),
            "panel_b": _panel_entry(["c3"], []),
        }

    @pytest.mark.asyncio
    async def test_legacy_list_shape_is_circuits_only(self, _patched_store: Any) -> None:
        """Pre-existing favorites stored as flat lists migrate to circuits-only entries."""
        _FakeStore.preload(
            "span_panel_settings",
            {"favorites": {"panel_a": ["c1", "c2"], "panel_b": ["c3"]}},
        )
        hass = MagicMock()
        result = await async_get_favorites(hass)
        assert result == {
            "panel_a": _panel_entry(["c1", "c2"]),
            "panel_b": _panel_entry(["c3"]),
        }

    @pytest.mark.asyncio
    async def test_filters_invalid_shapes(self, _patched_store: Any) -> None:
        """Malformed entries (non-str values, missing kinds, empties) are dropped."""
        _FakeStore.preload(
            "span_panel_settings",
            {
                "favorites": {
                    "panel_a": {"circuits": ["c1", "", 42, "c2"], "sub_devices": "bad"},
                    "panel_b": "not-a-dict",
                    123: {"circuits": ["c3"]},  # type: ignore[dict-item]
                    "panel_empty": {"circuits": [], "sub_devices": []},
                }
            },
        )
        hass = MagicMock()
        result = await async_get_favorites(hass)
        assert result == {"panel_a": _panel_entry(["c1", "c2"], [])}

    @pytest.mark.asyncio
    async def test_tolerates_missing_favorites_key(self, _patched_store: Any) -> None:
        _FakeStore.preload(
            "span_panel_settings", {"show_panel": False, "panel_admin_only": True}
        )
        hass = MagicMock()
        result = await async_get_favorites(hass)
        assert result == {}


class TestAsyncSetFavorite:
    """Tests for ``async_set_favorite`` helper."""

    @pytest.mark.asyncio
    async def test_add_circuit_creates_panel_entry(self, _patched_store: Any) -> None:
        hass = MagicMock()
        result = await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        assert result == {"panel_a": _panel_entry(["c1"])}
        persisted = _FakeStore._shared_state["span_panel_settings"]
        assert persisted["favorites"] == {"panel_a": _panel_entry(["c1"])}

    @pytest.mark.asyncio
    async def test_add_subdevice_coexists_with_circuits(self, _patched_store: Any) -> None:
        hass = MagicMock()
        await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        result = await async_set_favorite(hass, "panel_a", "sub_devices", "bess1", True)
        assert result == {"panel_a": _panel_entry(["c1"], ["bess1"])}

    @pytest.mark.asyncio
    async def test_add_dedupes(self, _patched_store: Any) -> None:
        hass = MagicMock()
        await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        result = await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        assert result == {"panel_a": _panel_entry(["c1"])}

    @pytest.mark.asyncio
    async def test_remove_drops_empty_panel_key(self, _patched_store: Any) -> None:
        hass = MagicMock()
        await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        result = await async_set_favorite(hass, "panel_a", "circuits", "c1", False)
        assert result == {}
        persisted = _FakeStore._shared_state["span_panel_settings"]
        assert persisted["favorites"] == {}

    @pytest.mark.asyncio
    async def test_remove_keeps_other_kind_entries(self, _patched_store: Any) -> None:
        hass = MagicMock()
        await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        await async_set_favorite(hass, "panel_a", "sub_devices", "bess1", True)
        result = await async_set_favorite(hass, "panel_a", "circuits", "c1", False)
        assert result == {"panel_a": _panel_entry([], ["bess1"])}

    @pytest.mark.asyncio
    async def test_remove_of_unknown_is_noop(self, _patched_store: Any) -> None:
        hass = MagicMock()
        result = await async_set_favorite(hass, "panel_a", "circuits", "missing", False)
        assert result == {}

    @pytest.mark.asyncio
    async def test_preserves_sibling_settings(self, _patched_store: Any) -> None:
        """Favorites writes must not trample ``show_panel`` or ``panel_admin_only``."""
        _FakeStore.preload(
            "span_panel_settings", {"show_panel": False, "panel_admin_only": True}
        )
        hass = MagicMock()
        await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        persisted = _FakeStore._shared_state["span_panel_settings"]
        assert persisted["show_panel"] is False
        assert persisted["panel_admin_only"] is True
        assert persisted["favorites"] == {"panel_a": _panel_entry(["c1"])}

    @pytest.mark.asyncio
    async def test_unknown_kind_raises(self, _patched_store: Any) -> None:
        hass = MagicMock()
        with pytest.raises(ValueError):
            await async_set_favorite(hass, "panel_a", "bogus", "c1", True)

    @pytest.mark.asyncio
    async def test_set_migrates_legacy_shape_in_place(self, _patched_store: Any) -> None:
        """Touching a panel with legacy ``[uuid]`` storage rewrites it as the canonical dict."""
        _FakeStore.preload(
            "span_panel_settings",
            {"favorites": {"panel_a": ["c1", "c2"]}},
        )
        hass = MagicMock()
        # No new circuit added; the same uuid we already had.
        result = await async_set_favorite(hass, "panel_a", "circuits", "c1", True)
        assert result == {"panel_a": _panel_entry(["c1", "c2"])}
        persisted = _FakeStore._shared_state["span_panel_settings"]["favorites"]
        # Persisted shape is now the nested dict, not the legacy list.
        assert persisted == {"panel_a": _panel_entry(["c1", "c2"])}

    @pytest.mark.asyncio
    async def test_concurrent_set_favorites_does_not_drop_writes(
        self, _patched_store: Any
    ) -> None:
        """Two parallel adds must both end up in storage (lock prevents lost writes)."""
        import asyncio

        hass = MagicMock()
        # Run both adds concurrently; each call goes through the lock so the
        # second write picks up the first's mutation.
        results = await asyncio.gather(
            async_set_favorite(hass, "panel_a", "circuits", "c1", True),
            async_set_favorite(hass, "panel_a", "circuits", "c2", True),
        )
        # Both calls return the favorites map at the time they wrote;
        # the FINAL persisted state must contain both circuits.
        persisted = _FakeStore._shared_state["span_panel_settings"]["favorites"]
        assert sorted(persisted["panel_a"]["circuits"]) == ["c1", "c2"]
        # Each call's returned map is at least non-empty.
        for r in results:
            assert "panel_a" in r


def _capture_registered_handlers(hass: MagicMock) -> dict[str, Any]:
    """Run ``_async_register_favorites_services`` and return a name->handler map."""
    handlers: dict[str, Any] = {}
    schemas: dict[str, Any] = {}
    responses: dict[str, Any] = {}

    def _register(
        domain: str,
        service: str,
        handler: Any,
        schema: Any | None = None,
        supports_response: Any = SupportsResponse.NONE,
    ) -> None:
        assert domain == DOMAIN
        handlers[service] = handler
        schemas[service] = schema
        responses[service] = supports_response

    hass.services = MagicMock()
    hass.services.async_register = MagicMock(side_effect=_register)
    _async_register_favorites_services(hass)
    return {"handlers": handlers, "schemas": schemas, "responses": responses}


def _make_service_call(data: dict[str, Any]) -> MagicMock:
    call = MagicMock()
    call.data = data
    return call


def _make_entity_entry(
    *,
    platform: str = DOMAIN,
    unique_id: str = "span_sp3-242424_abcdef0123456789abcdef0123456789_power",
    device_id: str | None = "d_main",
) -> MagicMock:
    entry = MagicMock()
    entry.platform = platform
    entry.unique_id = unique_id
    entry.device_id = device_id
    return entry


def _make_device_entry(
    *,
    device_id: str = "d_main",
    identifiers: set[tuple[str, str]] | None = None,
    via_device_id: str | None = None,
) -> MagicMock:
    device = MagicMock()
    device.id = device_id
    device.identifiers = identifiers if identifiers is not None else {(DOMAIN, "serial_a")}
    device.via_device_id = via_device_id
    return device


def _patch_registries(entity: MagicMock | None, device: MagicMock | None) -> Any:
    """Patch services.er.async_get and services.dr.async_get for a single call.

    Either registry returns the given object from ``async_get`` regardless of id.
    """
    entity_reg = MagicMock()
    entity_reg.async_get = MagicMock(return_value=entity)
    device_reg = MagicMock()
    device_reg.async_get = MagicMock(return_value=device)

    return patch.multiple(
        "custom_components.span_panel.services",
        er=MagicMock(async_get=MagicMock(return_value=entity_reg)),
        dr=MagicMock(async_get=MagicMock(return_value=device_reg)),
    )


def _patch_registries_for_subdevice(
    entity: MagicMock,
    sub_device: MagicMock,
    parent_panel: MagicMock,
) -> Any:
    """Stub registries so device_registry.async_get returns sub_device for the
    entity's device_id and parent_panel for the via_device_id lookup."""
    entity_reg = MagicMock()
    entity_reg.async_get = MagicMock(return_value=entity)

    device_reg = MagicMock()
    def _device_lookup(device_id: str) -> MagicMock | None:
        if device_id == sub_device.id:
            return sub_device
        if device_id == parent_panel.id:
            return parent_panel
        return None
    device_reg.async_get = MagicMock(side_effect=_device_lookup)

    return patch.multiple(
        "custom_components.span_panel.services",
        er=MagicMock(async_get=MagicMock(return_value=entity_reg)),
        dr=MagicMock(async_get=MagicMock(return_value=device_reg)),
    )


class TestFavoritesServiceHandlers:
    """Tests for the ``get_favorites`` / ``add_favorite`` / ``remove_favorite`` service handlers."""

    @pytest.mark.asyncio
    async def test_get_favorites_returns_current_map(self, _patched_store: Any) -> None:
        _FakeStore.preload(
            "span_panel_settings",
            {"favorites": {"panel_a": {"circuits": ["c1"], "sub_devices": []}}},
        )
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["get_favorites"]

        result = await handler(_make_service_call({}))
        assert result == {
            "favorites": {"panel_a": _panel_entry(["c1"])},
        }
        assert registered["responses"]["get_favorites"] is SupportsResponse.ONLY

    @pytest.mark.asyncio
    async def test_add_favorite_rejects_unknown_entity(
        self, _patched_store: Any
    ) -> None:
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        with _patch_registries(entity=None, device=None):
            with pytest.raises(ServiceValidationError):
                await handler(_make_service_call({"entity_id": "sensor.unknown"}))

    @pytest.mark.asyncio
    async def test_add_favorite_rejects_non_span_platform(
        self, _patched_store: Any
    ) -> None:
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        foreign_entity = _make_entity_entry(platform="other_domain")
        with _patch_registries(entity=foreign_entity, device=None):
            with pytest.raises(ServiceValidationError):
                await handler(_make_service_call({"entity_id": "sensor.other_power"}))

    @pytest.mark.asyncio
    async def test_add_favorite_accepts_sub_device_entity(
        self, _patched_store: Any
    ) -> None:
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        entity = _make_entity_entry(
            unique_id="span_sp3-242424_storage_battery_percentage",
            device_id="d_bess",
        )
        sub_device = _make_device_entry(
            device_id="d_bess",
            identifiers={(DOMAIN, "serial_a_bess")},
            via_device_id="d_main",
        )
        parent = _make_device_entry(
            device_id="d_main",
            identifiers={(DOMAIN, "serial_a")},
            via_device_id=None,
        )

        with _patch_registries_for_subdevice(entity, sub_device, parent):
            result = await handler(
                _make_service_call({"entity_id": "sensor.battery_level"})
            )

        assert result == {"favorites": {"d_main": _panel_entry([], ["d_bess"])}}

    @pytest.mark.asyncio
    async def test_add_favorite_rejects_entity_without_uuid_in_unique_id(
        self, _patched_store: Any
    ) -> None:
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        # Panel-level sensor (no circuit uuid segment in unique_id).
        entity = _make_entity_entry(unique_id="span_sp3-242424_instantGridPowerW")
        device = _make_device_entry()

        with _patch_registries(entity=entity, device=device):
            with pytest.raises(ServiceValidationError):
                await handler(_make_service_call({"entity_id": "sensor.panel_power"}))

    @pytest.mark.asyncio
    async def test_add_favorite_persists_and_returns_map(
        self, _patched_store: Any
    ) -> None:
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        circuit_uuid = "abcdef0123456789abcdef0123456789"
        entity = _make_entity_entry(
            unique_id=f"span_sp3-242424_{circuit_uuid}_power",
            device_id="d_main",
        )
        device = _make_device_entry(device_id="d_main")

        with _patch_registries(entity=entity, device=device):
            result = await handler(
                _make_service_call({"entity_id": "sensor.kitchen_power"})
            )

        assert result == {"favorites": {"d_main": _panel_entry([circuit_uuid])}}
        assert _FakeStore._shared_state["span_panel_settings"]["favorites"] == {
            "d_main": _panel_entry([circuit_uuid])
        }

    @pytest.mark.asyncio
    async def test_remove_favorite_resolves_via_entity_registry(
        self, _patched_store: Any
    ) -> None:
        circuit_uuid = "abcdef0123456789abcdef0123456789"
        _FakeStore.preload(
            "span_panel_settings",
            {"favorites": {"d_main": _panel_entry([circuit_uuid])}},
        )
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["remove_favorite"]

        entity = _make_entity_entry(
            unique_id=f"span_sp3-242424_{circuit_uuid}_power",
            device_id="d_main",
        )
        device = _make_device_entry(device_id="d_main")

        with _patch_registries(entity=entity, device=device):
            result = await handler(
                _make_service_call({"entity_id": "sensor.kitchen_power"})
            )

        assert result == {"favorites": {}}

    def test_mutation_responses_are_optional(self, _patched_store: Any) -> None:
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        assert registered["responses"]["add_favorite"] is SupportsResponse.OPTIONAL
        assert registered["responses"]["remove_favorite"] is SupportsResponse.OPTIONAL

    @pytest.mark.asyncio
    async def test_add_favorite_rejects_entity_with_no_device_id(
        self, _patched_store: Any
    ) -> None:
        """Entities with no ``device_id`` are not favoritable."""
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        orphan = _make_entity_entry(device_id=None)
        with _patch_registries(entity=orphan, device=None):
            with pytest.raises(ServiceValidationError):
                await handler(_make_service_call({"entity_id": "sensor.orphan"}))

    @pytest.mark.asyncio
    async def test_add_favorite_rejects_subdevice_with_non_span_parent(
        self, _patched_store: Any
    ) -> None:
        """Sub-device whose ``via_device_id`` does NOT point at a SPAN panel."""
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        entity = _make_entity_entry(device_id="d_sub")
        sub_device = _make_device_entry(
            device_id="d_sub",
            identifiers={(DOMAIN, "serial_a_bess")},
            via_device_id="d_foreign",
        )
        # Parent exists but isn't a SPAN device (different domain identifier).
        foreign_parent = _make_device_entry(
            device_id="d_foreign",
            identifiers={("other_domain", "xyz")},
            via_device_id=None,
        )

        with _patch_registries_for_subdevice(entity, sub_device, foreign_parent):
            with pytest.raises(ServiceValidationError):
                await handler(
                    _make_service_call({"entity_id": "sensor.bess_under_foreign"})
                )

    @pytest.mark.asyncio
    async def test_add_favorite_rejects_subdevice_with_missing_parent(
        self, _patched_store: Any
    ) -> None:
        """Sub-device whose ``via_device_id`` references a missing device."""
        hass = MagicMock()
        registered = _capture_registered_handlers(hass)
        handler = registered["handlers"]["add_favorite"]

        entity = _make_entity_entry(device_id="d_sub")
        sub_device = _make_device_entry(
            device_id="d_sub",
            identifiers={(DOMAIN, "serial_a_bess")},
            via_device_id="d_missing",
        )

        # Stub a registry where the parent lookup returns None.
        entity_reg = MagicMock()
        entity_reg.async_get = MagicMock(return_value=entity)

        device_reg = MagicMock()
        def _device_lookup(device_id: str) -> MagicMock | None:
            return sub_device if device_id == "d_sub" else None
        device_reg.async_get = MagicMock(side_effect=_device_lookup)

        with patch.multiple(
            "custom_components.span_panel.services",
            er=MagicMock(async_get=MagicMock(return_value=entity_reg)),
            dr=MagicMock(async_get=MagicMock(return_value=device_reg)),
        ):
            with pytest.raises(ServiceValidationError):
                await handler(
                    _make_service_call({"entity_id": "sensor.bess_orphaned"})
                )
