"""A v2 entry set up for real, with only the switch platform forwarded.

For tests whose subject is what the entry's own setup builds: the control policy
it resolves from the options, the gate it installs on the transport, the runtime
data it hands the platforms and the WebSocket commands, the switch platform it
forwards, and the reload an options change asks for. Those are all the real
ones. The transport and the coordinator are stand-ins, because none of that
reads the panel.

A command a relay switch sends goes through the interceptor setup installed, as
the library's publish does, so a test can operate a switch through Home
Assistant and see the real gate decide.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry
from span_panel_api import (
    ControlCommand,
    ControlInterceptor,
    PublishOutcome,
    PublishState,
    SpanCircuitSnapshot,
    SpanPanelSnapshot,
)

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HTTP_PORT,
    DOMAIN,
)
from custom_components.span_panel.control_gate import ControlMode
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.options import CONTROL_MODE
from custom_components.span_panel.package_ratings import MonitoringRating

from .factories import SpanPanelSnapshotFactory
from .helpers import ensure_device_registered_stand_in


def v2_entry(*, serial: str, entry_id: str, options: Mapping[str, object]) -> MockConfigEntry:
    """Build a v2 entry for the panel `serial`, at the current config version, with `options`."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_VERSION: "v2",
            CONF_HOST: "192.168.1.50",
            CONF_EBUS_BROKER_HOST: "span-panel.local",
            CONF_EBUS_BROKER_USERNAME: "mqtt-user",
            CONF_EBUS_BROKER_PASSWORD: "hunter2-synthetic",
            CONF_EBUS_BROKER_PORT: 8883,
            CONF_HTTP_PORT: 80,
        },
        options=dict(options),
        entry_id=entry_id,
        title="SPAN Panel",
        unique_id=serial,
        version=CURRENT_CONFIG_VERSION,
        minor_version=CURRENT_CONFIG_MINOR_VERSION,
    )


class GatedControls:
    """The coordinator's client, as far as a relay switch uses it.

    Each command is put to the interceptor setup installed on the transport and
    recorded in `published` once the interceptor lets it through, which is the
    part of the library's publish a gate test needs. The library also reports
    every outcome back to the interceptor; nothing here does, so no audit event
    fires and no relay debounce window opens.
    """

    def __init__(self, serial: str) -> None:
        """Start with no interceptor, as the transport does before setup installs one."""
        self._serial = serial
        self.interceptor: ControlInterceptor | None = None
        self.published: list[ControlCommand] = []

    def install(self, interceptor: ControlInterceptor | None) -> None:
        """Replace the interceptor, as `set_control_interceptor` on the transport does."""
        self.interceptor = interceptor

    async def set_circuit_relay(self, circuit_id: str, state: str) -> PublishOutcome:
        """Put a relay command to the interceptor, and publish it if it is let through."""
        return await self._publish(
            ControlCommand(
                device_id=self._serial,
                node_id=circuit_id,
                property_id="relay",
                value=state,
                topic=f"ebus/5/{self._serial}/{circuit_id}/switch/relay/set",
            )
        )

    async def _publish(self, command: ControlCommand) -> PublishOutcome:
        assert self.interceptor is not None, "setup installed no interceptor"
        await self.interceptor.before_publish(command)
        self.published.append(command)
        return PublishOutcome(
            state=PublishState.CONFIRMED, topic=command.topic, value=command.value
        )


def _no_copied_rating(circuit_id: str, snapshot: SpanPanelSnapshot) -> MonitoringRating:
    """Answer as a package-rating manager holding no copy does: with the panel's own rating."""
    circuit = snapshot.circuits.get(circuit_id)
    rating = circuit.breaker_rating_a if circuit is not None else None
    return MonitoringRating(rating, rating, False, None, None)


@contextlib.asynccontextmanager
async def switch_platform_setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    circuits: Mapping[str, SpanCircuitSnapshot] | None = None,
) -> AsyncIterator[MockConfigEntry]:
    """Add `entry`, set it up for real with only the switch platform forwarded, and unload it after.

    The panel publishes `circuits`, none by default. The stand-in coordinator's
    client is a `GatedControls` (see `gated_controls`), and its package-rating
    manager answers as one holding no copied rating, so the topology can be
    asked of the entry. The WebSocket commands are not registered by setup; a
    test that sends one registers them itself.
    """
    entry.add_to_hass(hass)
    serial = str(entry.unique_id)

    snapshot = SpanPanelSnapshotFactory.create(serial_number=serial, circuits=dict(circuits or {}))
    controls = GatedControls(serial)
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.set_control_interceptor = MagicMock(side_effect=controls.install)
    coordinator = MagicMock()
    # As the real coordinator is: the panel's device info is built from its
    # entry, and a circuit entity's id from the registries its `hass` holds.
    coordinator.hass = hass
    coordinator.config_entry = entry
    coordinator.data = snapshot
    coordinator.client = controls
    # A connected panel, so its relay switches are available: Core skips an
    # unavailable entity without an error, and the command would never be sent.
    coordinator.panel_offline = False
    coordinator.transport_dead = False
    coordinator.current_monitor = None
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.package_rating_manager.async_load = AsyncMock()
    coordinator.package_rating_manager.resolve = MagicMock(side_effect=_no_copied_rating)
    coordinator.async_setup_streaming = AsyncMock()
    coordinator.async_shutdown = AsyncMock()

    with (
        patch("custom_components.span_panel.async_register_commands"),
        patch("custom_components.span_panel.SpanMqttClient", return_value=client),
        patch("custom_components.span_panel.SpanPanelCoordinator", return_value=coordinator),
        patch(
            "custom_components.span_panel.ensure_device_registered",
            AsyncMock(side_effect=ensure_device_registered_stand_in),
        ),
        patch("custom_components.span_panel.PLATFORMS", [Platform.SWITCH]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        try:
            yield entry
        finally:
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def gated_controls(entry: MockConfigEntry) -> GatedControls:
    """Return the stand-in client the loaded `entry`'s relay switches publish through."""
    controls = entry.runtime_data.coordinator.client
    assert isinstance(controls, GatedControls)
    return controls


async def set_control_mode(hass: HomeAssistant, entry: MockConfigEntry, mode: ControlMode) -> None:
    """Change the control mode as an options save does, and let the reload it asks for run."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONTROL_MODE: mode.value}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.control_policy.mode is mode
