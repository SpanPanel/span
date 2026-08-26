"""Tests for the Span Panel coordinator."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from span_panel_api import (
    AdoptedDevice,
    AdoptedProperty,
    ExtensionProperty,
    ExtensionSubject,
    SpanMqttClient,
)
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelServerError,
    SpanPanelTimeoutError,
)
from span_panel_api.models import FieldMetadata
from span_panel_api.protocol import SpanPanelClientProtocol

from custom_components.span_panel.coordinator import SpanPanelCoordinator
from custom_components.span_panel.helpers import (
    adopted_capability_tokens,
    circuit_has_a_breaker_switch,
    circuit_has_a_priority_select,
    detect_capabilities,
)

from .adapter_fixtures import schema_one_snapshot
from .factories import (
    SpanBatterySnapshotFactory,
    SpanCircuitSnapshotFactory,
    SpanEvseSnapshotFactory,
    SpanPanelSnapshotFactory,
)


def _create_coordinator(
    hass: HomeAssistant,
    *,
    client: object | None = None,
    options: dict | None = None,
) -> SpanPanelCoordinator:
    """Create a coordinator with mocked dependencies."""
    return SpanPanelCoordinator(
        hass,
        cast(SpanMqttClient, client or MagicMock()),
        MockConfigEntry(
            domain="span_panel",
            options=options or {},
            entry_id="entry-123",
            title="SPAN Panel",
        ),
    )


async def test_capability_change_requests_reload(hass: HomeAssistant) -> None:
    """A new hardware capability should trigger a reload request."""
    coordinator = _create_coordinator(hass)

    baseline = SpanPanelSnapshotFactory.create()
    upgraded = SpanPanelSnapshotFactory.create(
        battery=SpanBatterySnapshotFactory.create(soe_percentage=88.0),
        power_flow_pv=1250.0,
        power_flow_site=3000.0,
        evse={"evse-0": SpanEvseSnapshotFactory.create()},
    )

    coordinator._check_capability_change(baseline)
    assert coordinator._known_capabilities == frozenset()
    assert coordinator._reload_requested is False

    coordinator._check_capability_change(upgraded)

    assert coordinator._known_capabilities == frozenset(
        {"bess", "evse", "power_flows", "pv"}
    )
    assert coordinator._reload_requested is True


async def test_async_update_data_returns_cached_snapshot_on_connection_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A transient connection error should return cached data when available."""
    client = MagicMock()
    client.get_snapshot = AsyncMock(
        side_effect=SpanPanelConnectionError("panel offline")
    )
    coordinator = _create_coordinator(hass, client=client)
    cached_snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-cached-001")
    coordinator.data = cached_snapshot

    caplog.set_level(logging.INFO)

    result = await coordinator._async_update_data()

    assert result is cached_snapshot
    assert coordinator.panel_offline is True
    assert "SPAN Panel is unavailable: panel offline" in caplog.text


async def test_async_update_data_raises_auth_failed(hass: HomeAssistant) -> None:
    """Authentication failures should be promoted to config-entry auth errors."""
    client = MagicMock()
    client.get_snapshot = AsyncMock(side_effect=SpanPanelAuthError("bad auth"))
    coordinator = _create_coordinator(hass, client=client)

    with pytest.raises(Exception) as err:
        await coordinator._async_update_data()

    assert err.type.__name__ == "ConfigEntryAuthFailed"


async def test_run_post_update_tasks_validates_once_and_schedules_reload(
    hass: HomeAssistant,
) -> None:
    """Post-update tasks should validate once and schedule a requested reload."""
    coordinator = _create_coordinator(hass)
    snapshot = SpanPanelSnapshotFactory.create()
    coordinator._reload_requested = True

    # The real `_run_schema_validation` sets the guard itself, and only once it
    # has actually produced findings; the stand-in has to emulate that or the
    # second pass would legitimately retry. `_a_none_first_pass...` below covers
    # the retry side.
    def _succeed() -> None:
        coordinator._schema_validated = True

    with (
        patch.object(
            coordinator, "_run_schema_validation", side_effect=_succeed
        ) as mock_validate,
        patch.object(coordinator, "_fire_dip_notification", AsyncMock()) as mock_notify,
        patch.object(coordinator, "_async_reload_task", AsyncMock()) as mock_reload,
        patch.object(hass, "async_create_task") as mock_create_task,
    ):
        await coordinator._run_post_update_tasks(snapshot)
        await coordinator._run_post_update_tasks(snapshot)

    assert mock_validate.call_count == 1
    assert mock_notify.await_count == 2
    assert mock_create_task.call_count == 1
    reload_coro = mock_create_task.call_args.args[0]
    reload_coro.close()
    mock_reload.assert_called_once()
    assert coordinator._reload_requested is False


async def test_async_shutdown_unregisters_streaming_and_closes_client(
    hass: HomeAssistant,
) -> None:
    """Shutdown should unregister streaming and close the client."""
    client = MagicMock()
    client.stop_streaming = AsyncMock()
    client.close = AsyncMock()
    unregister = MagicMock()
    coordinator = _create_coordinator(hass, client=client)
    coordinator._unregister_streaming = unregister

    await coordinator.async_shutdown()

    unregister.assert_called_once()
    client.stop_streaming.assert_awaited_once()
    client.close.assert_awaited_once()
    assert coordinator._unregister_streaming is None


async def test_report_dip_and_fire_notification_clears_events(
    hass: HomeAssistant,
) -> None:
    """Dip notifications should summarize and clear pending events."""
    coordinator = _create_coordinator(hass)
    coordinator.report_energy_dip("sensor.a", 2.5, 4.0)
    coordinator.report_energy_dip("sensor.b", 1.0, 1.5)

    with patch(
        "custom_components.span_panel.coordinator.async_create"
    ) as mock_create:
        await coordinator._fire_dip_notification()

    mock_create.assert_called_once()
    body = mock_create.call_args.args[1]
    assert "sensor.a" in body
    assert "dip 2.5 Wh" in body
    assert coordinator._pending_dip_events == []


async def test_fire_dip_notification_noops_without_events(hass: HomeAssistant) -> None:
    """No notification should be created when there are no pending dips."""
    coordinator = _create_coordinator(hass)

    with patch(
        "custom_components.span_panel.coordinator.async_create"
    ) as mock_create:
        await coordinator._fire_dip_notification()

    mock_create.assert_not_called()


async def test_async_setup_streaming_registers_callback_and_starts_client(
    hass: HomeAssistant,
) -> None:
    """Streaming setup should register both callbacks and start the client."""
    client = MagicMock()
    unregister_connection = MagicMock()
    client.register_connection_callback = MagicMock(return_value=unregister_connection)
    client.register_snapshot_callback = MagicMock(return_value=MagicMock())
    client.start_streaming = AsyncMock()
    coordinator = _create_coordinator(hass, client=client)

    await coordinator.async_setup_streaming()

    client.register_snapshot_callback.assert_called_once()
    client.start_streaming.assert_awaited_once()
    assert coordinator._unregister_streaming is not None
    client.register_connection_callback.assert_called_once_with(coordinator._on_connection_change)
    assert coordinator._unregister_connection is not None


async def test_on_snapshot_push_updates_state_and_runs_post_tasks(
    hass: HomeAssistant,
) -> None:
    """Push snapshots should update coordinator data and run maintenance."""
    coordinator = _create_coordinator(hass)
    coordinator._panel_offline = True
    snapshot = SpanPanelSnapshotFactory.create()

    with (
        patch.object(coordinator, "_check_capability_change") as mock_caps,
        patch.object(coordinator, "async_set_updated_data") as mock_set,
        patch.object(coordinator, "_run_post_update_tasks", AsyncMock()) as mock_post,
    ):
        await coordinator._on_snapshot_push(snapshot)

    assert coordinator.panel_offline is False
    mock_caps.assert_called_once_with(snapshot)
    mock_set.assert_called_once_with(snapshot)
    mock_post.assert_awaited_once_with(snapshot)


async def test_run_schema_validation_skips_without_metadata(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """No metadata means "unknown", so the pass must leave findings untouched.

    `field_metadata` is None for the whole _on_pre_rebuild -> retained-message
    window, which an ordinary reconnect opens. Producing empty findings here
    would read as "every issue is resolved" to the Repairs reconciler.
    """
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = None
    coordinator = _create_coordinator(hass, client=client)

    caplog.set_level(logging.DEBUG)
    coordinator._run_schema_validation()

    assert "Schema validation skipped" in caplog.text
    assert coordinator.schema_findings is None
    assert coordinator.unresolved_paths == frozenset()


async def test_run_schema_validation_preserves_prior_findings(
    hass: HomeAssistant,
) -> None:
    """A later pass without metadata must not erase what an earlier one found."""
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = {
        "circuit.instant_power_w": FieldMetadata(None, "unknown", resolved=False)
    }
    coordinator = _create_coordinator(hass, client=client)

    coordinator._run_schema_validation()
    assert coordinator.unresolved_paths == frozenset({"circuit.instant_power_w"})

    client.field_metadata = None
    coordinator._run_schema_validation()

    assert coordinator.unresolved_paths == frozenset({"circuit.instant_power_w"})


async def test_run_schema_validation_reads_metadata_through_the_protocol(
    hass: HomeAssistant,
) -> None:
    """The unit cross-check must key sensor definitions by field path.

    Not `circuit.instant_power_w`/"kW": that pair is in
    `KNOWN_BAD_SCHEMA_UNITS`, so it would prove the exception, not the wiring.
    """
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = {"panel.l1_voltage": FieldMetadata("kV", "float")}
    coordinator = _create_coordinator(hass, client=client)

    coordinator._run_schema_validation()

    findings = coordinator.schema_findings
    assert findings is not None
    assert [m.field_path for m in findings.unit_mismatches] == ["panel.l1_voltage"]
    assert findings.unit_mismatches[0].schema_unit == "kV"
    assert findings.unit_mismatches[0].ha_unit == "V"


@pytest.mark.parametrize(
    ("error", "expected_log"),
    [
        (SpanPanelTimeoutError("timeout"), "SPAN Panel is unavailable: timeout"),
        (SpanPanelServerError("server"), "SPAN Panel is unavailable: server"),
        (SpanPanelAPIError("api"), "SPAN Panel is unavailable: api"),
        (RuntimeError("boom"), "SPAN Panel is unavailable: boom"),
    ],
)
async def test_async_update_data_raises_first_error_without_cached_data(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_log: str,
) -> None:
    """First-refresh errors should be re-raised after logging."""
    client = MagicMock()
    client.get_snapshot = AsyncMock(side_effect=error)
    coordinator = _create_coordinator(hass, client=client)

    caplog.set_level(logging.INFO)

    with pytest.raises(type(error)):
        await coordinator._async_update_data()

    assert coordinator.panel_offline is True
    assert expected_log in caplog.text


async def test_async_update_data_re_raises_existing_auth_failed(
    hass: HomeAssistant,
) -> None:
    """Existing auth failures should pass through untouched."""
    client = MagicMock()
    client.get_snapshot = AsyncMock(side_effect=ConfigEntryAuthFailed)
    coordinator = _create_coordinator(hass, client=client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_async_update_data_logs_unavailable_and_recovery_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Coordinator should log offline and recovery transitions once each."""
    cached_snapshot = SpanPanelSnapshotFactory.create(serial_number="sp3-cached-001")
    recovered_snapshot = SpanPanelSnapshotFactory.create(
        serial_number="sp3-recovered-001"
    )
    client = MagicMock()
    client.get_snapshot = AsyncMock(
        side_effect=[
            SpanPanelConnectionError("panel offline"),
            SpanPanelConnectionError("still offline"),
            recovered_snapshot,
        ]
    )
    coordinator = _create_coordinator(hass, client=client)
    coordinator.data = cached_snapshot

    caplog.set_level(logging.INFO)

    assert await coordinator._async_update_data() is cached_snapshot
    assert await coordinator._async_update_data() is cached_snapshot
    assert await coordinator._async_update_data() is recovered_snapshot

    assert caplog.text.count("SPAN Panel is unavailable: panel offline") == 1
    assert "SPAN Panel is unavailable: still offline" not in caplog.text
    assert caplog.text.count("SPAN Panel is back online") == 1


async def test_async_reload_task_handles_expected_errors(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Reload task should log and suppress reload-related errors."""
    coordinator = _create_coordinator(hass)

    caplog.set_level(logging.WARNING)

    with (
        patch.object(hass, "async_block_till_done", AsyncMock()),
        patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(side_effect=ConfigEntryNotReady("not ready")),
        ),
    ):
        await coordinator._async_reload_task()

    assert "Config entry not ready during reload: not ready" in caplog.text

    caplog.clear()
    caplog.set_level(logging.ERROR)

    with (
        patch.object(hass, "async_block_till_done", AsyncMock()),
        patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(side_effect=HomeAssistantError("reload failed")),
        ),
    ):
        await coordinator._async_reload_task()

    assert "Home Assistant error during reload: reload failed" in caplog.text


async def test_connection_callback_registered_and_unregistered_on_lifecycle(
    hass: HomeAssistant,
) -> None:
    """async_setup_streaming should register a connection callback; async_shutdown should unregister it."""
    client = MagicMock()
    client.register_connection_callback = MagicMock()
    client.register_snapshot_callback = MagicMock()
    client.start_streaming = AsyncMock()
    client.stop_streaming = AsyncMock()
    client.close = AsyncMock()

    # register_connection_callback returns an unregister function
    unregister_connection = MagicMock()
    client.register_connection_callback.return_value = unregister_connection
    client.register_snapshot_callback.return_value = MagicMock()

    coordinator = _create_coordinator(hass, client=client)

    await coordinator.async_setup_streaming()

    # Connection callback was registered exactly once with the coordinator's handler
    client.register_connection_callback.assert_called_once_with(coordinator._on_connection_change)
    assert coordinator._unregister_connection is unregister_connection

    await coordinator.async_shutdown()

    # Unregister was invoked and the field cleared
    cast(MagicMock, unregister_connection).assert_called_once_with()
    assert coordinator._unregister_connection is None


async def test_on_connection_change_false_flips_offline_and_notifies_listeners(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A False edge must flip panel_offline True, log once, and push a listener update."""
    coordinator = _create_coordinator(hass)
    assert coordinator.panel_offline is False

    with (
        patch.object(coordinator, "async_update_listeners") as notify,
        caplog.at_level(logging.INFO),
    ):
        coordinator._on_connection_change(False)

    assert coordinator.panel_offline is True
    notify.assert_called_once_with()
    assert any(
        "is unavailable" in r.message and "MQTT broker disconnected" in r.message
        for r in caplog.records
    )


async def test_on_connection_change_true_clears_offline_and_notifies_listeners(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A True edge must flip panel_offline False, log once, and push a listener update."""
    coordinator = _create_coordinator(hass)
    coordinator._panel_offline = True

    with (
        patch.object(coordinator, "async_update_listeners") as notify,
        caplog.at_level(logging.INFO),
    ):
        coordinator._on_connection_change(True)

    assert coordinator.panel_offline is False
    notify.assert_called_once_with()
    assert any("is back online" in r.message for r in caplog.records)


async def test_on_connection_change_noop_when_state_unchanged(
    hass: HomeAssistant,
) -> None:
    """When connected state matches current panel_offline, no listener fan-out."""
    coordinator = _create_coordinator(hass)

    # Already online (panel_offline=False); receiving another True edge is a no-op
    with patch.object(coordinator, "async_update_listeners") as notify_online_case:
        coordinator._on_connection_change(True)
    notify_online_case.assert_not_called()
    assert coordinator.panel_offline is False

    # Already offline; receiving another False edge is a no-op
    coordinator._panel_offline = True
    with patch.object(coordinator, "async_update_listeners") as notify_offline_case:
        coordinator._on_connection_change(False)
    notify_offline_case.assert_not_called()
    assert coordinator.panel_offline is True


async def test_async_update_data_stale_data_error_marks_offline_and_returns_last_data(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A SpanPanelStaleDataError from get_snapshot() should be treated as an expected offline signal."""
    from span_panel_api.exceptions import SpanPanelStaleDataError

    last_snapshot = SpanPanelSnapshotFactory.create()

    client = MagicMock()
    client.get_snapshot = AsyncMock(
        side_effect=SpanPanelStaleDataError("MQTT broker disconnected")
    )

    coordinator = _create_coordinator(hass, client=client)
    # Simulate a prior successful update
    coordinator.data = last_snapshot
    assert coordinator.panel_offline is False

    with caplog.at_level(logging.INFO):
        result = await coordinator._async_update_data()

    assert result is last_snapshot
    assert coordinator.panel_offline is True
    assert any(
        "is unavailable" in r.message and "MQTT broker disconnected" in r.message
        for r in caplog.records
    )


async def test_entities_register_themselves_against_the_field_they_read(
    hass: HomeAssistant,
) -> None:
    """The affected-entity map is recorded by entities, never derived from them.

    Three unique_id builders are in play and they disagree, so reconstructing
    entity ids from entity descriptions reported "0 affected" for most fields.
    tests/test_schema_repairs.py drives real entities through a real platform;
    this pins the coordinator side of the contract.
    """
    coordinator = _create_coordinator(hass)

    coordinator.async_register_field_path_entity("panel.door_state", "binary_sensor.door")
    coordinator.async_register_field_path_entity("circuit.instant_power_w", "sensor.b")
    coordinator.async_register_field_path_entity("circuit.instant_power_w", "sensor.a")
    coordinator.async_register_field_path_entity("circuit.instant_power_w", "sensor.a")

    assert coordinator.entity_ids_by_field_path == {
        "panel.door_state": ["binary_sensor.door"],
        "circuit.instant_power_w": ["sensor.a", "sensor.b"],
    }

    coordinator.async_unregister_field_path_entity("circuit.instant_power_w", "sensor.a")
    coordinator.async_unregister_field_path_entity("panel.door_state", "binary_sensor.door")
    # Unknown pairs are ignored rather than raising: removal can outlive setup.
    coordinator.async_unregister_field_path_entity("panel.door_state", "binary_sensor.gone")

    assert coordinator.entity_ids_by_field_path == {
        "circuit.instant_power_w": ["sensor.b"]
    }


async def test_sync_schema_repairs_raises_a_repair_naming_the_dead_entities(
    hass: HomeAssistant,
) -> None:
    """The whole point of the feature: a dead field names the sensors it killed."""
    from homeassistant.helpers import issue_registry as ir

    entry = MockConfigEntry(domain="span_panel", entry_id="entry-affected")
    entry.add_to_hass(hass)
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = {
        "circuit.instant_power_w": FieldMetadata(None, "unknown", resolved=False)
    }
    coordinator = SpanPanelCoordinator(hass, cast(SpanMqttClient, client), entry)
    coordinator.async_register_field_path_entity(
        "circuit.instant_power_w", "sensor.span_panel_kitchen_power"
    )

    coordinator._run_schema_validation()
    coordinator.async_sync_schema_repairs()

    issue = ir.async_get(hass).async_get_issue(
        "span_panel", "unresolved_entry-affected_circuit.instant_power_w"
    )
    assert issue is not None
    assert issue.translation_placeholders["count"] == "1"
    assert issue.translation_placeholders["examples"] == "sensor.span_panel_kitchen_power"


async def test_sync_schema_repairs_is_a_no_op_while_findings_are_unknown(
    hass: HomeAssistant,
) -> None:
    """"Unknown" must not reconcile at all.

    `field_metadata` is None for the whole retained-message window, which an
    ordinary reconnect opens. Reconciling against no findings would delete every
    schema issue, and with it every dismissal the user has made.
    """
    from homeassistant.helpers import issue_registry as ir

    entry = MockConfigEntry(domain="span_panel", entry_id="entry-unknown")
    entry.add_to_hass(hass)
    ir.async_create_issue(
        hass,
        "span_panel",
        "unresolved_entry-unknown_circuit.instant_power_w",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="schema_field_unresolved",
        translation_placeholders={"field_path": "x", "count": "0", "examples": "none"},
    )

    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = None
    coordinator = SpanPanelCoordinator(hass, cast(SpanMqttClient, client), entry)

    coordinator._run_schema_validation()
    coordinator.async_sync_schema_repairs()

    assert coordinator.schema_findings is None
    assert ir.async_get(hass).async_get_issue(
        "span_panel", "unresolved_entry-unknown_circuit.instant_power_w"
    )


async def test_a_none_first_pass_does_not_disable_validation_for_the_session(
    hass: HomeAssistant,
) -> None:
    """One unlucky first pass must not silence the feature until the next reload.

    `field_metadata` is None for the whole not-ready / pre-rebuild window, which
    an ordinary reconnect opens. Setting the once-only guard before validation
    ran meant that a first pass landing in that window left findings at None
    forever: the guard was set, later passes skipped, and the single reconcile at
    setup had nothing to raise. Zero issues, for the life of the entry.
    """
    from homeassistant.helpers import issue_registry as ir

    entry = MockConfigEntry(domain="span_panel", entry_id="entry-late")
    entry.add_to_hass(hass)
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = None
    coordinator = SpanPanelCoordinator(hass, cast(SpanMqttClient, client), entry)
    coordinator.async_register_field_path_entity(
        "circuit.instant_power_w", "sensor.kitchen_power"
    )
    snapshot = SpanPanelSnapshotFactory.create()

    with patch.object(coordinator, "_fire_dip_notification", AsyncMock()):
        # The pass that happens during setup's first refresh — metadata not ready.
        await coordinator._run_post_update_tasks(snapshot)
        assert coordinator.schema_findings is None
        assert coordinator._schema_validated is False

        # Setup finishes and reconciles; there is nothing to raise yet.
        coordinator.async_sync_schema_repairs()
        assert not [k for k in ir.async_get(hass).issues if k[0] == "span_panel"]

        # Metadata arrives, and the panel turns out to be degraded.
        client.field_metadata = {
            "circuit.instant_power_w": FieldMetadata(None, "unknown", resolved=False)
        }
        for _ in range(5):
            await coordinator._run_post_update_tasks(snapshot)

    assert coordinator.schema_findings is not None
    assert coordinator.unresolved_paths == frozenset({"circuit.instant_power_w"})

    issue = ir.async_get(hass).async_get_issue(
        "span_panel", "unresolved_entry-late_circuit.instant_power_w"
    )
    assert issue is not None
    assert issue.translation_placeholders["count"] == "1"
    assert issue.translation_placeholders["examples"] == "sensor.kitchen_power"


async def test_validation_runs_at_most_once_successfully(hass: HomeAssistant) -> None:
    """Metadata is static within a session; re-reading it every pass is waste.

    The retry above must not turn into revalidation. Once a pass has produced
    findings, later passes leave them alone.
    """
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = {
        "circuit.instant_power_w": FieldMetadata(None, "unknown", resolved=False)
    }
    coordinator = _create_coordinator(hass, client=client)
    snapshot = SpanPanelSnapshotFactory.create()

    with patch.object(coordinator, "_fire_dip_notification", AsyncMock()):
        await coordinator._run_post_update_tasks(snapshot)
        assert coordinator.unresolved_paths == frozenset({"circuit.instant_power_w"})

        # A later pass sees different metadata and must not act on it.
        client.field_metadata = {}
        for _ in range(3):
            await coordinator._run_post_update_tasks(snapshot)

    assert coordinator.unresolved_paths == frozenset({"circuit.instant_power_w"})


def test_the_reload_trigger_sees_every_capability_the_platforms_gate_on() -> None:
    """The coordinator must not derive its own, narrower capability set.

    It used to. `_detect_capabilities` was a hand-rolled copy that knew only
    bess/pv/power_flows/evse, so a panel that gained `mid`, `shed_forecast`,
    `bess_telemetry`, `pcs` or `der_link_health` on a firmware upgrade
    published the properties, created no entities, and asked for no reload --
    the user saw nothing until they restarted Home Assistant.

    Asserting equality on a snapshot that exercises the capabilities is what
    catches a re-divergence; asserting the two names are the same object would
    pass the moment someone re-inlined the logic.
    """
    snapshot = schema_one_snapshot()

    from custom_components.span_panel.helpers import detect_capabilities

    assert SpanPanelCoordinator._detect_capabilities(snapshot) == detect_capabilities(
        snapshot
    )
    # The capabilities the old copy was blind to are present in this capture,
    # so the assertion above is not comparing two empty sets.
    assert {"shed_forecast", "pcs", "bess_telemetry", "der_link_health"} <= (
        detect_capabilities(snapshot)
    )


# --- a device or a property that arrives after setup -------------------------


def _adopted_device(device_id: str = "generator-1") -> AdoptedDevice:
    return AdoptedDevice(
        device_id=device_id,
        device_type="energy.ebus.device.generator",
        name="Backup Generator",
        properties=(
            AdoptedProperty(
                node_id="meter", property_id="active-power", datatype="float", unit="W"
            ),
        ),
    )


def _extension_row(property_id: str = "cell-temperature") -> ExtensionProperty:
    return ExtensionProperty(
        subject=ExtensionSubject(kind="battery", instance_key=None),
        node_id="battery-2",
        property_id=property_id,
        datatype="float",
        unit="°C",
    )


async def test_an_adopted_device_arriving_after_setup_asks_for_a_reload(
    hass: HomeAssistant,
) -> None:
    """Adoption's module docstring calls a device nobody modelled an *expected* event.

    Nothing adds these entities dynamically, so the only way one reaches the user
    without a manual restart is the capability reload -- and the capability set
    used to be blind to them, so a vendor device that appeared an hour after
    setup produced no device, no entity and no reload until somebody restarted
    Home Assistant.
    """
    coordinator = _create_coordinator(hass)
    baseline = SpanPanelSnapshotFactory.create()
    arrived = replace(baseline, adopted_devices=(_adopted_device(),))

    assert detect_capabilities(arrived) != detect_capabilities(baseline)

    coordinator._check_capability_change(baseline)
    coordinator._check_capability_change(arrived)

    assert coordinator._reload_requested is True


async def test_an_extension_property_arriving_after_setup_asks_for_a_reload(
    hass: HomeAssistant,
) -> None:
    """The other half of vendor extensibility, and the same silence."""
    coordinator = _create_coordinator(hass)
    baseline = SpanPanelSnapshotFactory.create()
    arrived = replace(baseline, extension_properties=(_extension_row(),))

    assert detect_capabilities(arrived) != detect_capabilities(baseline)

    coordinator._check_capability_change(baseline)
    coordinator._check_capability_change(arrived)

    assert coordinator._reload_requested is True


async def test_a_device_that_leaves_the_tree_asks_for_nothing(hass: HomeAssistant) -> None:
    """Expansion-only, exactly like every other capability flag.

    A device that stops publishing costs nothing to leave alone: its entities
    read unknown and nothing here removes a registry row, so a reload would
    rebuild the identical set. Reloading on a shrink would also make a flapping
    publisher reload the integration on a loop.
    """
    coordinator = _create_coordinator(hass)
    present = replace(
        SpanPanelSnapshotFactory.create(),
        adopted_devices=(_adopted_device(), _adopted_device("generator-2")),
    )
    gone = replace(present, adopted_devices=(_adopted_device(),))

    coordinator._check_capability_change(present)
    coordinator._check_capability_change(gone)

    assert coordinator._reload_requested is False


async def test_the_same_devices_and_properties_ask_for_nothing(hass: HomeAssistant) -> None:
    """The fingerprint has to be stable, or every refresh would reload the integration."""
    coordinator = _create_coordinator(hass)
    snapshot = replace(
        SpanPanelSnapshotFactory.create(),
        adopted_devices=(_adopted_device(),),
        extension_properties=(_extension_row(), _extension_row("pack-voltage")),
    )

    coordinator._check_capability_change(snapshot)
    for _ in range(5):
        coordinator._check_capability_change(
            replace(
                SpanPanelSnapshotFactory.create(),
                adopted_devices=(_adopted_device(),),
                extension_properties=(_extension_row("pack-voltage"), _extension_row()),
            )
        )

    assert coordinator._reload_requested is False


async def test_no_wire_identity_reaches_the_capability_set_in_the_clear() -> None:
    """The capability set is logged at INFO whenever it expands, so it may not carry a serial.

    `diagnostics.AdoptedDeviceRow` states the repo rule: a device id can embed a
    serial, because producers derive a DER's id preferring a serial over a
    default slug -- which is why this repository holds PV's `info/serial-number`
    unvalued and why that block reports `proxied` rather than `parent`. A
    capability token naming the id verbatim would put the same value in a log
    line users paste into issues.

    The subject kind and the wire path stay in the clear on purpose: they are
    vendor vocabulary and name no install.
    """
    snapshot = replace(
        SpanPanelSnapshotFactory.create(),
        adopted_devices=(_adopted_device("panel-EX-0000-0001"),),
        extension_properties=(
            replace(
                _extension_row(),
                subject=ExtensionSubject(kind="evse", instance_key="evse-EX-0000-0002"),
            ),
        ),
    )

    tokens = adopted_capability_tokens(snapshot)

    assert len(tokens) == 2
    rendered = " ".join(sorted(tokens))
    assert "EX-0000-0001" not in rendered
    assert "EX-0000-0002" not in rendered
    assert "panel-" not in rendered
    # What is kept is what a maintainer can act on without an identity.
    assert "extension:evse:" in rendered
    assert "battery-2/cell-temperature" in rendered


async def test_the_digest_is_stable_across_calls_and_distinguishes_devices() -> None:
    """A digest that moved between snapshots would reload the integration on every refresh."""
    first = replace(SpanPanelSnapshotFactory.create(), adopted_devices=(_adopted_device(),))
    again = replace(SpanPanelSnapshotFactory.create(), adopted_devices=(_adopted_device(),))
    other = replace(
        SpanPanelSnapshotFactory.create(), adopted_devices=(_adopted_device("generator-2"),)
    )

    assert adopted_capability_tokens(first) == adopted_capability_tokens(again)
    assert adopted_capability_tokens(first) != adopted_capability_tokens(other)
async def test_a_circuit_that_becomes_controllable_requests_a_reload(
    hass: HomeAssistant,
) -> None:
    """The direction no entity can see: a circuit that gains a control.

    A circuit the panel declares non-commandable gets no switch and no select,
    so there is no entity on that circuit to notice the panel changing its mind
    later. Only a reader that sees every circuit can, and the coordinator is
    the one thing that reads the whole snapshot on every push.
    """
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = None
    coordinator = _create_coordinator(hass, client=client)

    locked = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen Outlets", is_user_controllable=False
    )
    baseline = SpanPanelSnapshotFactory.create(circuits={"1": locked})
    commissioned = SpanPanelSnapshotFactory.create(
        circuits={"1": replace(locked, is_user_controllable=True)}
    )

    with patch.object(coordinator, "request_reload") as request_reload:
        await coordinator._on_snapshot_push(baseline)
        request_reload.assert_not_called()

        await coordinator._on_snapshot_push(commissioned)
        request_reload.assert_called_once()


async def test_a_circuit_that_stops_being_controllable_requests_a_reload(
    hass: HomeAssistant,
) -> None:
    """A control entity can outlive its own controllability, and must not.

    `is_user_controllable` decided at setup that this circuit gets a switch and
    a select. When the panel withdraws it mid-session both stay on the
    dashboard and every press and every choice is refused, which is a worse
    experience than the entities going away on the reload the change earns.
    """
    coordinator = _create_coordinator(hass)
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen Outlets", is_user_controllable=True
    )

    coordinator._check_settability_change(SpanPanelSnapshotFactory.create(circuits={"1": circuit}))
    assert coordinator._known_settability == {"1": (True, True)}
    assert coordinator._reload_requested is False

    coordinator._check_settability_change(
        SpanPanelSnapshotFactory.create(
            circuits={"1": replace(circuit, is_user_controllable=False)}
        )
    )

    assert coordinator._known_settability == {"1": (False, False)}
    assert coordinator._reload_requested is True


async def test_a_circuit_commissioned_never_backup_requests_a_reload(
    hass: HomeAssistant,
) -> None:
    """Priority settability is its own flag, and it decides whether a select exists.

    A circuit the panel commissions never-backup keeps a relay it can still
    operate, so the switch is unaffected -- but its priority select now refuses
    every choice made in it.
    """
    coordinator = _create_coordinator(hass)
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen Outlets", is_user_controllable=True
    )

    coordinator._check_settability_change(SpanPanelSnapshotFactory.create(circuits={"1": circuit}))
    coordinator._check_settability_change(
        SpanPanelSnapshotFactory.create(circuits={"1": replace(circuit, is_never_backup=True)})
    )

    assert coordinator._known_settability == {"1": (True, False)}
    assert coordinator._reload_requested is True


async def test_a_circuit_whose_settability_is_unchanged_requests_no_reload(
    hass: HomeAssistant,
) -> None:
    """The watch has to be an edge, or every push would reload the entry."""
    coordinator = _create_coordinator(hass)
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen Outlets", is_user_controllable=True, relay_state="CLOSED"
    )

    coordinator._check_settability_change(SpanPanelSnapshotFactory.create(circuits={"1": circuit}))
    coordinator._check_settability_change(
        SpanPanelSnapshotFactory.create(circuits={"1": replace(circuit, relay_state="OPEN")})
    )

    assert coordinator._reload_requested is False


async def test_a_settability_change_asks_for_one_reload_and_not_a_storm(
    hass: HomeAssistant,
) -> None:
    """The baseline moves on the pass that asks, exactly like the capability check.

    Without that, every push after a withdrawn control would re-request a
    reload, and the entry would reload for as long as the panel held its new
    answer.
    """
    coordinator = _create_coordinator(hass)
    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen Outlets", is_user_controllable=True
    )
    withdrawn = SpanPanelSnapshotFactory.create(
        circuits={"1": replace(circuit, is_user_controllable=False)}
    )

    coordinator._check_settability_change(SpanPanelSnapshotFactory.create(circuits={"1": circuit}))

    with patch.object(coordinator, "request_reload") as request_reload:
        coordinator._check_settability_change(withdrawn)
        coordinator._check_settability_change(withdrawn)
        coordinator._check_settability_change(withdrawn)

    request_reload.assert_called_once()


async def test_a_circuit_dropping_out_of_a_snapshot_requests_no_reload(
    hass: HomeAssistant,
) -> None:
    """Absence is not an answer about settability, and must not be read as one.

    Circuits go missing from a snapshot and come back -- both control platforms
    guard for exactly that. Counting the gap as a change would turn a flapping
    circuit into a reload loop, so only circuits present in both readings are
    judged. The one that returns is judged again on its return.
    """
    coordinator = _create_coordinator(hass)
    kitchen = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen Outlets", is_user_controllable=True
    )
    garage = SpanCircuitSnapshotFactory.create(
        circuit_id="2", name="Garage Outlets", is_user_controllable=True
    )

    coordinator._check_settability_change(
        SpanPanelSnapshotFactory.create(circuits={"1": kitchen, "2": garage})
    )
    coordinator._check_settability_change(SpanPanelSnapshotFactory.create(circuits={"1": kitchen}))
    assert coordinator._reload_requested is False

    coordinator._check_settability_change(
        SpanPanelSnapshotFactory.create(circuits={"1": kitchen, "2": garage})
    )
    assert coordinator._reload_requested is False

    coordinator._check_settability_change(
        SpanPanelSnapshotFactory.create(
            circuits={"1": kitchen, "2": replace(garage, is_user_controllable=False)}
        )
    )
    assert coordinator._reload_requested is True


async def test_the_settability_reader_gates_on_what_the_platforms_gate_on(
    hass: HomeAssistant,
) -> None:
    """The coordinator must not derive its own, narrower notion of settable.

    A second copy of either predicate would drift, and the drift would be
    silent: the platforms and the reload trigger disagreeing about what the
    panel allows. Both read `helpers`, and this pins the pair by driving a case
    only the shared predicate answers -- a PV circuit with no physical breaker,
    which is controllable and still gets neither entity.
    """
    upstream_pv = SpanCircuitSnapshotFactory.create(
        circuit_id="9",
        name="Solar",
        device_type="pv",
        relative_position="UPSTREAM",
        is_user_controllable=True,
    )
    coordinator = _create_coordinator(hass)

    coordinator._check_settability_change(
        SpanPanelSnapshotFactory.create(circuits={"9": upstream_pv})
    )

    assert coordinator._known_settability == {"9": (False, False)}
    assert circuit_has_a_breaker_switch(upstream_pv) is False
    assert circuit_has_a_priority_select(upstream_pv) is False


async def test_the_polling_path_watches_settability_too(hass: HomeAssistant) -> None:
    """A panel with no live MQTT stream has only the fallback poll to notice on."""
    client = MagicMock(spec=SpanPanelClientProtocol)
    client.field_metadata = None
    coordinator = _create_coordinator(hass, client=client)

    circuit = SpanCircuitSnapshotFactory.create(
        circuit_id="1", name="Kitchen Outlets", is_user_controllable=False
    )
    client.get_snapshot = AsyncMock(
        return_value=SpanPanelSnapshotFactory.create(circuits={"1": circuit})
    )
    await coordinator._async_update_data()

    client.get_snapshot = AsyncMock(
        return_value=SpanPanelSnapshotFactory.create(
            circuits={"1": replace(circuit, is_user_controllable=True)}
        )
    )
    with patch.object(coordinator, "request_reload") as request_reload:
        await coordinator._async_update_data()

    request_reload.assert_called_once()
