"""Data coordinator for Gen3 Span panels.

Wraps the push-based gRPC streaming client in Home Assistant's standard
DataUpdateCoordinator pattern. This gives Gen3 the same
``coordinator.data`` interface that entities expect, while receiving
real-time updates from the gRPC stream rather than polling.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ..const import DOMAIN
from .const import DEFAULT_GRPC_PORT
from .span_grpc_client import PanelData, SpanGrpcClient

_LOGGER = logging.getLogger(__name__)

# Fallback poll interval — the gRPC stream pushes data, but
# DataUpdateCoordinator requires an interval.  Set to a long value
# since real updates come from the stream callback.
_FALLBACK_INTERVAL = timedelta(seconds=300)


class SpanGen3Coordinator(DataUpdateCoordinator[PanelData]):
    """Coordinator for Gen3 Span panels using gRPC streaming."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Span Gen3 ({entry.data.get('host', 'unknown')})",
            update_interval=_FALLBACK_INTERVAL,
        )
        self.config_entry = entry
        self._client = SpanGrpcClient(
            host=entry.data["host"],
            port=entry.data.get("port", DEFAULT_GRPC_PORT),
        )

    @property
    def client(self) -> SpanGrpcClient:
        """Return the gRPC client."""
        return self._client

    async def async_setup(self) -> bool:
        """Connect to the panel and start streaming."""
        if not await self._client.connect():
            return False

        # Wire up the gRPC stream callback to DataUpdateCoordinator
        self._client.register_callback(self._on_data_update)

        # Seed the coordinator with initial data
        self.async_set_updated_data(self._client.data)

        # Start the metric stream
        await self._client.start_streaming()
        return True

    async def async_shutdown(self) -> None:
        """Stop streaming and disconnect."""
        await self._client.stop_streaming()
        await self._client.disconnect()

    @callback
    def _on_data_update(self) -> None:
        """Handle data update from gRPC stream.

        Called by the gRPC client whenever new metrics arrive. Pushes
        the latest PanelData into the DataUpdateCoordinator, which
        triggers entity state writes.
        """
        self.async_set_updated_data(self._client.data)

    async def _async_update_data(self) -> PanelData:
        """Fallback for manual refresh — return cached data from stream."""
        return self._client.data
