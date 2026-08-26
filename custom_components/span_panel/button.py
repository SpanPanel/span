"""Button entities for the Span Panel."""

import logging
from typing import Final

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from span_panel_api import SpanMqttClient, SpanPanelSnapshot
from span_panel_api.exceptions import SpanPanelServerError

from . import SpanPanelConfigEntry
from .const import CONF_DEVICE_NAME, DOMAIN
from .control_gate import ControlMode
from .coordinator import SpanPanelCoordinator
from .entity import SpanPanelEntity
from .helpers import construct_panel_unique_id_for_entry, has_bess

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


GFE_OVERRIDE_DESCRIPTION: Final = ButtonEntityDescription(
    key="gfe_override",
    translation_key="gfe_override",
)


class SpanPanelGFEOverrideButton(SpanPanelEntity, ButtonEntity):
    """Button entity for overriding the panel's grid-forming entity.

    The SPAN panel's GFE (dominant-power-source) is normally managed by the
    battery system (BESS). When BESS communication is lost, the GFE value
    becomes stale. These buttons allow a user or automation to publish a
    temporary override via the eBus MQTT /set topic. The BESS automatically
    reclaims control when communication is restored.
    """

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

        self._attr_device_info = self._build_device_info(coordinator, snapshot)

        device_name = coordinator.config_entry.data.get(
            CONF_DEVICE_NAME, coordinator.config_entry.title
        )
        self._attr_unique_id = construct_panel_unique_id_for_entry(
            coordinator, snapshot, description.key, device_name
        )

    async def async_press(self) -> None:
        """Publish the GFE override to the panel.

        A refusal is raised at the caller rather than filed as a persistent
        notification, the same way the circuit controls report one: nothing was
        published, so there is nothing to correct later, and the person who
        pressed the button is the one who needs to hear about it.
        """
        client = self.coordinator.client
        if not hasattr(client, "set_dominant_power_source"):
            _LOGGER.warning("Client does not support GFE override")
            return

        try:
            await self._async_guarded_control(
                client.set_dominant_power_source(self._override_value)
            )
        except SpanPanelServerError as err:
            _LOGGER.warning(
                "SPAN panel did not accept a GFE override to %s: %s", self._override_value, err
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="gfe_override_failed",
                translation_placeholders={
                    "value": self._override_value,
                    "reason": str(err),
                },
            ) from err
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """Return entity availability.

        The override is only relevant when BESS communication is lost and the
        panel is not already reporting grid-connected. When BESS is online or the
        panel already reads on-grid, firmware is managing correctly and the button
        should not be pressable.

        The second check reads `dsm_state`, not the grid-forming entity. It asks
        "are we already on the grid", which is what `dsm_state` answers directly and
        the GFE answers only by implication -- and, unlike the GFE, it is populated on
        both wire schemas, so this stays free of any knowledge of which one is
        underneath. It used to compare `dominant_power_source` to `"GRID"`, which is
        `None` under v1.0 and so never fired there at all.

        One useful consequence: `dsm_state` folds in the user's own assertion, so once
        a press takes effect the button disables itself rather than inviting a second.
        """
        if getattr(self.coordinator, "panel_offline", False):
            return False
        if not super().available:
            return False
        snapshot: SpanPanelSnapshot = self.coordinator.data
        bess_connected = snapshot.battery.connected if snapshot.battery else None
        if bess_connected is True:
            return False
        if snapshot.dsm_state == "DSM_ON_GRID":
            return False
        return True


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SpanPanelConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up button entities for Span Panel."""
    # Under `disabled` no control entity is created and no registry entry is
    # removed. See `switch.async_setup_entry` for why the registry entries stay.
    if config_entry.runtime_data.control_policy.mode is ControlMode.DISABLED:
        return

    coordinator = config_entry.runtime_data.coordinator

    entities: list[SpanPanelGFEOverrideButton] = []

    snapshot: SpanPanelSnapshot = coordinator.data
    if isinstance(coordinator.client, SpanMqttClient) and has_bess(snapshot):
        entities.append(SpanPanelGFEOverrideButton(coordinator, GFE_OVERRIDE_DESCRIPTION, "GRID"))

    async_add_entities(entities)
