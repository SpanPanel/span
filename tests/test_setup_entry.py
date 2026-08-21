"""Tests for Span Panel async_setup_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from span_panel_api.exceptions import SpanPanelServerError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import get_async_client
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api.exceptions import SpanPanelAuthError

from custom_components.span_panel import SpanPanelRuntimeData, async_setup_entry
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DOMAIN,
)

from .factories import SpanPanelSnapshotFactory


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
    assert entry.runtime_data == SpanPanelRuntimeData(
        coordinator=coordinator, panel_device_id="panel-device-id"
    )
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
