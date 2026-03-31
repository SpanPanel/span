"""Tests for graph horizon service call handlers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.span_panel.const import DEFAULT_GRAPH_HORIZON


def _consume_coro(coro):
    """Consume a coroutine to avoid 'never awaited' warnings in tests."""
    if asyncio.iscoroutine(coro):
        coro.close()
    return coro


def _make_hass():
    hass = MagicMock()
    hass.async_create_task = MagicMock(side_effect=_consume_coro)
    return hass


def _make_runtime_data(hass, entry_id="test_entry"):
    """Create mock runtime data with a GraphHorizonManager."""
    from custom_components.span_panel.graph_horizon import GraphHorizonManager

    entry = MagicMock()
    entry.entry_id = entry_id

    manager = GraphHorizonManager(hass, entry)
    manager._store = MagicMock()
    manager._store.async_save = AsyncMock()
    manager._store.async_load = AsyncMock(return_value=None)

    coordinator = MagicMock()
    coordinator.graph_horizon_manager = manager

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator

    return runtime_data, manager


class TestSetGraphTimeHorizon:
    """Tests for the set_graph_time_horizon service."""

    @pytest.mark.asyncio
    async def test_set_global_horizon(self):
        from custom_components.span_panel.graph_horizon import GraphHorizonManager

        hass = _make_hass()
        runtime_data, manager = _make_runtime_data(hass)

        manager.set_global_horizon("1h")
        assert manager.get_global_horizon() == "1h"

    @pytest.mark.asyncio
    async def test_set_invalid_horizon_raises(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        with pytest.raises(ValueError):
            manager.set_global_horizon("invalid")


class TestSetCircuitGraphHorizon:
    """Tests for the set_circuit_graph_horizon service."""

    @pytest.mark.asyncio
    async def test_set_circuit_override(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        manager.set_circuit_horizon("c1", "1d")
        assert manager.get_effective_horizon("c1") == "1d"

    @pytest.mark.asyncio
    async def test_clear_circuit_override(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        manager.set_circuit_horizon("c1", "1d")
        manager.clear_circuit_horizon("c1")
        assert manager.get_effective_horizon("c1") == DEFAULT_GRAPH_HORIZON


class TestGetGraphSettings:
    """Tests for the get_graph_settings service."""

    @pytest.mark.asyncio
    async def test_returns_settings(self):
        hass = _make_hass()
        _, manager = _make_runtime_data(hass)

        manager.set_circuit_horizon("c1", "1M")
        result = manager.get_all_settings()

        assert result["global_horizon"] == DEFAULT_GRAPH_HORIZON
        assert result["circuits"]["c1"]["horizon"] == "1M"
        assert result["circuits"]["c1"]["has_override"] is True
