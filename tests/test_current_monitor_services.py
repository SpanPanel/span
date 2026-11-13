"""Tests for current monitoring service registration and handling."""

from __future__ import annotations

from dataclasses import replace
from typing import Final
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import FieldMetadata, SpanPanelSnapshot
import voluptuous as vol

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.runtime import SpanPanelRuntimeData
from custom_components.span_panel.services import _async_register_monitoring_services

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for

_DECLARED: Final = {"panel.main_breaker_rating_a": FieldMetadata(unit="A", datatype="integer")}


class TestServiceRegistration:
    """Tests for monitoring service registration."""

    def test_set_circuit_threshold_service_schema_validates(self):
        """Service schema accepts valid circuit threshold input."""
        from custom_components.span_panel.__init__ import (
            _build_set_circuit_threshold_schema,
        )

        schema = _build_set_circuit_threshold_schema()
        result = schema({"circuit_id": "sensor.span_panel_kitchen_power", "spike_threshold_pct": 90})
        assert result["circuit_id"] == "sensor.span_panel_kitchen_power"
        assert result["spike_threshold_pct"] == 90

    def test_set_circuit_threshold_schema_rejects_missing_circuit_id(self):
        """Service schema rejects input without circuit_id."""
        from custom_components.span_panel.__init__ import (
            _build_set_circuit_threshold_schema,
        )

        schema = _build_set_circuit_threshold_schema()
        with pytest.raises(vol.MultipleInvalid):
            schema({"spike_threshold_pct": 90})

    def test_set_mains_threshold_service_schema_validates(self):
        """Service schema accepts valid mains threshold input."""
        from custom_components.span_panel.__init__ import (
            _build_set_mains_threshold_schema,
        )

        schema = _build_set_mains_threshold_schema()
        result = schema({"leg": "sensor.span_panel_upstream_l1_current", "spike_threshold_pct": 90})
        assert result["leg"] == "sensor.span_panel_upstream_l1_current"

    def test_set_mains_threshold_schema_accepts_legacy_leg_name(self):
        """Service schema accepts legacy leg name for backwards compatibility."""
        from custom_components.span_panel.__init__ import (
            _build_set_mains_threshold_schema,
        )

        schema = _build_set_mains_threshold_schema()
        result = schema({"leg": "upstream_l1", "spike_threshold_pct": 90})
        assert result["leg"] == "upstream_l1"


def _rated_panel(*, publishes_shared_meters: bool | None) -> SpanPanelSnapshot:
    """Return a panel with one rated circuit; `publishes_shared_meters` True describes its multi-load breakers."""
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="circuit-5", name="Synthetic Circuit", tabs=[5], breaker_rating_a=20
    )
    snapshot = SpanPanelSnapshotFactory.create(
        serial_number="sp3-offered-001", circuits={"circuit-5": circuit}, main_breaker_rating_a=100
    )
    return replace(snapshot, publishes_shared_meters=publishes_shared_meters)


async def _monitoring_status(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, live: SpanPanelSnapshot | None
) -> dict[str, object]:
    """Answer `get_monitoring_status` for a panel set up on `snapshot` whose coordinator holds `live`."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=snapshot.serial_number)
    entry.add_to_hass(hass)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    coordinator = MagicMock()
    coordinator.data = live
    coordinator.package_rating_manager = manager
    coordinator.current_monitor = CurrentMonitor(hass, entry, manager)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id="sp3-offered-001-device",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    _async_register_monitoring_services(hass)

    response = await hass.services.async_call(
        DOMAIN, "get_monitoring_status", {}, blocking=True, return_response=True
    )
    assert response is not None
    return dict(response)


class TestMonitoringStatusSaysWhetherThePanelTakesCopies:
    """The status root says, as the topology root does, whether the panel takes copied package ratings."""

    @pytest.mark.parametrize(
        ("publishes_shared_meters", "offered"),
        [
            pytest.param(None, True, id="declares no shared meters"),
            pytest.param(True, False, id="describes its breakers"),
        ],
    )
    async def test_the_root_carries_monitoring_rating_offered(
        self, hass: HomeAssistant, publishes_shared_meters: bool | None, offered: bool
    ) -> None:
        """Offered on a panel that does not describe its multi-load breakers, and only there."""
        snapshot = _rated_panel(publishes_shared_meters=publishes_shared_meters)

        status = await _monitoring_status(hass, snapshot, snapshot)

        assert status["enabled"] is True
        assert status["monitoring_rating_offered"] is offered

    async def test_not_offered_while_the_coordinator_has_no_snapshot(
        self, hass: HomeAssistant
    ) -> None:
        """With no snapshot there is nothing to say a copy is taken by, so none is offered."""
        snapshot = _rated_panel(publishes_shared_meters=None)

        status = await _monitoring_status(hass, snapshot, None)

        assert status["enabled"] is True
        assert status["monitoring_rating_offered"] is False
