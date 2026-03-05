"""Button entities for the Span Panel."""

import logging
from typing import Any, Final

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from span_panel_api import SpanMqttClient, SpanPanelSnapshot
from span_panel_api.exceptions import SpanPanelServerError

from .const import (
    CONF_API_VERSION,
    CONF_DEVICE_NAME,
    COORDINATOR,
    DOMAIN,
)
from .coordinator import SpanPanelCoordinator
from .helpers import (
    async_create_span_notification,
    construct_panel_unique_id_for_entry,
)
from .sensors.factory import has_bess
from .util import snapshot_to_device_info

_LOGGER = logging.getLogger(__name__)


GFE_OVERRIDE_DESCRIPTION: Final = ButtonEntityDescription(
    key="gfe_override",
    name="GFE Override: Grid Connected",
    icon="mdi:transmission-tower",
    translation_key="gfe_override",
)


class SpanPanelGFEOverrideButton(CoordinatorEntity[SpanPanelCoordinator], ButtonEntity):
    """Button entity for overriding the panel's grid-forming entity.

    The SPAN panel's GFE (dominant-power-source) is normally managed by the
    battery system (BESS). When BESS communication is lost, the GFE value
    becomes stale. These buttons allow a user or automation to publish a
    temporary override via the eBus MQTT /set topic. The BESS automatically
    reclaims control when communication is restored.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SpanPanelCoordinator,
        description: ButtonEntityDescription,
        override_value: str,
    ) -> None:
        """Initialize the GFE override button."""
        super().__init__(coordinator)
        snapshot: SpanPanelSnapshot = coordinator.data

        self.entity_description = description
        self._override_value = override_value

        device_name = coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, coordinator.config_entry.title
        )

        is_simulator = coordinator.config_entry.data.get(CONF_API_VERSION) == "simulation"
        host = coordinator.config_entry.data.get(CONF_HOST)
        self._attr_device_info = snapshot_to_device_info(snapshot, device_name, is_simulator, host)

        self._attr_unique_id = construct_panel_unique_id_for_entry(
            coordinator, snapshot, description.key, device_name
        )

    async def async_press(self) -> None:
        """Publish the GFE override to the panel."""
        client = self.coordinator.client
        if not hasattr(client, "set_dominant_power_source"):
            _LOGGER.warning("GFE override not available in simulation mode")
            return

        try:
            await client.set_dominant_power_source(self._override_value)
            await self.coordinator.async_request_refresh()
        except SpanPanelServerError:
            warning_msg = (
                f"SPAN API returned a server error attempting "
                f"to override GFE to {self._override_value}."
            )
            _LOGGER.warning(warning_msg)
            await async_create_span_notification(
                self.hass,
                message=warning_msg,
                title="SPAN API Error",
                notification_id="span_panel_gfe_override_error",
            )

    @property
    def available(self) -> bool:
        """Return entity availability.

        The override is only relevant when BESS communication is lost and the
        panel is not already reporting grid-connected. When BESS is online or
        GFE is already GRID, firmware is managing correctly and the button
        should not be pressable.
        """
        if getattr(self.coordinator, "panel_offline", False):
            return False
        if not super().available:
            return False
        snapshot: SpanPanelSnapshot = self.coordinator.data
        bess_connected = snapshot.battery.connected if snapshot.battery else None
        gfe = snapshot.dominant_power_source
        if bess_connected is True:
            return False
        if gfe == "GRID":
            return False
        return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities for Span Panel."""
    data: dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SpanPanelCoordinator = data[COORDINATOR]

    entities: list[SpanPanelGFEOverrideButton] = []

    snapshot: SpanPanelSnapshot = coordinator.data
    if isinstance(coordinator.client, SpanMqttClient) and has_bess(snapshot):
        entities.append(SpanPanelGFEOverrideButton(coordinator, GFE_OVERRIDE_DESCRIPTION, "GRID"))

    async_add_entities(entities)
