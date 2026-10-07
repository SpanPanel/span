"""Tests for the rotate_credentials service."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import Context, CoreState, HomeAssistant, ServiceResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
from span_panel_api import PassphraseRotation
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelInsufficientPrivilegeError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
    SpanPanelTLSVerificationError,
)

from custom_components.span_panel import (
    SpanPanelRuntimeData,
    _async_register_credential_services,
    update_listener,
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
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.services import (
    _ROTATION_RECONNECT_DELAYS_S,
    _ROTATIONS_IN_PROGRESS,
    _rotation_outcome_unknown,
    rotation_in_progress,
)

from .factories import SpanPanelSnapshotFactory, pv_binding_for

OLD_BROKER_PASSWORD = "old-broker-password"
NEW_BROKER_PASSWORD = "new-broker-password"
# The panel reports the same value in both fields; distinct here so a test can
# tell which field the integration stored.
NEW_HOP_PASSPHRASE = "new-hop-passphrase"
ROTATION = PassphraseRotation(
    ebus_broker_password=NEW_BROKER_PASSWORD, hop_passphrase=NEW_HOP_PASSPHRASE
)


def _raised_from(
    error_type: type[SpanPanelConnectionError | SpanPanelTimeoutError],
    cause_type: type[httpx.TransportError],
) -> Exception:
    """Build a library transport error chained from the httpx error behind it."""
    error = error_type(cause_type.__name__)
    error.__cause__ = cause_type(cause_type.__name__)
    return error


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
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SpanPanelSnapshotFactory.create()),
        setup_snapshot=SpanPanelSnapshotFactory.create(),
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


async def _call_rotate(hass: HomeAssistant, context: Context | None) -> ServiceResponse:
    """Call the service, blocking so exceptions propagate."""
    return await hass.services.async_call(
        DOMAIN,
        "rotate_credentials",
        {},
        blocking=True,
        context=context,
        return_response=True,
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
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ) as rotate,
        patch.object(hass.config_entries, "async_reload", reload_mock),
    ):
        response = await _call_rotate(hass, _admin_context(hass))

    assert response == {"hop_passphrase": NEW_HOP_PASSPHRASE, "reconnected": True}
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
        "custom_components.span_panel.services.rotate_passphrase",
        AsyncMock(return_value=ROTATION),
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
        "custom_components.span_panel.services.rotate_passphrase",
        AsyncMock(return_value=ROTATION),
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
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(side_effect=_raised_from(SpanPanelConnectionError, httpx.ConnectError)),
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
        "custom_components.span_panel.services.rotate_passphrase",
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
        "custom_components.span_panel.services.rotate_passphrase",
        AsyncMock(return_value=ROTATION),
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
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SpanPanelSnapshotFactory.create()),
        setup_snapshot=SpanPanelSnapshotFactory.create(),
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
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SpanPanelSnapshotFactory.create()),
        setup_snapshot=SpanPanelSnapshotFactory.create(),
    )
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
    ):
        await hass.services.async_call(
            DOMAIN,
            "rotate_credentials",
            {"config_entry_id": second.entry_id},
            blocking=True,
            context=_admin_context(hass),
            return_response=True,
        )

    assert second.data[CONF_EBUS_BROKER_PASSWORD] == NEW_BROKER_PASSWORD
    assert first.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_entry_that_is_not_loaded_is_rotated(hass: HomeAssistant) -> None:
    """An entry that is not loaded is rotated from its stored data.

    It is the entry a rotation that did not reconnect leaves behind, or one that
    restarted with a broker password the panel no longer accepts, and another
    rotation is how it recovers: the panel does not revoke the stored token.
    """
    entry = _add_v2_entry(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    del entry.runtime_data
    _async_register_credential_services(hass)

    reload_mock = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ) as rotate,
        patch.object(hass.config_entries, "async_reload", reload_mock),
    ):
        response = await _call_rotate(hass, _admin_context(hass))

    assert response == {"hop_passphrase": NEW_HOP_PASSPHRASE, "reconnected": True}
    assert rotate.await_args.args[1] == "panel-access-token"
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == NEW_BROKER_PASSWORD
    reload_mock.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_disabled_entry_is_not_a_candidate(hass: HomeAssistant) -> None:
    """A disabled entry is not rotated, and nothing reaches the panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_ACCESS_TOKEN: "panel-access-token",
            CONF_API_VERSION: "v2",
            CONF_EBUS_BROKER_PASSWORD: OLD_BROKER_PASSWORD,
        },
        entry_id="span_entry",
        unique_id="sp3-test-001",
        disabled_by=ConfigEntryDisabler.USER,
    )
    entry.add_to_hass(hass)
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ) as rotate,
        pytest.raises(ServiceValidationError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_no_entry"
    rotate.assert_not_awaited()
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


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
        curation=CurationOverlay.empty(),
        pv_binding=pv_binding_for(SpanPanelSnapshotFactory.create()),
        setup_snapshot=SpanPanelSnapshotFactory.create(),
    )
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
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
async def test_a_reconnect_that_never_succeeds_still_returns_the_passphrase(
    hass: HomeAssistant,
) -> None:
    """Raising would discard the only copy of the new passphrase, so it is returned."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    reload_mock = AsyncMock(return_value=False)
    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ),
        patch.object(hass.config_entries, "async_reload", reload_mock),
        patch("custom_components.span_panel.services.asyncio.sleep", AsyncMock()) as sleep,
    ):
        response = await _call_rotate(hass, _admin_context(hass))

    assert response == {"hop_passphrase": NEW_HOP_PASSPHRASE, "reconnected": False}
    assert [c.args[0] for c in sleep.await_args_list] == list(_ROTATION_RECONNECT_DELAYS_S)
    assert sum(_ROTATION_RECONNECT_DELAYS_S) == pytest.approx(60.0)
    assert reload_mock.await_count == len(_ROTATION_RECONNECT_DELAYS_S) + 1
    # The panel has already issued it, so the entry must keep the new one.
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == NEW_BROKER_PASSWORD
    assert not rotation_in_progress(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_a_reload_that_raises_still_returns_the_passphrase(
    hass: HomeAssistant,
) -> None:
    """An exception from a reload is a failed attempt, not a lost response."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    reload_mock = AsyncMock(side_effect=[RuntimeError("setup blew up"), True])
    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ),
        patch.object(hass.config_entries, "async_reload", reload_mock),
        patch("custom_components.span_panel.services.asyncio.sleep", AsyncMock()),
    ):
        response = await _call_rotate(hass, _admin_context(hass))

    assert response == {"hop_passphrase": NEW_HOP_PASSPHRASE, "reconnected": True}
    assert reload_mock.await_count == 2
    assert not rotation_in_progress(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_reconnect_retries_until_the_broker_accepts_the_new_password(
    hass: HomeAssistant,
) -> None:
    """Refusals right after the rotation are retried with the new password, under the grace flag."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    seen: list[tuple[str, bool]] = []

    async def _reload(entry_id: str) -> bool:
        seen.append(
            (
                hass.config_entries.async_get_entry(entry_id).data[CONF_EBUS_BROKER_PASSWORD],
                rotation_in_progress(hass, entry_id),
            )
        )
        return len(seen) == 3

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(side_effect=_reload)),
        patch("custom_components.span_panel.services.asyncio.sleep", AsyncMock()) as sleep,
    ):
        response = await _call_rotate(hass, _admin_context(hass))

    assert response == {"hop_passphrase": NEW_HOP_PASSPHRASE, "reconnected": True}
    # Every attempt uses the new password and runs inside the grace window.
    assert seen == [(NEW_BROKER_PASSWORD, True)] * 3
    assert [c.args[0] for c in sleep.await_args_list] == list(_ROTATION_RECONNECT_DELAYS_S[:2])
    assert not rotation_in_progress(hass, entry.entry_id)
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


@pytest.mark.asyncio
async def test_the_options_listener_leaves_the_reload_to_the_rotation(
    hass: HomeAssistant,
) -> None:
    """Storing the new password must not start a second, unguarded reload."""
    entry = _add_v2_entry(hass)
    hass.set_state(CoreState.running)
    hass.data.setdefault(_ROTATIONS_IN_PROGRESS, set()).add(entry.entry_id)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_mock:
        await update_listener(hass, entry)

    reload_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_passphrase_is_returned_but_never_stored_or_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The integration keeps only the broker password; the passphrase goes to the caller."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)
    caplog.set_level(logging.DEBUG)

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
    ):
        response = await _call_rotate(hass, _admin_context(hass))

    assert response["hop_passphrase"] == NEW_HOP_PASSPHRASE
    assert NEW_HOP_PASSPHRASE not in repr(dict(entry.data))
    assert NEW_HOP_PASSPHRASE not in repr(dict(entry.options))
    assert NEW_HOP_PASSPHRASE not in caplog.text
    assert NEW_BROKER_PASSWORD not in caplog.text


@pytest.mark.asyncio
async def test_a_call_that_does_not_ask_for_the_response_is_refused(
    hass: HomeAssistant,
) -> None:
    """Without the response the new passphrase would be lost, so nothing is rotated."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ) as rotate,
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            DOMAIN,
            "rotate_credentials",
            {},
            blocking=True,
            context=_admin_context(hass),
        )

    rotate.assert_not_awaited()
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.parametrize(
    ("error", "translation_key", "validation"),
    [
        (
            SpanPanelInsufficientPrivilegeError("403"),
            "rotate_credentials_insufficient_privilege",
            True,
        ),
        (SpanPanelAuthError("401"), "rotate_credentials_auth_failed", True),
        (SpanPanelServerError("500", status_code=500), "rotate_credentials_outcome_unknown", False),
        (SpanPanelServerError("503", status_code=503), "rotate_credentials_failed", True),
        (SpanPanelServerError("502", status_code=502), "rotate_credentials_outcome_unknown", False),
        (
            SpanPanelAPIError("unreadable body", status_code=200),
            "rotate_credentials_outcome_unknown",
            False,
        ),
        (SpanPanelAPIError("404", status_code=404), "rotate_credentials_failed", True),
        (
            _raised_from(SpanPanelTimeoutError, httpx.ReadTimeout),
            "rotate_credentials_outcome_unknown",
            False,
        ),
        (
            _raised_from(SpanPanelTimeoutError, httpx.WriteTimeout),
            "rotate_credentials_outcome_unknown",
            False,
        ),
        (
            _raised_from(SpanPanelConnectionError, httpx.RemoteProtocolError),
            "rotate_credentials_outcome_unknown",
            False,
        ),
        (
            _raised_from(SpanPanelConnectionError, httpx.ReadError),
            "rotate_credentials_outcome_unknown",
            False,
        ),
        (SpanPanelTimeoutError("timed out"), "rotate_credentials_outcome_unknown", False),
        (
            _raised_from(SpanPanelTimeoutError, httpx.ConnectTimeout),
            "rotate_credentials_failed",
            True,
        ),
        (
            _raised_from(SpanPanelTimeoutError, httpx.PoolTimeout),
            "rotate_credentials_failed",
            True,
        ),
        (
            _raised_from(SpanPanelConnectionError, httpx.ConnectError),
            "rotate_credentials_failed",
            True,
        ),
        (SpanPanelTLSVerificationError("bad certificate"), "rotate_credentials_failed", True),
    ],
)
@pytest.mark.asyncio
async def test_each_rotation_error_has_its_own_message_and_stores_nothing(
    hass: HomeAssistant,
    error: Exception,
    translation_key: str,
    validation: bool,
) -> None:
    """An unknown outcome is not reported as a refusal that changed nothing."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    reload_mock = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(side_effect=error),
        ),
        patch.object(hass.config_entries, "async_reload", reload_mock),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == translation_key
    assert isinstance(err.value, ServiceValidationError) is validation
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD
    reload_mock.assert_not_awaited()


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
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(return_value=ROTATION),
        ) as rotate,
        patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)),
        pytest.raises(ServiceValidationError) as err,
    ):
        await _call_rotate(hass, _admin_context(hass))

    assert err.value.translation_key == "rotate_credentials_ca_unusable"
    rotate.assert_not_awaited()
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == OLD_BROKER_PASSWORD


@pytest.mark.asyncio
async def test_overlapping_rotations_of_one_entry_run_one_after_the_other(
    hass: HomeAssistant,
) -> None:
    """A second call waits for the first to finish, and the later rotation's password is stored."""
    entry = _add_v2_entry(hass)
    _async_register_credential_services(hass)

    second = PassphraseRotation(
        ebus_broker_password="second-broker-password", hop_passphrase="second-hop"
    )
    events: list[str] = []
    release_first_reload = asyncio.Event()
    first_reload_started = asyncio.Event()

    async def _rotate(*_args: object, **_kwargs: object) -> PassphraseRotation:
        events.append(f"put{len([e for e in events if e.startswith('put')]) + 1}")
        return ROTATION if events[-1] == "put1" else second

    async def _reload(entry_id: str) -> bool:
        events.append(f"reload:{rotation_in_progress(hass, entry_id)}")
        if not first_reload_started.is_set():
            first_reload_started.set()
            await release_first_reload.wait()
        return True

    with (
        patch(
            "custom_components.span_panel.services.rotate_passphrase",
            AsyncMock(side_effect=_rotate),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock(side_effect=_reload)),
    ):
        first = hass.async_create_task(_call_rotate(hass, _admin_context(hass)))
        await first_reload_started.wait()
        second_call = hass.async_create_task(_call_rotate(hass, _admin_context(hass)))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # The second PUT has not started while the first is still reconnecting.
        assert events == ["put1", "reload:True"]
        assert rotation_in_progress(hass, entry.entry_id)
        release_first_reload.set()
        first_response = await first
        second_response = await second_call

    assert events == ["put1", "reload:True", "put2", "reload:True"]
    assert first_response == {"hop_passphrase": NEW_HOP_PASSPHRASE, "reconnected": True}
    assert second_response == {"hop_passphrase": "second-hop", "reconnected": True}
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == "second-broker-password"
    assert not rotation_in_progress(hass, entry.entry_id)


def test_the_outcome_unknown_fallback_matches_strings_json() -> None:
    """The English default message is the one in strings.json."""
    strings = json.loads(
        (Path(__file__).parent.parent / "custom_components/span_panel/strings.json").read_text(
            encoding="utf-8"
        )
    )
    expected = strings["exceptions"]["rotate_credentials_outcome_unknown"]["message"]
    assert str(_rotation_outcome_unknown("panel.local")) == expected.format(host="panel.local")
