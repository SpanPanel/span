"""Tests for the rotate_credentials service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelConnectionError

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    _async_register_credential_services,
)
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_PANEL_CA_PEM,
    DOMAIN,
)

OLD_BROKER_PASSWORD = "old-broker-password"
NEW_BROKER_PASSWORD = "new-broker-password"


def _add_v2_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a loaded v2 entry with runtime data attached."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_ACCESS_TOKEN: "panel-access-token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_HOST: "192.168.1.100",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_EBUS_BROKER_USERNAME: "span-user",
            CONF_EBUS_BROKER_PASSWORD: OLD_BROKER_PASSWORD,
        },
        entry_id="span_entry",
        unique_id="sp3-test-001",
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=MagicMock(),
        panel_device_id="panel-device-id",
    )
    return entry


def _admin_context(hass: HomeAssistant) -> Context:
    """Return a Context belonging to an administrator."""
    user = MockUser(is_owner=True).add_to_hass(hass)
    return Context(user_id=user.id)


def _non_admin_context(hass: HomeAssistant) -> Context:
    """Return a Context belonging to a non-administrator."""
    user = MockUser().add_to_hass(hass)
    return Context(user_id=user.id)


async def _call_rotate(hass: HomeAssistant, context: Context | None) -> None:
    """Call the service, blocking so exceptions propagate."""
    await hass.services.async_call(
        DOMAIN,
        "rotate_credentials",
        {},
        blocking=True,
        context=context,
    )


@pytest.mark.asyncio
async def test_admin_rotation_stores_the_new_password_and_reloads(
    hass: HomeAssistant,
) -> None:
    """An administrator gets the new broker password persisted and the entry reloaded."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    reload_mock = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.span_panel.services.regenerate_passphrase",
            AsyncMock(return_value=NEW_BROKER_PASSWORD),
        ) as rotate,
        patch.object(hass.config_entries, "async_reload", reload_mock),
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert rotate.await_count == 1
    assert rotate.await_args.args[0] == "192.168.1.100"
    assert rotate.await_args.args[1] == "panel-access-token"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == NEW_BROKER_PASSWORD
    reload_mock.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_non_admin_is_refused(hass: HomeAssistant) -> None:
    """A logged-in non-admin cannot rotate, and nothing reaches the panel."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with patch(
        "custom_components.span_panel.services.regenerate_passphrase",
        AsyncMock(return_value=NEW_BROKER_PASSWORD),
    ) as rotate, pytest.raises(ServiceValidationError) as err:
        await _call_rotate(hass, _non_admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_requires_admin"
    rotate.assert_not_awaited()
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_contextless_call_is_refused(hass: HomeAssistant) -> None:
    """An automation, script or integration has no user and is refused outright."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with patch(
        "custom_components.span_panel.services.regenerate_passphrase",
        AsyncMock(return_value=NEW_BROKER_PASSWORD),
    ) as rotate, pytest.raises(ServiceValidationError) as err:
        await _call_rotate(hass, None)

    assert err.value.translation_key == "rotate_credentials_requires_user"
    rotate.assert_not_awaited()
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_a_deleted_user_is_refused(hass: HomeAssistant) -> None:
    """A user_id that no longer resolves is not an administrator."""
    _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with pytest.raises(ServiceValidationError) as err:
        await _call_rotate(hass, Context(user_id="user-who-no-longer-exists"))

    assert err.value.translation_key == "rotate_credentials_requires_admin"


@pytest.mark.asyncio
async def test_connection_failure_leaves_the_old_password_in_place(
    hass: HomeAssistant,
) -> None:
    """A panel that never answers must not cost the credential that still works."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    reload_mock = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.span_panel.services.regenerate_passphrase",
            AsyncMock(side_effect=SpanPanelConnectionError("unreachable")),
        ),
        patch.object(hass.config_entries, "async_reload", reload_mock),
        pytest.raises(ServiceValidationError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_failed"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD
    reload_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_token_asks_for_reauthentication(hass: HomeAssistant) -> None:
    """A stale access token gets its own message rather than a generic failure."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with patch(
        "custom_components.span_panel.services.regenerate_passphrase",
        AsyncMock(side_effect=SpanPanelAuthError("401")),
    ), pytest.raises(ServiceValidationError) as err:
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_auth_failed"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_missing_access_token_is_reported_before_any_call(
    hass: HomeAssistant,
) -> None:
    """An entry with no token cannot authenticate the rotation."""
    entry = _add_v2_entry(hass)
    data = dict(entry.data)
    del data[CONF_ACCESS_TOKEN]
    hass.config_entries.async_update_entry(entry, data=data)
    _async_register_credential_services(hass)

    with patch(
        "custom_components.span_panel.services.regenerate_passphrase",
        AsyncMock(return_value=NEW_BROKER_PASSWORD),
    ) as rotate, pytest.raises(ServiceValidationError) as err:
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_no_token"
    rotate.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_v2_entry_is_reported(hass: HomeAssistant) -> None:
    """Only v2 entries have a broker credential to rotate."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        data={CONF_HOST: "192.168.1.100", CONF_API_VERSION: "v1"},
        entry_id="span_v1_entry",
        unique_id="sp3-test-002",
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=MagicMock(),
        panel_device_id="panel-device-id",
    )
    _async_register_credential_services(hass)

    with pytest.raises(ServiceValidationError) as err:
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_no_entry"


@pytest.mark.asyncio
async def test_config_entry_id_selects_the_named_panel(hass: HomeAssistant) -> None:
    """With two panels configured, the call rotates only the one named."""
    first = _add_v2_entry(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        data=dict(first.data) | {CONF_HOST: "192.168.1.101"},
        entry_id="span_entry_two",
        unique_id="sp3-test-003",
    )
    second.add_to_hass(hass)
    second.mock_state(hass, ConfigEntryState.LOADED)
    second.runtime_data = SpanPanelRuntimeData(
        coordinator=MagicMock(),
        panel_device_id="panel-device-id-two",
    )
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.regenerate_passphrase",
            AsyncMock(return_value=NEW_BROKER_PASSWORD),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
    ):
        await hass.services.async_call(
            DOMAIN,
            "rotate_credentials",
            {"config_entry_id": second.entry_id},
            blocking=True,
            context=_admin_context(hass),
        )

    assert second.data[CONF_EBUS_BROKER_PASSWORD] == NEW_BROKER_PASSWORD
    assert first.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_entry_without_runtime_data_is_skipped(hass: HomeAssistant) -> None:
    """An entry that reports loaded but carries no runtime data is not a candidate."""
    entry = _add_v2_entry(hass)
    del entry.runtime_data
    _async_register_credential_services(hass)

    with pytest.raises(ServiceValidationError) as err:
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_no_entry"


@pytest.mark.asyncio
async def test_two_panels_and_no_id_refuses_rather_than_picking_one(
    hass: HomeAssistant,
) -> None:
    """With two panels loaded, an omitted id is ambiguous, not a default."""
    first = _add_v2_entry(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        data=dict(first.data) | {CONF_HOST: "192.168.1.101"},
        entry_id="span_entry_two",
        unique_id="sp3-test-004",
    )
    second.add_to_hass(hass)
    second.mock_state(hass, ConfigEntryState.LOADED)
    second.runtime_data = SpanPanelRuntimeData(
        coordinator=MagicMock(),
        panel_device_id="panel-device-id-two",
    )
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.regenerate_passphrase",
            AsyncMock(return_value=NEW_BROKER_PASSWORD),
        ) as rotate,
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
        pytest.raises(ServiceValidationError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_multiple_panels"
    rotate.assert_not_awaited()
    assert first.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD
    assert second.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_a_reload_that_fails_is_reported_with_the_password_already_stored(
    hass: HomeAssistant,
) -> None:
    """The panel did not come back on the new password; the caller must hear it."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.regenerate_passphrase",
            AsyncMock(return_value=NEW_BROKER_PASSWORD),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_reload_failed"
    # The panel has already issued it, so the entry must keep the new one.
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == NEW_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_an_unusable_stored_ca_refuses_rather_than_rotating_in_the_clear(
    hass: HomeAssistant,
) -> None:
    """A rotation carries the token out and the new password back; never unpinned."""
    entry = _add_v2_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data=dict(entry.data) | {CONF_PANEL_CA_PEM: "not a certificate"}
    )
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.regenerate_passphrase",
            AsyncMock(return_value=NEW_BROKER_PASSWORD),
        ) as rotate,
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
        pytest.raises(ServiceValidationError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_ca_unusable"
    rotate.assert_not_awaited()
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD
