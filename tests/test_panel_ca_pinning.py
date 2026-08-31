"""Tests for pinning the panel's CA: acquisition, deferral, and the change repair."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
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
    PANEL_STATUS,
)
from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.curation import CurationOverlay
from custom_components.span_panel.sensor_circuit import SpanCircuitPowerSensor
from custom_components.span_panel.sensor_definitions import CIRCUIT_SENSORS

from .factories import SpanCircuitSnapshotFactory, SpanPanelSnapshotFactory

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

    with patch("custom_components.span_panel.async_fetch_panel_ca", new=AsyncMock()) as fetch:
        assert await _async_pinned_ca(hass, entry, "192.168.1.100", 80) == PEM

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_deferred_fetch_stores_the_ca_and_clears_the_flag(
    hass: HomeAssistant,
) -> None:
    """A migrated entry acquires its CA at the first setup that reaches the panel.

    The leaf check is stood down here and exercised for real in
    `test_panel_ca_pinning_tls`: this is about the bookkeeping either side of it.
    """
    entry = _entry(hass, **{PANEL_CA_PENDING: True})

    with (
        patch(
            "custom_components.span_panel.async_fetch_panel_ca",
            new=AsyncMock(return_value=PEM),
        ) as fetch,
        patch(
            "custom_components.span_panel.async_ca_signs_panel_leaf",
            new=AsyncMock(return_value=True),
        ),
    ):
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

    with patch("custom_components.span_panel.async_fetch_panel_ca", new=AsyncMock()) as fetch:
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
async def test_non_v2_entries_are_never_flagged(hass: HomeAssistant, api_version: str) -> None:
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

    assert ir.async_get(hass).async_get_issue(DOMAIN, ca_changed_issue_id(entry.entry_id)) is None


@pytest.mark.asyncio
async def test_the_fix_flow_re_pins_only_on_an_explicit_confirmation(
    hass: HomeAssistant,
) -> None:
    """Showing the fingerprint must not itself accept it."""
    from custom_components.span_panel.repairs import PanelCAChangedRepairFlow

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM, CONF_HTTP_PORT: 8080})
    flow = PanelCAChangedRepairFlow(entry.entry_id)
    flow.hass = hass

    with (
        patch(
            "custom_components.span_panel.repairs.async_fetch_panel_ca",
            new=AsyncMock(return_value=OTHER_PEM),
        ) as fetch,
        # Stood down here and exercised for real in `test_panel_ca_pinning_tls`;
        # this test is about what an explicit confirmation does and does not do.
        patch(
            "custom_components.span_panel.repairs.async_ca_signs_panel_leaf",
            new=AsyncMock(return_value=True),
        ),
    ):
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


# ---------- a transport that has stopped for good ----------


def _panel_reading_1200_watts(
    hass: HomeAssistant,
) -> tuple[SpanPanelCoordinator, SpanCircuitPowerSensor]:
    """Build a live entry whose next read finds the panel behind a different CA.

    The sensor is a real one on a real coordinator: the defect was in how the
    two of them agreed on availability, so a mock of either proves nothing.
    """
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="c1", name="Kitchen Outlets", instant_power_w=1200.0
    )
    snapshot = SpanPanelSnapshotFactory.create(circuits={"c1": circuit})

    entry = _entry(hass, **{CONF_PANEL_CA_PEM: PEM})
    client = MagicMock()
    client.get_snapshot = AsyncMock(side_effect=SpanPanelCAChangedError("aa" * 32, "bb" * 32))
    coordinator = SpanPanelCoordinator(hass, client, entry)
    # A previous good snapshot, which the ordinary offline path would keep serving.
    coordinator.data = snapshot

    power = next(desc for desc in CIRCUIT_SENSORS if desc.key == "circuit_power")
    return coordinator, SpanCircuitPowerSensor(coordinator, power, snapshot, "c1")


@pytest.mark.asyncio
async def test_the_coordinator_takes_entities_unavailable_on_a_ca_change(
    hass: HomeAssistant,
) -> None:
    """A dead transport must not keep serving the snapshot read before it died."""
    coordinator, _ = _panel_reading_1200_watts(hass)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    # Dead, not merely offline: the offline flag is what keeps sensors serving
    # the last snapshot, and a transport that is not coming back must not.
    assert coordinator.transport_dead is True
    assert coordinator.panel_offline is False


@pytest.mark.asyncio
async def test_a_power_sensor_goes_unavailable_rather_than_reporting_zero(
    hass: HomeAssistant,
) -> None:
    """The defect: a CA change left every POWER sensor 'available' at 0 W."""
    coordinator, sensor = _panel_reading_1200_watts(hass)

    sensor._update_native_value()
    assert sensor.available is True
    assert sensor.native_value == 1200.0

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    sensor._update_native_value()

    assert sensor.available is False
    assert sensor.native_value != 0.0


@pytest.mark.asyncio
async def test_the_fatal_channel_takes_the_entities_down_without_waiting_for_a_poll(
    hass: HomeAssistant,
) -> None:
    """The mid-session route in: no exception reaches anybody's call stack.

    The reconnect loop is fire-and-forget, so the only notification is this
    callback. Without it the entities keep rendering the last snapshot until
    the fallback poll comes round.
    """
    coordinator, sensor = _panel_reading_1200_watts(hass)
    coordinator.client.start_streaming = AsyncMock()
    await coordinator.async_setup_streaming()
    on_fatal = coordinator.client.register_fatal_error_callback.call_args.args[0]

    rendered: list[None] = []
    # Listening is also what starts the coordinator's refresh timer, hence the
    # unsubscribe below.
    unsubscribe = coordinator.async_add_listener(lambda: rendered.append(None))

    on_fatal(SpanPanelCAChangedError("aa" * 32, "bb" * 32))

    assert coordinator.transport_dead is True
    assert sensor.available is False
    # Told at once, rather than a minute later: nothing else fires here.
    assert rendered == [None]
    unsubscribe()


@pytest.mark.asyncio
async def test_an_ordinary_disconnect_still_holds_the_last_reading(
    hass: HomeAssistant,
) -> None:
    """The grace period is for outages, and this fix must not have taken it."""
    coordinator, sensor = _panel_reading_1200_watts(hass)

    coordinator._on_connection_change(False)

    assert coordinator.panel_offline is True
    assert coordinator.transport_dead is False
    assert sensor.available is True


@pytest.mark.asyncio
async def test_a_reconnection_brings_the_entities_back(hass: HomeAssistant) -> None:
    """Dead is not permanent -- it is 'until something connects again'."""
    coordinator, sensor = _panel_reading_1200_watts(hass)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert sensor.available is False

    coordinator._on_connection_change(True)

    assert coordinator.transport_dead is False
    assert sensor.available is True


@pytest.mark.asyncio
async def test_a_held_hardware_reading_and_a_control_go_with_the_transport(
    hass: HomeAssistant,
) -> None:
    """The other two shapes of availability, both wrong for a dead transport.

    `door_state` returns True on the offline branch so it can render Unknown
    through an outage, and the switch returns False there because a control
    that cannot reach the panel is not a control. Neither branch is consulted
    now: the transport probe settles both ahead of them.
    """
    from custom_components.span_panel.binary_sensor import BINARY_SENSORS, SpanPanelBinarySensor
    from custom_components.span_panel.const import SYSTEM_DOOR_STATE
    from custom_components.span_panel.switch import SpanPanelCircuitsSwitch

    coordinator, _ = _panel_reading_1200_watts(hass)
    door = SpanPanelBinarySensor(
        coordinator, next(desc for desc in BINARY_SENSORS if desc.key == SYSTEM_DOOR_STATE)
    )
    relay = SpanPanelCircuitsSwitch(coordinator, "c1", "SPAN Panel")
    assert door.available is True
    assert relay.available is True

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert door.available is False
    assert relay.available is False


@pytest.mark.asyncio
async def test_the_connectivity_sensor_reports_disconnected_rather_than_vanishing(
    hass: HomeAssistant,
) -> None:
    """The one entity that must survive the condition it reports.

    `panel_status` is exempt from the transport probe, so it has to read the
    flag itself -- otherwise it would sit at 'connected' describing a transport
    that has stopped for good.
    """
    from custom_components.span_panel.binary_sensor import BINARY_SENSORS, SpanPanelBinarySensor

    coordinator, _ = _panel_reading_1200_watts(hass)
    description = next(desc for desc in BINARY_SENSORS if desc.key == PANEL_STATUS)
    status = SpanPanelBinarySensor(coordinator, description)
    status.async_write_ha_state = MagicMock()

    status._handle_coordinator_update()
    assert status.is_on is True

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    status._handle_coordinator_update()

    assert status.available is True
    assert status.is_on is False


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
    """A pin needs its own client: httpx fixes its trust store at construction."""
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
    from pytest_homeassistant_custom_component.common import MockUser

    from custom_components.span_panel import (
        SpanPanelRuntimeData,
        _async_register_credential_services,
    )
    from custom_components.span_panel.const import CONF_EBUS_BROKER_PASSWORD

    entry = _entry(
        hass,
        **{CONF_PANEL_CA_PEM: PEM, CONF_EBUS_BROKER_PASSWORD: "old", CONF_HTTP_PORT: 8080},
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SpanPanelRuntimeData(
        coordinator=MagicMock(),
        panel_device_id="panel-device-id",
        curation=CurationOverlay.empty(),
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
