"""Tests for pinning the panel's CA: acquisition, deferral, and the change repair."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from span_panel_api.exceptions import SpanPanelCAChangedError, SpanPanelConnectionError

from custom_components.span_panel import _async_pinned_ca, async_migrate_entry
from custom_components.span_panel.ca_repairs import (
    async_clear_ca_changed,
    async_raise_ca_changed,
    ca_changed_issue_id,
)
from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_HTTP_PORT,
    CONF_PANEL_CA_PEM,
    DOMAIN,
    PANEL_CA_PENDING,
)
from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from pytest_homeassistant_custom_component.common import MockConfigEntry

PEM = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
OTHER_PEM = "-----BEGIN CERTIFICATE-----\nb3RoZXI=\n-----END CERTIFICATE-----\n"


def _entry(hass: HomeAssistant, **data: object) -> MockConfigEntry:
    """Add a v7 entry carrying the given data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=7,
        title="Span Panel",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_ACCESS_TOKEN: "token",
            CONF_API_VERSION: "v2",
            **data,
        },
        source=config_entries.SOURCE_USER,
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)
    return entry


# ---------- acquisition at setup ----------


@pytest.mark.asyncio
async def test_a_stored_pin_is_used_without_any_fetch(hass: HomeAssistant) -> None:
    """An entry that already carries a CA does not go back to the panel for one."""
    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})

    with patch(
        "custom_components.span_panel.async_fetch_panel_ca", new=AsyncMock()
    ) as fetch:
        assert await _async_pinned_ca(hass, entry, "192.168.1.100", 80) == PEM

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_deferred_fetch_stores_the_ca_and_clears_the_flag(
    hass: HomeAssistant,
) -> None:
    """A migrated entry acquires its CA at the first setup that reaches the panel."""
    entry = _entry(hass, **{PANEL_CA_PENDING: True})

    with patch(
        "custom_components.span_panel.async_fetch_panel_ca",
        new=AsyncMock(return_value=PEM),
    ) as fetch:
        assert await _async_pinned_ca(hass, entry, "192.168.1.100", 8080) == PEM

    fetch.assert_awaited_once_with(hass, "192.168.1.100", http_port=8080)
    assert entry.data[CONF_PANEL_CA_PEM] == PEM
    assert PANEL_CA_PENDING not in entry.data


@pytest.mark.asyncio
async def test_a_failed_deferred_fetch_keeps_the_flag_and_does_not_fail_setup(
    hass: HomeAssistant,
) -> None:
    """An unreachable certificate endpoint must not cost the user their integration."""
    entry = _entry(hass, **{PANEL_CA_PENDING: True})

    with patch(
        "custom_components.span_panel.async_fetch_panel_ca",
        new=AsyncMock(side_effect=SpanPanelConnectionError("unreachable")),
    ):
        assert await _async_pinned_ca(hass, entry, "192.168.1.100", 80) is None

    # Still pending, so the next setup retries for free.
    assert entry.data[PANEL_CA_PENDING] is True
    assert CONF_PANEL_CA_PEM not in entry.data


@pytest.mark.asyncio
async def test_an_entry_with_neither_pin_nor_flag_is_left_alone(
    hass: HomeAssistant,
) -> None:
    """Nothing acquires a CA behind the user's back."""
    entry = _entry(hass)

    with patch(
        "custom_components.span_panel.async_fetch_panel_ca", new=AsyncMock()
    ) as fetch:
        assert await _async_pinned_ca(hass, entry, "192.168.1.100", 80) is None

    fetch.assert_not_awaited()


# ---------- the migration that queues it ----------


@pytest.mark.asyncio
async def test_v7_flags_a_v2_entry_for_ca_acquisition(hass: HomeAssistant) -> None:
    """The migration queues the fetch rather than performing it."""
    entry = MockConfigEntry(
        version=6,
        domain=DOMAIN,
        title="Span Panel",
        data={CONF_HOST: "192.168.1.100", CONF_API_VERSION: "v2"},
        source=config_entries.SOURCE_USER,
        unique_id="SPAN-V2-001",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.span_panel.migrations.async_migrate_entry",
        wraps=async_migrate_entry,
    ):
        assert await async_migrate_entry(hass, entry) is True

    assert entry.data[PANEL_CA_PENDING] is True
    # No PEM: the migration does no I/O at all.
    assert CONF_PANEL_CA_PEM not in entry.data


@pytest.mark.asyncio
@pytest.mark.parametrize("api_version", ["v1", "simulation"])
async def test_non_v2_entries_are_never_flagged(
    hass: HomeAssistant, api_version: str
) -> None:
    """v1 fails setup before it reaches a panel, and a simulation has none."""
    entry = MockConfigEntry(
        version=6,
        domain=DOMAIN,
        title="Span Panel",
        data={CONF_HOST: "192.168.1.100", CONF_API_VERSION: api_version},
        source=config_entries.SOURCE_USER,
        unique_id=f"SPAN-{api_version}",
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert PANEL_CA_PENDING not in entry.data


# ---------- the repair ----------


@pytest.mark.asyncio
async def test_a_changed_ca_raises_a_fixable_repair_carrying_both_fingerprints(
    hass: HomeAssistant,
) -> None:
    """The user needs both values, because the two remedies are opposite."""
    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})

    async_raise_ca_changed(hass, entry, "aa" * 32, "bb" * 32)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ca_changed_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.is_fixable is True
    # Persistent, because the transport it describes is already dead — there is
    # no live state left to re-assert it from.
    assert issue.is_persistent is True
    assert issue.severity == ir.IssueSeverity.ERROR
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["expected_fingerprint"] == "aa" * 32
    assert issue.translation_placeholders["observed_fingerprint"] == "bb" * 32


@pytest.mark.asyncio
async def test_a_clean_connection_clears_a_standing_repair(hass: HomeAssistant) -> None:
    """The Repair describes a state that a successful handshake disproves."""
    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
    async_raise_ca_changed(hass, entry, "aa" * 32, "bb" * 32)

    async_clear_ca_changed(hass, entry)

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, ca_changed_issue_id(entry.entry_id))
        is None
    )


@pytest.mark.asyncio
async def test_the_fix_flow_re_pins_only_on_an_explicit_confirmation(
    hass: HomeAssistant,
) -> None:
    """Showing the fingerprint must not itself accept it."""
    from custom_components.span_panel.repairs import PanelCAChangedRepairFlow

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM, CONF_HTTP_PORT: 8080})
    flow = PanelCAChangedRepairFlow(entry.entry_id)
    flow.hass = hass

    with patch(
        "custom_components.span_panel.repairs.async_fetch_panel_ca",
        new=AsyncMock(return_value=OTHER_PEM),
    ) as fetch:
        shown = await flow.async_step_init()

    fetch.assert_awaited_once_with(hass, "192.168.1.100", http_port=8080)
    assert shown["type"] == "form"
    assert shown["description_placeholders"] is not None
    # The fingerprint offered is of the certificate that would actually be
    # stored, not a record of an earlier observation.
    assert shown["description_placeholders"]["fingerprint"]
    # Nothing has been accepted yet.
    assert entry.data[CONF_PANEL_CA_PEM] == PEM

    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        done = await flow.async_step_confirm({})

    assert done["type"] == "create_entry"
    assert entry.data[CONF_PANEL_CA_PEM] == OTHER_PEM
    reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_the_fix_flow_aborts_when_the_panel_cannot_be_read(
    hass: HomeAssistant,
) -> None:
    """A fingerprint that cannot be read is not one a user can be asked to accept."""
    from custom_components.span_panel.repairs import PanelCAChangedRepairFlow

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
    flow = PanelCAChangedRepairFlow(entry.entry_id)
    flow.hass = hass

    with patch(
        "custom_components.span_panel.repairs.async_fetch_panel_ca",
        new=AsyncMock(side_effect=SpanPanelConnectionError("unreachable")),
    ):
        result = await flow.async_step_init()

    assert result["type"] == "abort"
    assert result["reason"] == "ca_unreadable"
    assert entry.data[CONF_PANEL_CA_PEM] == PEM


@pytest.mark.asyncio
async def test_the_coordinator_takes_entities_unavailable_on_a_ca_change(
    hass: HomeAssistant,
) -> None:
    """A dead transport must not keep serving the snapshot read before it died."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    from custom_components.span_panel.coordinator import SpanPanelCoordinator

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
    client = MagicMock()
    client.get_snapshot = AsyncMock(
        side_effect=SpanPanelCAChangedError("aa" * 32, "bb" * 32)
    )
    coordinator = SpanPanelCoordinator(hass, client, entry)
    # A previous good snapshot, which the ordinary offline path would keep serving.
    coordinator.data = MagicMock()

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.panel_offline is True


# ---------- the transport those credentials travel over ----------


@pytest.mark.asyncio
async def test_an_unpinned_entry_uses_the_plaintext_bootstrap_port(
    hass: HomeAssistant,
) -> None:
    """No pin, no context: exactly the transport this integration always had."""
    from custom_components.span_panel.config_flow_validation import panel_rest_transport

    entry = _entry(hass, **{CONF_HTTP_PORT: 8080})

    transport = panel_rest_transport(hass, entry.data)

    assert transport.port == 8080
    assert transport.ssl_context is None
    # Home Assistant's shared client, which it owns and closes at shutdown.
    assert transport.httpx_client is not None


@pytest.mark.asyncio
async def test_a_pinned_entry_moves_to_tls_and_drops_the_shared_client(
    hass: HomeAssistant,
) -> None:
    """httpx fixes its trust store at construction, so a pin needs its own client."""
    from custom_components.span_panel.config_flow_validation import panel_rest_transport

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM, CONF_HTTP_PORT: 8080})
    context = MagicMock()

    with patch(
        "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
        return_value=context,
    ):
        transport = panel_rest_transport(hass, entry.data)

    # 443, not the stored plaintext 8080 — the library refuses port 80 with a
    # context outright, and a plaintext port under TLS is never what was meant.
    assert transport.port == 443
    assert transport.ssl_context is context
    assert transport.httpx_client is None


@pytest.mark.asyncio
async def test_a_pinned_entry_honours_a_configured_https_port(
    hass: HomeAssistant,
) -> None:
    """A reverse proxy does not have to listen on 443."""
    from custom_components.span_panel.config_flow_validation import panel_rest_transport
    from custom_components.span_panel.const import CONF_HTTPS_PORT

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM, CONF_HTTPS_PORT: 9443})

    with patch(
        "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
        return_value=MagicMock(),
    ):
        transport = panel_rest_transport(hass, entry.data)

    assert transport.port == 9443


@pytest.mark.asyncio
async def test_an_unusable_stored_pem_falls_back_rather_than_bricking_the_entry(
    hass: HomeAssistant,
) -> None:
    """A hand-edited `.storage` must not leave an entry unable to make a call."""
    import ssl

    from custom_components.span_panel.config_flow_validation import panel_rest_transport

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: "not a certificate"})

    with patch(
        "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
        side_effect=ssl.SSLError("nope"),
    ):
        transport = panel_rest_transport(hass, entry.data)

    assert transport.ssl_context is None
    assert transport.port == 80


@pytest.mark.asyncio
async def test_rotation_goes_over_the_pin_when_the_entry_has_one(
    hass: HomeAssistant,
) -> None:
    """Fresh secrets over unverified HTTP would undo the pin where it matters most."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.core import Context

    from custom_components.span_panel import (
        SpanPanelRuntimeData,
        _async_register_credential_services,
    )
    from custom_components.span_panel.const import CONF_EBUS_BROKER_PASSWORD

    from pytest_homeassistant_custom_component.common import MockUser

    entry = _entry(
        hass,
        **{CONF_PANEL_CA_PEM: PEM, CONF_EBUS_BROKER_PASSWORD: "old", CONF_HTTP_PORT: 8080},
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=MagicMock(), panel_device_id="panel-device-id"
    )
    _async_register_credential_services(hass)

    context = MagicMock()
    user = MockUser(is_owner=True).add_to_hass(hass)
    with (
        patch(
            "custom_components.span_panel.config_flow_validation.build_panel_ssl_context",
            return_value=context,
        ),
        patch(
            "custom_components.span_panel.services.regenerate_passphrase",
            new=AsyncMock(return_value="new"),
        ) as rotate,
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()),
    ):
        await hass.services.async_call(
            DOMAIN,
            "rotate_credentials",
            {},
            blocking=True,
            context=Context(user_id=user.id),
        )

    assert rotate.await_args is not None
    assert rotate.await_args.kwargs["ssl_context"] is context
    assert rotate.await_args.kwargs["port"] == 443
    assert rotate.await_args.kwargs["httpx_client"] is None
    assert entry.data[CONF_EBUS_BROKER_PASSWORD] == "new"
