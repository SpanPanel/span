"""Tests for Span Panel async_setup_entry."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import (
    SETUP_RETRY_MAX_WAIT,
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    ConfigEntryState,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import dt as dt_util
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from span_panel_api import SpanPanelSnapshot, SpanPVSnapshot, V2HomieSchema, V2StatusInfo
from span_panel_api.exceptions import (
    SpanPanelAdapterIncompatibleError,
    SpanPanelAdapterMissingError,
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
    SpanPanelTLSVerificationError,
)

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    async_remove_entry,
    async_setup_entry,
)
from custom_components.span_panel.config_flow_validation import PanelRestTransport
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DOMAIN,
)
from custom_components.span_panel.control_gate import ControlPolicy
from custom_components.span_panel.curation import CurationOverlay, CurationRecord
from custom_components.span_panel.flat_firmware_repairs import async_raise_flat_firmware
from custom_components.span_panel.id_builder import build_pv_inverter_unique_id
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.options import CONTROL_LOCK_TIMEOUT
from custom_components.span_panel.pv_binding import PvBinding
from custom_components.span_panel.services import _ROTATIONS_IN_PROGRESS

from .conftest import PARENT_CHILD_PANEL_SCHEMA
from .factories import SpanPanelSnapshotFactory, pv_binding_for


def _create_v2_entry(**data_overrides) -> MockConfigEntry:
    """Create a standard v2 config entry for setup-entry tests."""
    data = {
        CONF_API_VERSION: "v2",
        CONF_HOST: "192.168.1.50",
        CONF_EBUS_BROKER_HOST: "span-panel.local",
        CONF_EBUS_BROKER_USERNAME: "mqtt-user",
        CONF_EBUS_BROKER_PASSWORD: "mqtt-pass",
        CONF_EBUS_BROKER_PORT: 8883,
        CONF_HTTP_PORT: 80,
    }
    data.update(data_overrides)
    return MockConfigEntry(
        domain=DOMAIN,
        data=data,
        entry_id="entry-setup",
        title="sp3-setup-001",
        unique_id="sp3-setup-001",
    )


async def test_async_setup_entry_v2_success_sets_runtime_data_and_title(
    hass: HomeAssistant,
) -> None:
    """Successful v2 setup should register runtime data and normalize the title."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = snapshot

    with (
        patch("custom_components.span_panel.async_register_commands") as mock_ws,
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=client
        ) as mock_client_cls,
        patch(
            "custom_components.span_panel.SpanPanelCoordinator",
            return_value=coordinator,
        ) as mock_coordinator_cls,
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ) as mock_ensure_device,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ) as mock_forward,
        patch.object(hass.config_entries, "async_update_entry") as mock_update_entry,
    ):
        assert await async_setup_entry(hass, entry) is True

    # The panel's registry id is carried forward, not recomputed: every sub-device
    # links to it with `via_device_id`, and registration is the only place it is known.
    assert isinstance(entry.runtime_data, SpanPanelRuntimeData)
    assert entry.runtime_data.coordinator is coordinator
    assert entry.runtime_data.panel_device_id == "panel-device-id"
    # The control lock is one object per entry, shared with the gate, so it is
    # compared by identity elsewhere and never by value.
    assert entry.runtime_data.control_lock.armed is False
    assert hass.data[DOMAIN]["websocket_registered"] is True
    mock_ws.assert_called_once_with(hass)
    mock_client_cls.assert_called_once()
    client.connect.assert_awaited_once()
    mock_coordinator_cls.assert_called_once_with(hass, client, entry)
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    coordinator.async_setup_streaming.assert_awaited_once()
    mock_ensure_device.assert_awaited_once_with(hass, entry, snapshot, "SPAN Panel")
    mock_forward.assert_awaited_once()
    mock_update_entry.assert_called_once_with(entry, title="SPAN Panel")


async def test_the_panel_client_is_given_home_assistants_shared_http_client(
    hass: HomeAssistant,
) -> None:
    """Not a client of its own, and not a copy: the one instance HA hands out.

    Without this the library builds a throwaway client per schema read -- once at
    connect, and once per retry while a panel finishes rebooting after a firmware
    upgrade. `quality_scale.yaml` declares `inject-websession: done`, and that was
    true of the config flow and of nothing that ran afterwards.

    Asserted by identity rather than by type. A test that only checked something
    was passed would pass just as well for a fresh client built here, which is
    the thing being removed -- HA owns this one and closes it at shutdown.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=client
        ) as mock_client_cls,
        patch(
            "custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert mock_client_cls.call_args.kwargs["httpx_client"] is get_async_client(hass)


async def test_a_panel_that_is_not_ready_yet_retries_rather_than_dying(
    hass: HomeAssistant,
) -> None:
    """A rebooting panel answers rather than refusing, and that is not a broken install.

    5xx from its front end while the application behind it starts, or a 200 with
    nothing usable in it, both arrive as `SpanPanelServerError`. Uncaught they
    produced SETUP_ERROR with a traceback and no automatic retry — a human needed,
    for a condition that clears itself in minutes.

    The two conditions correlate more than they look: one power event takes out
    the house's electrical panel and the Home Assistant host watching it, and they
    race each other back up. `ConfigEntryNotReady` is what makes the race
    survivable.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.connect = AsyncMock(
        side_effect=SpanPanelServerError("Panel not ready: HTTP 502", 502)
    )
    client.close = AsyncMock()

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    client.close.assert_awaited_once()


async def test_async_setup_entry_v2_missing_mqtt_credentials_raises_auth_failed(
    hass: HomeAssistant,
) -> None:
    """Missing v2 MQTT credentials should trigger reauthentication."""
    entry = _create_v2_entry(**{CONF_EBUS_BROKER_PASSWORD: None})
    entry.add_to_hass(hass)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient") as mock_client_cls,
        pytest.raises(ConfigEntryAuthFailed, match="missing MQTT credentials"),
    ):
        await async_setup_entry(hass, entry)

    mock_client_cls.assert_not_called()


async def test_async_setup_entry_v2_missing_unique_id_raises_not_ready(
    hass: HomeAssistant,
) -> None:
    """A v2 entry without a serial-number unique ID should not set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.168.1.50",
            CONF_EBUS_BROKER_HOST: "span-panel.local",
            CONF_EBUS_BROKER_USERNAME: "mqtt-user",
            CONF_EBUS_BROKER_PASSWORD: "mqtt-pass",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_HTTP_PORT: 80,
        },
        entry_id="entry-no-uid",
        title="SPAN Panel",
        unique_id=None,
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        pytest.raises(ConfigEntryNotReady, match="no unique_id"),
    ):
        await async_setup_entry(hass, entry)


async def test_async_setup_entry_v2_auth_error_closes_client(
    hass: HomeAssistant,
) -> None:
    """MQTT auth errors should close the client before raising."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.connect = AsyncMock(side_effect=SpanPanelAuthError("bad auth"))
    client.close = AsyncMock()

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=client
        ),
        pytest.raises(ConfigEntryAuthFailed, match="MQTT authentication failed"),
    ):
        await async_setup_entry(hass, entry)

    client.close.assert_awaited_once()


async def test_broker_auth_refusal_during_a_rotation_is_not_ready_not_reauth(
    hass: HomeAssistant,
) -> None:
    """Right after a rotation the broker may still refuse the new password.

    `rotate_credentials` retries the reload, so the refusal must not start a
    reauth flow while it does.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.connect = AsyncMock(side_effect=SpanPanelAuthError("bad auth"))
    client.close = AsyncMock()
    hass.data.setdefault(_ROTATIONS_IN_PROGRESS, set()).add(entry.entry_id)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=client
        ),
        pytest.raises(ConfigEntryNotReady, match="rotated password"),
    ):
        await async_setup_entry(hass, entry)

    client.close.assert_awaited_once()


async def test_async_setup_entry_v1_requires_reauth(
    hass: HomeAssistant,
) -> None:
    """Legacy v1 entries should fail with a reauth request."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_VERSION: "v1", CONF_HOST: "192.168.1.50"},
        entry_id="entry-v1",
        title="SPAN Panel",
        unique_id="sp3-v1-001",
    )
    entry.add_to_hass(hass)

    with pytest.raises(ConfigEntryAuthFailed, match="requires reauthentication"):
        await async_setup_entry(hass, entry)


async def test_async_setup_entry_unknown_api_version_raises_config_error(
    hass: HomeAssistant,
) -> None:
    """Unknown API versions should fail clearly."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_VERSION: "v3", CONF_HOST: "192.168.1.50"},
        entry_id="entry-bad-api",
        title="SPAN Panel",
        unique_id="sp3-bad-api-001",
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        pytest.raises(ConfigEntryError, match="Unknown api_version: v3"),
    ):
        await async_setup_entry(hass, entry)


async def test_async_setup_entry_renames_to_unique_panel_title(
    hass: HomeAssistant,
) -> None:
    """Serial-number titles should be normalized without colliding with existing entries."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_VERSION: "v2"},
        entry_id="existing-entry",
        title="SPAN Panel",
        unique_id="sp3-existing-001",
    )
    existing.add_to_hass(hass)

    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = snapshot

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=client
        ),
        patch(
            "custom_components.span_panel.SpanPanelCoordinator",
            return_value=coordinator,
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry") as mock_update_entry,
    ):
        assert await async_setup_entry(hass, entry) is True

    assert mock_update_entry.call_args_list[-1].kwargs["title"] == "SPAN Panel 2"


async def test_async_setup_entry_shutdowns_coordinator_on_forward_failure(
    hass: HomeAssistant,
) -> None:
    """Late setup failures should shut down the coordinator."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.async_shutdown = AsyncMock()
    coordinator.data = snapshot

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=client
        ),
        patch(
            "custom_components.span_panel.SpanPanelCoordinator",
            return_value=coordinator,
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(side_effect=RuntimeError("forward failed")),
        ),
        pytest.raises(RuntimeError, match="forward failed"),
    ):
        await async_setup_entry(hass, entry)

    coordinator.async_shutdown.assert_awaited_once()


async def test_setup_syncs_schema_repairs_after_the_platforms(
    hass: HomeAssistant,
) -> None:
    """Repairs must be reconciled after the platforms, never before.

    A schema Repair names the entities an unresolved field took down, and those
    entities record themselves only once their platform has added them. Schema
    validation itself runs on the first refresh, which setup awaits well before
    forwarding the platforms — reconciling there would report every dead field
    as affecting zero entities.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = snapshot

    order: list[str] = []
    coordinator.async_sync_schema_repairs = MagicMock(
        side_effect=lambda: order.append("sync")
    )

    async def _forward(*_args, **_kwargs) -> None:
        order.append("forward")

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch(
            "custom_components.span_panel.SpanPanelCoordinator",
            return_value=coordinator,
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", AsyncMock(side_effect=_forward)
        ),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert order == ["forward", "sync"]


async def test_setup_announces_additions_after_the_platforms(
    hass: HomeAssistant,
) -> None:
    """The announcement has to run after the forward, and that is the whole ordering.

    A newly added entity is only in the registry once its platform has added it,
    so announcing before the forward would announce nothing, every time. The old
    mechanism also needed a *probe* before the forward, because it diffed the
    registry across it; the announcement record replaced that, which is what makes
    the answer survive a restart landing between the two.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = snapshot

    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, "already-there", config_entry=entry)

    order: list[str] = []
    coordinator.async_sync_schema_repairs = MagicMock(side_effect=lambda: order.append("sync"))

    async def _forward(*_args, **_kwargs) -> None:
        order.append("forward")
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            "added-by-the-forward",
            config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        )

    async def _announce(_hass, _entry) -> None:
        order.append("announce")

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch(
            "custom_components.span_panel.SpanPanelCoordinator",
            return_value=coordinator,
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", AsyncMock(side_effect=_forward)
        ),
        patch.object(hass.config_entries, "async_update_entry"),
        patch(
            "custom_components.span_panel.async_announce_new_entities",
            side_effect=_announce,
        ),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert order == ["forward", "sync", "announce"]


async def test_an_enabled_control_lock_is_armed_before_the_platforms_are_forwarded(
    hass: HomeAssistant,
) -> None:
    """The gate consults the lock long before the switch exists to restore it.

    `set_control_interceptor` happens above the first refresh; the platform
    forward is the last thing setup does. A lock built disarmed would leave
    that whole window -- a first refresh, a streaming start, a device
    registration -- with the feature nominally on and nothing refusing.

    Asserted here rather than through the entity because the entity is exactly
    what has not been created yet at the moment this matters.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONTROL_LOCK_TIMEOUT: 0})
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch(
            "custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert entry.runtime_data.control_lock.armed is True


def test_runtime_data_defaults_its_lock_to_the_default_policys_answer() -> None:
    """The dataclass default may not contradict the policy it stands in for.

    `control_lock` is defaulted rather than required, so an entry built without
    one gets whatever this class decides -- and a bare `ControlLock()` would be
    disarmed, a promise about a feature the class has no way to check. Deriving
    it from `ControlPolicy.default()` is what keeps it honest, and this pins the
    two together so a change to the default policy cannot silently unlock the
    entries that never named a lock.
    """
    runtime_data = SpanPanelRuntimeData(
        coordinator=MagicMock(),
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SpanPanelSnapshotFactory.create()),
        setup_snapshot=SpanPanelSnapshotFactory.create(),
    )

    assert runtime_data.control_lock.armed == ControlPolicy.default().lock_enabled


def test_runtime_data_refuses_to_be_built_without_an_overlay() -> None:
    """`curation` is required so a setup path that forgets the load fails loudly.

    Defaulting it to an empty overlay would make a missed load indistinguishable
    from a user who has curated nothing -- every adopted entity born bare, with
    the user's stored assertions still on disk and nothing saying why they stopped
    being applied.
    """
    with pytest.raises(TypeError, match="curation"):
        SpanPanelRuntimeData(coordinator=MagicMock(), panel_device_id="panel-device-id")


async def test_setup_hands_the_platforms_the_overlay_that_was_on_disk(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """Loaded before the platforms are forwarded, so adopted entities are born curated.

    Applying it afterwards would mean every adopted entity exists uncurated for
    the length of a setup, and a `state_class` that arrives after the first state
    is written is a statistics reset rather than a metadata change.
    """
    hass_storage["span_panel.curation.entry-setup"] = {
        "version": 1,
        "data": {"records": {"bess/battery-2/cell-voltage": {"device_class": "voltage"}}},
    }
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001")
    forwarded_overlay: list[CurationOverlay] = []

    async def _capture(*args: object, **kwargs: object) -> None:
        forwarded_overlay.append(entry.runtime_data.curation)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch(
            "custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator
        ),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", _capture),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert forwarded_overlay[0].record_for("bess/battery-2/cell-voltage") == CurationRecord(
        device_class="voltage"
    )


async def _forwarded_pv_binding(hass: HomeAssistant, entry: MockConfigEntry, snapshot: SpanPanelSnapshot) -> PvBinding:
    """Run `async_setup_entry` over a coordinator publishing `snapshot`; return the binding the platforms were handed."""
    client = MagicMock()
    client.connect = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.data = snapshot
    forwarded: list[PvBinding] = []

    async def _capture(*args: object, **kwargs: object) -> None:
        forwarded.append(entry.runtime_data.pv_binding)

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", _capture),
        patch.object(hass.config_entries, "async_update_entry"),
    ):
        assert await async_setup_entry(hass, entry) is True
    (binding,) = forwarded
    return binding


SETUP_CIRCUIT = "c-setup"


def _one_fed_inverter() -> SpanPanelSnapshot:
    return replace(
        SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001"),
        pv_inverters={
            SETUP_CIRCUIT: SpanPVSnapshot(device_id="pv", node_id=SETUP_CIRCUIT, feed_circuit_id=SETUP_CIRCUIT)
        },
    )


async def test_setup_resolves_the_pv_binding_before_the_platforms(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """The platforms are handed the identity the record on disk decides, not one derived after them."""
    hass_storage["span_panel.pv_binding.entry-setup"] = {"version": 1, "data": {"circuit_id": "c-bound"}}
    entry = _create_v2_entry()
    entry.add_to_hass(hass)

    binding = await _forwarded_pv_binding(hass, entry, SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001"))

    assert binding.bound_key == "c-bound"
    assert binding.mode == "inverter"


async def test_setup_decides_the_pv_binding_from_the_coordinators_snapshot(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """No record: the one inverter `coordinator.data` publishes is bound, and the record is written through setup."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)

    binding = await _forwarded_pv_binding(hass, entry, _one_fed_inverter())

    assert binding.mode == "inverter"
    assert binding.bound_key == SETUP_CIRCUIT
    stored = hass_storage["span_panel.pv_binding.entry-setup"]
    assert isinstance(stored, dict)
    assert stored["data"] == {"circuit_id": SETUP_CIRCUIT}


async def test_setup_reads_the_registry_for_cards_the_inverter_already_holds(
    hass: HomeAssistant, hass_storage: dict[str, object]
) -> None:
    """No record, but the inverter already has a card in this entry: it keeps it, and the record is unbound."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        build_pv_inverter_unique_id("sp3-setup-001", SETUP_CIRCUIT, "pv_vendor"),
        config_entry=entry,
    )

    binding = await _forwarded_pv_binding(hass, entry, _one_fed_inverter())

    assert binding.mode == "unbound"
    assert binding.has_own_card(SETUP_CIRCUIT)
    stored = hass_storage["span_panel.pv_binding.entry-setup"]
    assert isinstance(stored, dict)
    assert stored["data"] == {"circuit_id": None}


def test_runtime_data_refuses_to_be_built_without_a_setup_snapshot() -> None:
    """Required like `pv_binding`: the platforms must build from the snapshot setup decided from."""
    snapshot = SpanPanelSnapshotFactory.create()
    with pytest.raises(TypeError, match="setup_snapshot"):
        SpanPanelRuntimeData(
            coordinator=MagicMock(),
            panel_device_id="panel-device-id",
            curation=CurationOverlay.empty(),
            pv_binding=pv_binding_for(snapshot),
        )


def test_runtime_data_refuses_to_be_built_without_a_pv_binding() -> None:
    """Required like `curation`: a setup path that forgets to resolve it must fail here."""
    with pytest.raises(TypeError, match="pv_binding"):
        SpanPanelRuntimeData(
            coordinator=MagicMock(), panel_device_id="panel-device-id", curation=CurationOverlay.empty()
        )


# ---------------------------------------------------------------------------
# Hardware this release has not been validated with
# ---------------------------------------------------------------------------

UNVALIDATED_HARDWARE_ISSUE = "unvalidated_hardware_entry-setup"


@dataclass(frozen=True, slots=True)
class _PanelUnderSetup:
    """The stand-ins one setup runs against, kept so a test can ask what happened to them."""

    client: MagicMock
    coordinator: MagicMock
    forward: AsyncMock


def _panel_with_model(model: str | None) -> _PanelUnderSetup:
    """A panel whose first refresh reports `model` as its `info/model`."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    # Stated rather than left to the mock, whose own attribute would differ from
    # the flat key only by accident.
    client.schema_major = "schema_1"
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.async_shutdown = AsyncMock()
    coordinator.data = replace(SpanPanelSnapshotFactory.create(serial_number="sp3-setup-001"), model=model)
    return _PanelUnderSetup(client=client, coordinator=coordinator, forward=AsyncMock())


@contextmanager
def _stand_ins(hass: HomeAssistant, panel: _PanelUnderSetup) -> Iterator[MagicMock]:
    """Put the panel's stand-ins where setup builds the real things, and yield the client class."""
    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch(
            "custom_components.span_panel.SpanMqttClient", return_value=panel.client
        ) as client_cls,
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=panel.coordinator),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(return_value="panel-device-id"),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", panel.forward),
    ):
        yield client_cls


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry, panel: _PanelUnderSetup) -> bool:
    with _stand_ins(hass, panel), patch.object(hass.config_entries, "async_update_entry"):
        return await async_setup_entry(hass, entry)


def _status(hardware_version: str | None) -> V2StatusInfo:
    return V2StatusInfo(
        serial_number="sp3-setup-001",
        firmware_version="spanos3/r202639/03",
        hardware_version=hardware_version,
    )


def _unvalidated_hardware_issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, UNVALIDATED_HARDWARE_ISSUE)


@pytest.mark.parametrize("version", ["1.2", "2.0"])
async def test_validated_hardware_proceeds(
    hass: HomeAssistant, panel_status: AsyncMock, version: str
) -> None:
    """The two values seen on real MAIN 32 hardware set up whatever the model says."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status(version)
    panel = _panel_with_model("MAIN_32")

    assert await _set_up(hass, entry, panel) is True

    panel.forward.assert_awaited_once()
    assert _unvalidated_hardware_issue(hass) is None


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(),
        SpanPanelTimeoutError("Timed out connecting to 192.168.1.50"),
        SpanPanelConnectionError("Cannot reach panel at 192.168.1.50"),
        SpanPanelTLSVerificationError("certificate verify failed"),
        SpanPanelAPIError("192.168.1.50 answered HTTP 200 with a body that is not JSON", 200),
        httpx.ReadError("connection reset"),
        httpx.InvalidURL("Invalid port: 'x'"),
    ],
    ids=[
        "timeout",
        "library-timeout",
        "connection",
        "tls",
        "unparseable-body",
        "httpx",
        "invalid-url",
    ],
)
async def test_a_status_that_cannot_be_read_proceeds(
    hass: HomeAssistant, panel_status: AsyncMock, failure: Exception
) -> None:
    """Fail-open: an unread status says nothing about the hardware, so even an unlisted model proceeds.

    The connect path that follows still owns readiness; a panel with flaky HTTPS
    must never be locked out by a read that exists only to refuse.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.side_effect = failure
    panel = _panel_with_model("OTHER_MODEL")

    assert await _set_up(hass, entry, panel) is True

    panel.forward.assert_awaited_once()
    assert _unvalidated_hardware_issue(hass) is None


async def test_a_non_string_hardware_version_proceeds(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    """A malformed field reads as absent, and absent proceeds."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = V2StatusInfo.from_status_payload(
        {"serialNumber": "sp3-setup-001", "firmwareVersion": "spanos3/r202639/03", "hardwareVersion": 9.9}
    )
    panel = _panel_with_model("OTHER_MODEL")

    assert await _set_up(hass, entry, panel) is True

    panel.forward.assert_awaited_once()
    assert _unvalidated_hardware_issue(hass) is None


async def test_a_status_without_hardware_version_proceeds(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    """Firmware before r202639 publishes no `hardwareVersion`: no Repair is raised, only cleared."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = V2StatusInfo.from_status_payload(
        {"serialNumber": "sp3-setup-001", "firmwareVersion": "spanos3/r202633/04"}
    )
    panel = _panel_with_model("MAIN_32")

    with (
        patch("custom_components.span_panel.async_raise_unvalidated_hardware") as raise_issue,
        patch("custom_components.span_panel.async_clear_unvalidated_hardware") as clear_issue,
    ):
        assert await _set_up(hass, entry, panel) is True

    raise_issue.assert_not_called()
    clear_issue.assert_called_once_with(hass, entry)
    panel.forward.assert_awaited_once()


@pytest.mark.parametrize("version", ["UNKNOWN", "9.9"])
async def test_main_32_always_proceeds(hass: HomeAssistant, panel_status: AsyncMock, version: str) -> None:
    """The model escape: `UNKNOWN`, or a value no release has listed, never refuses a MAIN 32."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status(version)
    panel = _panel_with_model("MAIN_32")

    assert await _set_up(hass, entry, panel) is True

    panel.coordinator.async_setup_streaming.assert_awaited_once()
    panel.forward.assert_awaited_once()
    assert _unvalidated_hardware_issue(hass) is None


async def test_unknown_with_no_model_proceeds(hass: HomeAssistant, panel_status: AsyncMock) -> None:
    """Nothing says the hardware is unvalidated: `UNKNOWN` is undetermined, and no model was published."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status("UNKNOWN")
    panel = _panel_with_model(None)

    assert await _set_up(hass, entry, panel) is True

    panel.forward.assert_awaited_once()
    assert _unvalidated_hardware_issue(hass) is None


async def test_unknown_with_another_model_is_refused(hass: HomeAssistant, panel_status: AsyncMock) -> None:
    """Refused after the first refresh, with the Repair, and nothing left running.

    The coordinator is shut down on the way out, which stops dispatch and closes
    the client; no stream is started and no platform is forwarded, so no entity
    exists.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status("UNKNOWN")
    panel = _panel_with_model("OTHER_MODEL")

    with pytest.raises(ConfigEntryError) as raised:
        await _set_up(hass, entry, panel)

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "unvalidated_hardware"
    assert raised.value.translation_placeholders == {"version": "UNKNOWN", "model": "OTHER_MODEL"}
    panel.coordinator.async_config_entry_first_refresh.assert_awaited_once()
    panel.coordinator.async_setup_streaming.assert_not_awaited()
    panel.coordinator.async_shutdown.assert_awaited_once()
    panel.forward.assert_not_awaited()
    issue = _unvalidated_hardware_issue(hass)
    assert issue is not None
    assert issue.translation_key == "unvalidated_hardware"
    assert issue.translation_placeholders == {"version": "UNKNOWN", "model": "OTHER_MODEL"}
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False


@pytest.mark.parametrize("version", ["1.2", "2.0", "3.0"])
async def test_a_validated_hardware_version_string_with_any_model_proceeds(
    hass: HomeAssistant, panel_status: AsyncMock, version: str
) -> None:
    """A validated hardware version string proceeds before the model is ever consulted."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status(version)
    panel = _panel_with_model("OTHER_MODEL")

    assert await _set_up(hass, entry, panel) is True

    panel.forward.assert_awaited_once()
    assert _unvalidated_hardware_issue(hass) is None


@pytest.mark.parametrize("version", ["9.9", "4.0", "0.1"])
async def test_an_unlisted_hardware_version_is_refused(
    hass: HomeAssistant, panel_status: AsyncMock, version: str
) -> None:
    """Any hardware version outside the validated set refuses a panel that is not a MAIN 32."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status(version)
    panel = _panel_with_model("OTHER_MODEL")

    with pytest.raises(ConfigEntryError):
        await _set_up(hass, entry, panel)

    panel.coordinator.async_setup_streaming.assert_not_awaited()
    panel.coordinator.async_shutdown.assert_awaited_once()
    panel.forward.assert_not_awaited()
    issue = _unvalidated_hardware_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"version": version, "model": "OTHER_MODEL"}


async def test_a_later_successful_setup_clears_the_repair(hass: HomeAssistant, panel_status: AsyncMock) -> None:
    """The Repair describes the last setup; one that proceeds takes it down."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status("9.9")
    with pytest.raises(ConfigEntryError):
        await _set_up(hass, entry, _panel_with_model("OTHER_MODEL"))
    assert _unvalidated_hardware_issue(hass) is not None

    panel_status.return_value = _status("2.0")
    assert await _set_up(hass, entry, _panel_with_model("OTHER_MODEL")) is True

    assert _unvalidated_hardware_issue(hass) is None


async def test_an_unread_status_leaves_the_repair_up(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    """A read that fails says nothing about the hardware, so it neither refuses nor takes the Repair down."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status("9.9")
    with pytest.raises(ConfigEntryError):
        await _set_up(hass, entry, _panel_with_model("OTHER_MODEL"))
    assert _unvalidated_hardware_issue(hass) is not None

    panel_status.side_effect = TimeoutError()
    assert await _set_up(hass, entry, _panel_with_model("OTHER_MODEL")) is True

    assert _unvalidated_hardware_issue(hass) is not None


@pytest.mark.parametrize("version", ["unknown", " UNKNOWN "])
async def test_unknown_is_read_whatever_its_spelling(
    hass: HomeAssistant, panel_status: AsyncMock, version: str
) -> None:
    """`unknown` is judged by the model like `UNKNOWN`, never refused on its spelling alone.

    With no model published, `UNKNOWN` proceeds where an unlisted version is refused.
    """
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status(version)

    assert await _set_up(hass, entry, _panel_with_model(None)) is True

    assert _unvalidated_hardware_issue(hass) is None


async def test_removing_the_entry_clears_the_repair(hass: HomeAssistant, panel_status: AsyncMock) -> None:
    """Nothing else could clear it once the entry is gone."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status("9.9")
    with pytest.raises(ConfigEntryError):
        await _set_up(hass, entry, _panel_with_model("OTHER_MODEL"))

    await async_remove_entry(hass, entry)

    assert _unvalidated_hardware_issue(hass) is None


async def test_the_status_is_read_before_connecting_over_the_entrys_rest_transport(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    """The entry's own port and anchor, the shared client, and before the broker is dialled."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    anchor = MagicMock(spec=ssl.SSLContext)
    transport = PanelRestTransport(port=8443, ssl_context=anchor, httpx_client=None, ca_pem="pem")
    panel = _panel_with_model("MAIN_32")
    order: list[str] = []
    panel_status.side_effect = lambda *_args, **_kwargs: order.append("status") or _status("2.0")
    panel.client.connect = AsyncMock(side_effect=lambda: order.append("connect"))

    with patch("custom_components.span_panel.panel_rest_transport", return_value=transport):
        assert await _set_up(hass, entry, panel) is True

    panel_status.assert_awaited_once_with(
        "192.168.1.50",
        port=8443,
        httpx_client=get_async_client(hass),
        ssl_context=anchor,
    )
    assert order == ["status", "connect"]


async def test_a_refused_panel_with_no_model_is_named_without_one(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    """A panel that publishes no model gets the sentence for that, never an empty model in the text."""
    entry = _create_v2_entry()
    entry.add_to_hass(hass)
    panel_status.return_value = _status("9.9")
    panel = _panel_with_model(None)

    with pytest.raises(ConfigEntryError) as raised:
        await _set_up(hass, entry, panel)

    assert raised.value.translation_key == "unvalidated_hardware_no_model"
    assert raised.value.translation_placeholders == {"version": "9.9"}
    issue = _unvalidated_hardware_issue(hass)
    assert issue is not None
    assert issue.translation_key == "unvalidated_hardware_no_model"
    assert issue.translation_placeholders == {"version": "9.9"}


async def test_a_refused_setup_logs_one_error_naming_the_hardware(
    hass: HomeAssistant, panel_status: AsyncMock
) -> None:
    """Home Assistant's own setup error is the one line, in the Repair's words."""
    # At the current version, so setup runs as it does for an installed entry rather than
    # after a migration.
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=dict(_create_v2_entry().data),
        entry_id="entry-setup",
        title="sp3-setup-001",
        unique_id="sp3-setup-001",
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    panel_status.return_value = _status("9.9")
    panel = _panel_with_model("OTHER_MODEL")
    errors = _ErrorRecords()
    root = logging.getLogger()
    root.addHandler(errors)

    try:
        with (
            patch("custom_components.span_panel.async_register_commands"),
            patch("custom_components.span_panel.SpanMqttClient", return_value=panel.client),
            patch("custom_components.span_panel.SpanPanelCoordinator", return_value=panel.coordinator),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id) is False
    finally:
        root.removeHandler(errors)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(errors.messages) == 1, errors.messages
    assert "Hardware version 9.9 with model OTHER_MODEL has not been validated" in errors.messages[0]


class _ErrorRecords(logging.Handler):
    """Every message logged at ERROR or above while attached; the suite's logging setup replaces caplog's handler."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


# ---------------------------------------------------------------------------
# Firmware that publishes the older flat eBus schema
# ---------------------------------------------------------------------------

FLAT_FIRMWARE_ISSUE = "flat_firmware_entry-setup"

FLAT_PANEL_SCHEMA = V2HomieSchema(
    firmware_version="spanos2/r202603/05",
    types_schema_hash="0000000000000000",
    types={},
    data_model_version=None,
)
"""What a panel on spanos2 firmware answers: a schema that publishes no `data_model_version`."""


def _installed_entry() -> MockConfigEntry:
    """Return an entry as an earlier setup left it: at the current version, and already titled.

    So Home Assistant sets it up as it does at boot, with no migration first and
    no rename after, whose update listener would reload it underneath the test.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        data=dict(_create_v2_entry().data),
        entry_id="entry-setup",
        title="SPAN Panel",
        unique_id="sp3-setup-001",
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )


async def _boot(hass: HomeAssistant, entry: MockConfigEntry, panel: _PanelUnderSetup) -> bool:
    """Set the entry up through Home Assistant, which turns setup's outcome into its state."""
    with _stand_ins(hass, panel):
        return await hass.config_entries.async_setup(entry.entry_id)


async def _let_the_retry_run(hass: HomeAssistant) -> None:
    """Move past the longest wait Home Assistant schedules a retry for, and let the retry finish."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SETUP_RETRY_MAX_WAIT + 1))
    await hass.async_block_till_done(wait_background_tasks=True)


def _flat_firmware_issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, FLAT_FIRMWARE_ISSUE)


def _assert_refused_as_flat(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Retrying, with the reason worded as flat firmware, and the Repair up."""
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_domain == DOMAIN
    assert entry.error_reason_translation_key == "panel_firmware_flat"
    assert entry.error_reason_translation_placeholders == {"panel": "SPAN Panel"}
    assert _flat_firmware_issue(hass) is not None


async def test_setup_refuses_a_flat_panel_and_raises_the_repair(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """Retried rather than refused for good, with the Repair, and no client built.

    The remedy is a firmware update on the panel, and the retry is what notices
    it. Nothing dials the broker of a panel this release refuses.
    """
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.return_value = FLAT_PANEL_SCHEMA
    panel = _panel_with_model("MAIN_32")

    with _stand_ins(hass, panel) as client_cls:
        assert await hass.config_entries.async_setup(entry.entry_id) is False

    _assert_refused_as_flat(hass, entry)
    client_cls.assert_not_called()
    issue = _flat_firmware_issue(hass)
    assert issue is not None
    assert issue.translation_key == "panel_firmware_flat"
    assert issue.translation_placeholders == {"panel": "SPAN Panel"}
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False
    assert issue.is_persistent is False


async def test_a_refused_panel_recovers_on_its_own(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """After a firmware update, Home Assistant's own retry sets the panel up and clears the Repair.

    Nobody has to reload it: the panel reconnects on its own, as the Repair says.
    """
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.return_value = FLAT_PANEL_SCHEMA
    panel = _panel_with_model("MAIN_32")

    with _stand_ins(hass, panel) as client_cls:
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        _assert_refused_as_flat(hass, entry)

        panel_schema.return_value = PARENT_CHILD_PANEL_SCHEMA
        await _let_the_retry_run(hass)

    assert entry.state is ConfigEntryState.LOADED
    client_cls.assert_called_once()
    panel.forward.assert_awaited_once()
    assert _flat_firmware_issue(hass) is None


async def test_an_unread_schema_then_a_parent_child_connection_clears_a_standing_repair(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """A parent/child adapter settles what the read could not: the panel is not flat."""
    entry = _installed_entry()
    entry.add_to_hass(hass)
    async_raise_flat_firmware(hass, entry)
    panel_schema.side_effect = TimeoutError()

    assert await _boot(hass, entry, _panel_with_model("MAIN_32")) is True

    assert entry.state is ConfigEntryState.LOADED
    assert _flat_firmware_issue(hass) is None


async def test_an_unread_schema_leaves_a_standing_repair_until_connect(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """A failed read says nothing about the firmware, and nor does a connect that fails after it.

    The panel retries as any unreachable panel does, under the connection's own
    reason rather than the flat one.
    """
    entry = _installed_entry()
    entry.add_to_hass(hass)
    async_raise_flat_firmware(hass, entry)
    panel_schema.side_effect = TimeoutError()
    panel = _panel_with_model("MAIN_32")
    panel.client.connect.side_effect = SpanPanelConnectionError(
        "Cannot reach panel at 192.168.1.50"
    )

    assert await _boot(hass, entry, panel) is False

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key is None
    assert entry.reason is not None
    assert entry.reason.startswith("SPAN panel is not ready yet")
    assert _flat_firmware_issue(hass) is not None


async def test_an_unread_schema_never_refuses(hass: HomeAssistant, panel_schema: AsyncMock) -> None:
    """A healthy panel whose schema read times out at boot sets up as it always has."""
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.side_effect = TimeoutError()
    panel = _panel_with_model("MAIN_32")

    assert await _boot(hass, entry, panel) is True

    assert entry.state is ConfigEntryState.LOADED
    panel.client.connect.assert_awaited_once()
    panel.forward.assert_awaited_once()
    assert _flat_firmware_issue(hass) is None


async def test_backstop_refuses_on_a_missing_flat_adapter(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """With the schema-0 package gone, the library refusing to parse the panel says it is flat."""
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.side_effect = TimeoutError()
    panel = _panel_with_model("MAIN_32")
    panel.client.connect.side_effect = SpanPanelAdapterMissingError(
        needed="schema_0", reason="data-model-version absent (flat schema)", available=["schema_1"]
    )

    assert await _boot(hass, entry, panel) is False

    _assert_refused_as_flat(hass, entry)
    panel.client.close.assert_awaited_once()


async def test_backstop_refuses_on_an_incompatible_flat_adapter(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """A leftover schema-0 package the library has outgrown is flat too: retried, not an error."""
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.side_effect = TimeoutError()
    panel = _panel_with_model("MAIN_32")
    panel.client.connect.side_effect = SpanPanelAdapterIncompatibleError(
        needed="schema_0",
        reason="data-model-version absent (flat schema)",
        defect="it was built against an older adapter contract.",
    )

    assert await _boot(hass, entry, panel) is False

    _assert_refused_as_flat(hass, entry)
    panel.client.close.assert_awaited_once()


async def test_backstop_refuses_a_leftover_flat_adapter(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """A schema-0 package an older install left behind parses the panel, which is refused anyway.

    Setup closes the client itself, since no coordinator exists yet to do it, and
    refuses before the first refresh, so nothing reads what that adapter made.
    """
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.side_effect = TimeoutError()
    panel = _panel_with_model("MAIN_32")
    panel.client.schema_major = "schema_0"

    assert await _boot(hass, entry, panel) is False

    _assert_refused_as_flat(hass, entry)
    panel.client.close.assert_awaited_once()
    panel.coordinator.async_config_entry_first_refresh.assert_not_awaited()
    panel.forward.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        SpanPanelAdapterMissingError(
            needed="schema_2", reason="data-model-version='2.0'", available=["schema_1"]
        ),
        SpanPanelAdapterIncompatibleError(
            needed="schema_2",
            reason="data-model-version='2.0'",
            defect="it was built against a newer adapter contract.",
        ),
    ],
    ids=["missing", "incompatible"],
)
async def test_an_adapter_error_for_another_schema_is_not_flat(
    hass: HomeAssistant, panel_schema: AsyncMock, error: Exception
) -> None:
    """Left as it always was, a setup error, and never taken for flat firmware."""
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.side_effect = TimeoutError()
    panel = _panel_with_model("MAIN_32")
    panel.client.connect.side_effect = error

    assert await _boot(hass, entry, panel) is False

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert _flat_firmware_issue(hass) is None


async def test_the_schema_is_read_before_the_client_is_built_over_the_entrys_rest_transport(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """The entry's own port and anchor, the shared client, and before any client exists.

    A pinned entry read without its context would send plaintext to its HTTPS
    port and answer unread on every boot, which skips the check unseen.
    """
    entry = _installed_entry()
    entry.add_to_hass(hass)
    anchor = MagicMock(spec=ssl.SSLContext)
    transport = PanelRestTransport(port=8443, ssl_context=anchor, httpx_client=None, ca_pem="pem")
    panel = _panel_with_model("MAIN_32")
    order: list[str] = []
    panel_schema.side_effect = lambda *_args, **_kwargs: (
        order.append("schema") or PARENT_CHILD_PANEL_SCHEMA
    )

    with (
        patch("custom_components.span_panel.panel_rest_transport", return_value=transport),
        _stand_ins(hass, panel) as client_cls,
    ):
        client_cls.side_effect = lambda *_args, **_kwargs: order.append("client") or panel.client
        assert await hass.config_entries.async_setup(entry.entry_id) is True

    panel_schema.assert_awaited_once_with(
        "192.168.1.50",
        port=8443,
        httpx_client=get_async_client(hass),
        ssl_context=anchor,
    )
    assert order == ["schema", "client"]


async def test_removing_a_refused_entry_clears_the_flat_firmware_repair(
    hass: HomeAssistant, panel_schema: AsyncMock
) -> None:
    """Not persistent, yet it would outlive the entry until a restart: nothing else clears it."""
    entry = _installed_entry()
    entry.add_to_hass(hass)
    panel_schema.return_value = FLAT_PANEL_SCHEMA
    assert await _boot(hass, entry, _panel_with_model("MAIN_32")) is False
    assert _flat_firmware_issue(hass) is not None

    await hass.config_entries.async_remove(entry.entry_id)

    assert _flat_firmware_issue(hass) is None
