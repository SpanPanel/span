"""One config entry set up over replayed panels, as Home Assistant runs it, across their updates.

A `Panel` is an entry whose every setup connects to the tree the panel serves at
that moment, through `captures_replay.ReplayClient`: set it up on one tree, push
another to the running session, or restart it on a third, as a panel that took
a firmware update does. Everything else is the integration's real setup.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry
from span_panel_api import SpanPanelSnapshot

from custom_components.span_panel.const import (
    CONF_API_VERSION,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    DOMAIN,
    ENABLE_CURRENT_MONITORING,
)
from custom_components.span_panel.id_builder import (
    build_circuit_device_identifier,
    build_circuit_unique_id,
)
from custom_components.span_panel.migrations import (
    CURRENT_CONFIG_MINOR_VERSION,
    CURRENT_CONFIG_VERSION,
)
from custom_components.span_panel.runtime import SpanPanelRuntimeData, loaded_runtime_data

from .captures_replay import ReplayClient, RetainedTree, replay, snapshot
from .test_expected_entities import FRESH_INSTALL_OPTIONS

PANEL_NAME: Final = "Span Panel"

MONITORED_OPTIONS: Final[Mapping[str, object]] = {
    **FRESH_INSTALL_OPTIONS,
    ENABLE_CURRENT_MONITORING: True,
}
"""A fresh install's options, with current monitoring switched on."""


# ---------------------------------------------------------------------------
# One panel, over the trees a test sets it up on
# ---------------------------------------------------------------------------


@dataclass
class Panel:
    """One config entry, set up over whichever tree the panel is serving."""

    hass: HomeAssistant
    entry: MockConfigEntry
    tree: RetainedTree
    clients: list[ReplayClient] = field(default_factory=list)

    def client(self) -> ReplayClient:
        """Build the client a setup connects with, over the tree served now."""
        client = ReplayClient(replay(self.tree))
        self.clients.append(client)
        return client

    @property
    def runtime(self) -> SpanPanelRuntimeData:
        """The loaded entry's runtime data."""
        runtime = loaded_runtime_data(self.entry)
        assert runtime is not None, "the entry is not loaded"
        return runtime

    @property
    def snapshot(self) -> SpanPanelSnapshot:
        """What the coordinator holds."""
        return self.runtime.coordinator.data

    @property
    def serial(self) -> str:
        """The panel's serial, which every unique id and identifier is built from."""
        return str(self.entry.unique_id)

    async def set_up(self, tree: RetainedTree) -> None:
        """Set the entry up over `tree`, then stream it once, as a live panel's first push."""
        self.tree = tree
        assert await self.hass.config_entries.async_setup(self.entry.entry_id) is True
        await self.hass.async_block_till_done()
        assert self.entry.state is ConfigEntryState.LOADED
        await self.push(tree)

    async def push(self, tree: RetainedTree) -> None:
        """Stream `tree` to the running session, as the panel publishing it does."""
        await self.clients[-1].push(replay(tree))
        await self.hass.async_block_till_done()

    async def unload(self) -> None:
        """Unload the entry, as Home Assistant stopping does."""
        assert await self.hass.config_entries.async_unload(self.entry.entry_id) is True
        await self.hass.async_block_till_done()

    async def restart_on(self, tree: RetainedTree) -> None:
        """Unload and set up again over `tree`: Home Assistant restarted after the update."""
        await self.unload()
        await self.set_up(tree)

    async def call(self, service: str, **data: object) -> None:
        """Call one of the integration's services as an automation would, with no user."""
        await self.hass.services.async_call(DOMAIN, service, dict(data), blocking=True)
        await self.hass.async_block_till_done()

    def entity_id(self, domain: str, unique_id: str) -> str:
        """Return the entity id registered under a unique id, or fail naming it."""
        entity_id = er.async_get(self.hass).async_get_entity_id(domain, DOMAIN, unique_id)
        assert entity_id is not None, f"no {domain} entity {unique_id}"
        return entity_id

    def power(self, circuit_id: str) -> str:
        """Return the entity id of a circuit's power sensor."""
        return self.entity_id(
            "sensor", build_circuit_unique_id(self.serial, circuit_id, "instantPowerW")
        )

    def monitoring(self, circuit_id: str) -> Mapping[str, object]:
        """Return what monitoring reports for a circuit, read through its status as the card reads it."""
        monitor = self.runtime.coordinator.current_monitor
        assert monitor is not None, "monitoring is not running"
        status = monitor.get_monitoring_status()["circuits"]
        current = build_circuit_unique_id(self.serial, circuit_id, "current")
        entity_id = er.async_get(self.hass).async_get_entity_id("sensor", DOMAIN, current)
        entry: Mapping[str, object] = status[
            entity_id if entity_id is not None else self.power(circuit_id)
        ]
        return entry

    def circuit_device(self, circuit_id: str) -> dr.ChildDeviceEntry:
        """Return a circuit's own device, or fail naming the circuit."""
        device = dr.async_get(self.hass).async_get_child_device_by_identifier(
            (DOMAIN, build_circuit_device_identifier(self.serial, circuit_id)), self.entry.entry_id
        )
        assert device is not None, f"{circuit_id} has no device"
        return device


@contextmanager
def replayed_panels(hass: HomeAssistant) -> Iterator[Callable[..., Panel]]:
    """Make panels whose setups connect to the trees they serve, by serial."""
    made: dict[str, Panel] = {}

    def make(
        tree: RetainedTree,
        *,
        options: Mapping[str, object] = MONITORED_OPTIONS,
        entry_id: str = "entry-device-ids",
    ) -> Panel:
        serial = snapshot(tree).serial_number
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_API_VERSION: "v2",
                CONF_HOST: "192.0.2.10",
                "device_name": PANEL_NAME,
                CONF_EBUS_BROKER_HOST: f"{serial}.local",
                CONF_EBUS_BROKER_USERNAME: "replay-user",
                CONF_EBUS_BROKER_PASSWORD: "replay-password",
                CONF_EBUS_BROKER_PORT: 8883,
            },
            options=dict(options),
            title=PANEL_NAME,
            entry_id=entry_id,
            unique_id=serial,
            version=CURRENT_CONFIG_VERSION,
            minor_version=CURRENT_CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        made[serial] = Panel(hass, entry, tree)
        return made[serial]

    def client_for(_host: str, serial: str, *_args: object, **_kwargs: object) -> ReplayClient:
        return made[serial].client()

    with patch("custom_components.span_panel.SpanMqttClient", side_effect=client_for):
        yield make
