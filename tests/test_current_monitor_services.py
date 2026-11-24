"""Tests for current monitoring service registration and handling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Final
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import probatio as vol
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import WebSocketGenerator
from span_panel_api import FieldMetadata, SpanPanelSnapshot

from custom_components.span_panel.const import DOMAIN
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.current_monitor import CurrentMonitor
from custom_components.span_panel.options import SPIKE_THRESHOLD_PCT
from custom_components.span_panel.package_rating_manager import PackageRatingManager
from custom_components.span_panel.runtime import SpanPanelRuntimeData
from custom_components.span_panel.services import (
    _async_register_monitoring_services,
    _async_register_package_rating_services,
)

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory, pv_binding_for
from .helpers import register_panel_device

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


async def _load_panel(
    hass: HomeAssistant, snapshot: SpanPanelSnapshot, *, monitored: bool = True
) -> MockConfigEntry:
    """Return the entry of a panel set up on, and still holding, `snapshot`, with monitoring on or off."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=snapshot.serial_number)
    entry.add_to_hass(hass)
    manager = PackageRatingManager(hass, entry)
    await manager.async_load()
    manager.begin(snapshot, _DECLARED)
    coordinator = MagicMock()
    coordinator.data = snapshot
    coordinator.package_rating_manager = manager
    # Awaited when the harness unloads the entry at teardown.
    coordinator.async_shutdown = AsyncMock()
    coordinator.current_monitor = (
        CurrentMonitor(hass, entry, manager, snapshot) if monitored else None
    )
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=coordinator,
        panel_device_id=register_panel_device(hass, entry, snapshot.serial_number),
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(snapshot),
        setup_snapshot=snapshot,
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


async def _ask_for_status(hass: HomeAssistant, **fields: str) -> dict[str, object]:
    """Call `get_monitoring_status` with `fields` and return its answer."""
    response = await hass.services.async_call(
        DOMAIN, "get_monitoring_status", fields, blocking=True, return_response=True
    )
    assert response is not None
    return dict(response)


async def _monitoring_status(hass: HomeAssistant, snapshot: SpanPanelSnapshot) -> dict[str, object]:
    """Answer `get_monitoring_status` for a panel set up on, and still holding, `snapshot`."""
    await _load_panel(hass, snapshot)
    _async_register_monitoring_services(hass)
    return await _ask_for_status(hass)


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

        status = await _monitoring_status(hass, snapshot)

        assert status["enabled"] is True
        assert status["monitoring_rating_offered"] is offered


def _unloaded_entry(
    hass: HomeAssistant, state: ConfigEntryState, *, disabled: bool = False
) -> MockConfigEntry:
    """Return a SPAN Panel entry Home Assistant holds in `state`, which is not loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        unique_id="sp3-unloaded-001",
        disabled_by=ConfigEntryDisabler.USER if disabled else None,
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, state)
    return entry


def _another_integrations_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a loaded config entry that belongs to another integration."""
    entry = MockConfigEntry(domain="synthetic_other", data={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


class TestMonitoringStatusRefusesAPanelThatIsNotLoaded:
    """A panel that is not loaded is refused, never reported as having monitoring off.

    A script asking while Home Assistant starts, or while the integration retries
    a panel it cannot reach, was told `{"enabled": False}` of a panel whose
    monitoring is on. The card waits for the entry to load on exactly this
    refusal: Core's `service_validation_error`, under this integration's
    translation domain and the `monitoring_panel_not_loaded` key.
    """

    @pytest.mark.parametrize(
        ("state", "disabled"),
        [
            pytest.param(ConfigEntryState.NOT_LOADED, False, id="not loaded"),
            pytest.param(ConfigEntryState.NOT_LOADED, True, id="disabled"),
            pytest.param(ConfigEntryState.SETUP_IN_PROGRESS, False, id="setting up"),
            pytest.param(ConfigEntryState.SETUP_RETRY, False, id="retrying setup"),
            pytest.param(ConfigEntryState.SETUP_ERROR, False, id="setup failed"),
        ],
    )
    async def test_an_entry_that_is_not_loaded_is_refused(
        self, hass: HomeAssistant, state: ConfigEntryState, disabled: bool
    ) -> None:
        """The entry the call names is refused while it is not loaded, whatever keeps it so."""
        entry = _unloaded_entry(hass, state, disabled=disabled)
        _async_register_monitoring_services(hass)

        with pytest.raises(ServiceValidationError) as refused:
            await _ask_for_status(hass, config_entry_id=entry.entry_id)

        assert (refused.value.translation_domain, refused.value.translation_key) == (
            DOMAIN,
            "monitoring_panel_not_loaded",
        )

    async def test_an_entry_that_is_not_loaded_is_refused_while_another_panel_is_loaded(
        self, hass: HomeAssistant
    ) -> None:
        """The panel the call names decides: another panel being loaded answers nothing for it."""
        await _load_panel(hass, _rated_panel(publishes_shared_meters=None))
        entry = _unloaded_entry(hass, ConfigEntryState.SETUP_RETRY)
        _async_register_monitoring_services(hass)

        with pytest.raises(ServiceValidationError) as refused:
            await _ask_for_status(hass, config_entry_id=entry.entry_id)

        assert refused.value.translation_key == "monitoring_panel_not_loaded"

    @pytest.mark.parametrize(
        "configured", [False, True], ids=["no entry", "an entry retrying setup"]
    )
    async def test_a_call_naming_no_entry_is_refused_while_no_panel_is_loaded(
        self, hass: HomeAssistant, configured: bool
    ) -> None:
        """With no entry named and none loaded, there is no panel to answer for."""
        if configured:
            _unloaded_entry(hass, ConfigEntryState.SETUP_RETRY)
        _async_register_monitoring_services(hass)

        with pytest.raises(ServiceValidationError) as refused:
            await _ask_for_status(hass)

        assert (refused.value.translation_domain, refused.value.translation_key) == (
            DOMAIN,
            "monitoring_panel_not_loaded",
        )

    @pytest.mark.parametrize(
        "named", ["no-such-entry", None], ids=["an id no entry has", "another integration's entry"]
    )
    async def test_an_entry_id_that_is_no_panels_is_refused_as_unknown(
        self, hass: HomeAssistant, named: str | None
    ) -> None:
        """An id that names no SPAN Panel's entry is refused as such, not as a panel still loading.

        A loaded panel is there too, so the refusal is about the id the call named.
        """
        await _load_panel(hass, _rated_panel(publishes_shared_meters=None))
        entry_id = named if named is not None else _another_integrations_entry(hass).entry_id
        _async_register_monitoring_services(hass)

        with pytest.raises(ServiceValidationError) as refused:
            await _ask_for_status(hass, config_entry_id=entry_id)

        assert (
            refused.value.translation_domain,
            refused.value.translation_key,
            refused.value.translation_placeholders,
        ) == (DOMAIN, "monitoring_panel_unknown", {"config_entry_id": entry_id})

    async def test_a_loaded_panel_with_monitoring_off_still_says_so(
        self, hass: HomeAssistant
    ) -> None:
        """Only a loaded panel can say its monitoring is off."""
        entry = await _load_panel(hass, _rated_panel(publishes_shared_meters=None), monitored=False)
        _async_register_monitoring_services(hass)

        assert await _ask_for_status(hass, config_entry_id=entry.entry_id) == {"enabled": False}
        assert await _ask_for_status(hass) == {"enabled": False}

    async def test_a_loaded_panel_with_monitoring_on_answers_its_status(
        self, hass: HomeAssistant
    ) -> None:
        """A loaded, monitored panel answers its status, named or not."""
        entry = await _load_panel(hass, _rated_panel(publishes_shared_meters=None))
        _async_register_monitoring_services(hass)

        for status in (
            await _ask_for_status(hass, config_entry_id=entry.entry_id),
            await _ask_for_status(hass),
        ):
            assert status["enabled"] is True
            circuits = status["circuits"]
            assert isinstance(circuits, dict)
            assert [c["name"] for c in circuits.values() if isinstance(c, dict)] == [
                "Synthetic Circuit"
            ]

    async def test_the_card_reads_the_refusal_over_the_websocket(
        self, hass: HomeAssistant, hass_ws_client: WebSocketGenerator
    ) -> None:
        """The refusal reaches a WebSocket caller as the contract the card waits on."""
        entry = _unloaded_entry(hass, ConfigEntryState.SETUP_RETRY)
        _async_register_monitoring_services(hass)
        client = await hass_ws_client(hass)

        await client.send_json_auto_id(
            {
                "type": "call_service",
                "domain": DOMAIN,
                "service": "get_monitoring_status",
                "service_data": {"config_entry_id": entry.entry_id},
                "return_response": True,
            }
        )
        reply = await client.receive_json()

        assert reply["success"] is False
        error = reply["error"]
        assert (error["code"], error["translation_domain"], error["translation_key"]) == (
            "service_validation_error",
            "span_panel",
            "monitoring_panel_not_loaded",
        )


_WRITES: Final[list[Any]] = [
    pytest.param("set_global_monitoring", {"enabled": True}, id="turn monitoring on"),
    pytest.param("set_global_monitoring", {"enabled": False}, id="turn monitoring off"),
    pytest.param("set_global_monitoring", {SPIKE_THRESHOLD_PCT: 90}, id="change settings"),
    pytest.param("test_notification", {}, id="test notification"),
    pytest.param(
        "set_circuit_threshold",
        {"circuit_id": "circuit-5", SPIKE_THRESHOLD_PCT: 90},
        id="set circuit",
    ),
    pytest.param("clear_circuit_threshold", {"circuit_id": "circuit-5"}, id="clear circuit"),
    pytest.param(
        "set_mains_threshold", {"leg": "upstream_l1", SPIKE_THRESHOLD_PCT: 90}, id="set mains"
    ),
    pytest.param("clear_mains_threshold", {"leg": "upstream_l1"}, id="clear mains"),
    pytest.param(
        "set_circuit_package_ratings",
        {"circuit_id": "circuit-5", "package_ratings_a": [20, 30]},
        id="copy ratings",
    ),
    pytest.param("clear_circuit_package_ratings", {"circuit_id": "circuit-5"}, id="clear ratings"),
]
"""Every monitoring write, with data its schema accepts, naming a raw circuit id or leg."""


def _register_every_monitoring_service(hass: HomeAssistant) -> None:
    _async_register_monitoring_services(hass)
    _async_register_package_rating_services(hass)


async def _refusal(
    hass: HomeAssistant, service: str, data: Mapping[str, object]
) -> ServiceValidationError:
    """Call `service` with `data` and return the refusal it must raise."""
    with pytest.raises(ServiceValidationError) as refused:
        await hass.services.async_call(DOMAIN, service, dict(data), blocking=True)
    return refused.value


class TestMonitoringWritesRefuseAPanelThatIsNotLoaded:
    """A monitoring write says the panel is not loaded, or is no panel, never that monitoring is off.

    `monitoring_not_enabled` is left to a loaded panel whose monitoring is off, so
    a caller can tell a panel still loading from one it has to turn monitoring on
    for.
    """

    @pytest.mark.parametrize(("service", "data"), _WRITES)
    async def test_a_write_to_a_panel_that_is_not_loaded_is_refused(
        self, hass: HomeAssistant, service: str, data: Mapping[str, object]
    ) -> None:
        """The panel the call names decides, though another panel is loaded with monitoring on."""
        await _load_panel(hass, _rated_panel(publishes_shared_meters=None))
        entry = _unloaded_entry(hass, ConfigEntryState.SETUP_RETRY)
        _register_every_monitoring_service(hass)

        refusal = await _refusal(hass, service, {**data, "config_entry_id": entry.entry_id})

        assert (refusal.translation_domain, refusal.translation_key) == (
            DOMAIN,
            "monitoring_panel_not_loaded",
        )

    @pytest.mark.parametrize(("service", "data"), _WRITES)
    async def test_a_write_with_no_panel_loaded_is_refused(
        self, hass: HomeAssistant, service: str, data: Mapping[str, object]
    ) -> None:
        """With no entry named and none loaded, there is no panel to change."""
        _unloaded_entry(hass, ConfigEntryState.SETUP_RETRY)
        _register_every_monitoring_service(hass)

        refusal = await _refusal(hass, service, data)

        assert refusal.translation_key == "monitoring_panel_not_loaded"

    @pytest.mark.parametrize(("service", "data"), _WRITES)
    async def test_a_write_naming_an_entry_that_is_no_panels_is_refused_as_unknown(
        self, hass: HomeAssistant, service: str, data: Mapping[str, object]
    ) -> None:
        """An id no SPAN Panel's entry has is refused as such, though a panel is loaded."""
        await _load_panel(hass, _rated_panel(publishes_shared_meters=None))
        _register_every_monitoring_service(hass)

        refusal = await _refusal(hass, service, {**data, "config_entry_id": "no-such-entry"})

        assert (refusal.translation_key, refusal.translation_placeholders) == (
            "monitoring_panel_unknown",
            {"config_entry_id": "no-such-entry"},
        )

    async def test_turning_monitoring_off_on_a_panel_that_is_not_loaded_leaves_it_on(
        self, hass: HomeAssistant, hass_storage: dict[str, Any]
    ) -> None:
        """Refused rather than answered with success, since nothing is turned off.

        The panel's stored monitoring is still on, so it monitors once it loads,
        which a success would have told the caller it would not.
        """
        entry = _unloaded_entry(hass, ConfigEntryState.SETUP_RETRY)
        hass_storage[f"span_panel_current_monitor.{entry.entry_id}"] = {
            "version": 1,
            "minor_version": 1,
            "key": f"span_panel_current_monitor.{entry.entry_id}",
            "data": {"enabled": True},
        }
        _register_every_monitoring_service(hass)

        refusal = await _refusal(
            hass, "set_global_monitoring", {"enabled": False, "config_entry_id": entry.entry_id}
        )

        assert refusal.translation_key == "monitoring_panel_not_loaded"
        assert await CurrentMonitor.async_is_enabled(hass, entry) is True

    @pytest.mark.parametrize(
        ("service", "data"),
        [
            ("set_circuit_threshold", {"circuit_id": "circuit-5", SPIKE_THRESHOLD_PCT: 90}),
            ("clear_circuit_threshold", {"circuit_id": "circuit-5"}),
            ("set_mains_threshold", {"leg": "upstream_l1", SPIKE_THRESHOLD_PCT: 90}),
            ("clear_mains_threshold", {"leg": "upstream_l1"}),
        ],
    )
    async def test_a_threshold_on_a_loaded_panel_with_monitoring_off_says_monitoring_is_off(
        self, hass: HomeAssistant, service: str, data: Mapping[str, object]
    ) -> None:
        """The one refusal that says monitoring is off: the panel is loaded, and its monitoring is."""
        entry = await _load_panel(hass, _rated_panel(publishes_shared_meters=None), monitored=False)
        _register_every_monitoring_service(hass)

        refusal = await _refusal(hass, service, {**data, "config_entry_id": entry.entry_id})

        assert refusal.translation_key == "monitoring_not_enabled"
