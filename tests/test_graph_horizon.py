"""Tests for the GraphHorizonManager class."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.span_panel.const import (
    DEFAULT_GRAPH_HORIZON,
    VALID_GRAPH_HORIZONS,
)
from custom_components.span_panel.graph_horizon import GraphHorizonManager


def _make_hass():
    """Create a minimal mock hass object."""
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=lambda c: c)
    return hass


def _make_manager(hass=None, entry_id="test_entry"):
    """Create a GraphHorizonManager with mocked hass and entry."""
    if hass is None:
        hass = _make_hass()
    entry = MagicMock()
    entry.entry_id = entry_id
    return GraphHorizonManager(hass, entry)


class TestGlobalHorizon:
    """Tests for global horizon get/set."""

    def test_default_global_horizon(self):
        manager = _make_manager()
        assert manager.get_global_horizon() == DEFAULT_GRAPH_HORIZON

    def test_set_global_horizon(self):
        manager = _make_manager()
        manager.set_global_horizon("1h")
        assert manager.get_global_horizon() == "1h"

    def test_set_invalid_horizon_raises(self):
        manager = _make_manager()
        with pytest.raises(ValueError, match="Invalid graph horizon"):
            manager.set_global_horizon("2h")

    def test_set_global_prunes_matching_overrides(self):
        """When global changes to match an override, the override is removed."""
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1h")
        assert manager.get_effective_horizon("circuit_1") == "1h"
        manager.set_global_horizon("1h")
        assert "circuit_1" not in manager._circuit_overrides


class TestCircuitOverrides:
    """Tests for per-circuit horizon overrides."""

    def test_effective_horizon_returns_global_when_no_override(self):
        manager = _make_manager()
        assert manager.get_effective_horizon("circuit_1") == DEFAULT_GRAPH_HORIZON

    def test_set_circuit_override(self):
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1d")
        assert manager.get_effective_horizon("circuit_1") == "1d"

    def test_set_circuit_invalid_horizon_raises(self):
        manager = _make_manager()
        with pytest.raises(ValueError, match="Invalid graph horizon"):
            manager.set_circuit_horizon("circuit_1", "bad")

    def test_set_circuit_matching_global_removes_override(self):
        """Setting a circuit to the global value removes the override."""
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1h")
        assert "circuit_1" in manager._circuit_overrides
        manager.set_circuit_horizon("circuit_1", DEFAULT_GRAPH_HORIZON)
        assert "circuit_1" not in manager._circuit_overrides

    def test_clear_circuit_override(self):
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1d")
        manager.clear_circuit_horizon("circuit_1")
        assert manager.get_effective_horizon("circuit_1") == DEFAULT_GRAPH_HORIZON

    def test_clear_nonexistent_override_is_noop(self):
        manager = _make_manager()
        manager.clear_circuit_horizon("nonexistent")  # should not raise


class TestGetAllSettings:
    """Tests for get_all_settings output."""

    def test_returns_global_and_empty_circuits(self):
        manager = _make_manager()
        settings = manager.get_all_settings()
        assert settings["global_horizon"] == DEFAULT_GRAPH_HORIZON
        assert settings["circuits"] == {}

    def test_returns_overrides_with_has_override_flag(self):
        manager = _make_manager()
        manager.set_circuit_horizon("circuit_1", "1M")
        settings = manager.get_all_settings()
        assert settings["circuits"]["circuit_1"] == {
            "horizon": "1M",
            "has_override": True,
        }


class TestStoragePersistence:
    """Tests for async_load and async_save."""

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self):
        hass = _make_hass()
        manager = _make_manager(hass)
        manager.set_global_horizon("1d")
        manager.set_circuit_horizon("c1", "1M")
        manager.set_circuit_horizon("c2", "1h")

        saved_data = {}

        async def fake_save(data):
            saved_data.update(data)

        manager._store = MagicMock()
        manager._store.async_save = AsyncMock(side_effect=fake_save)
        await manager.async_save()

        assert saved_data["global_horizon"] == "1d"
        assert saved_data["circuit_overrides"] == {"c1": "1M", "c2": "1h"}

        manager2 = _make_manager(hass)
        manager2._store = MagicMock()
        manager2._store.async_load = AsyncMock(return_value=saved_data)
        await manager2.async_load()

        assert manager2.get_global_horizon() == "1d"
        assert manager2.get_effective_horizon("c1") == "1M"
        assert manager2.get_effective_horizon("c2") == "1h"

    @pytest.mark.asyncio
    async def test_load_handles_no_existing_data(self):
        hass = _make_hass()
        manager = _make_manager(hass)
        manager._store = MagicMock()
        manager._store.async_load = AsyncMock(return_value=None)
        await manager.async_load()
        assert manager.get_global_horizon() == DEFAULT_GRAPH_HORIZON
        assert manager._circuit_overrides == {}
