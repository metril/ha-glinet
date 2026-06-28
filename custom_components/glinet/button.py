"""Button platform for GL.iNet routers."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GlinetError
from .const import DOMAIN, SVC_SYSTEM
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the GL.iNet reboot button."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities([GlinetRebootButton(coordinator, entry)])


class GlinetRebootButton(GlinetEntity, ButtonEntity):
    """Reboot the router."""

    _attr_name = "Reboot"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the reboot button."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_reboot"

    async def async_press(self) -> None:
        """Reboot the router."""
        try:
            await self.coordinator.client.call(SVC_SYSTEM, "reboot")
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to reboot router: {err}") from err
