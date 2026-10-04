"""Button platform for GL.iNet routers."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GlinetConfigEntry
from .api import GlinetError
from .const import SVC_SYSTEM
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)


PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlinetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the GL.iNet buttons."""
    coordinator = entry.runtime_data
    buttons: list[ButtonEntity] = [GlinetRebootButton(coordinator, entry)]
    async_add_entities(buttons)


class GlinetRebootButton(GlinetEntity, ButtonEntity):
    """Reboot the router."""

    _attr_translation_key = "reboot"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: GlinetConfigEntry,
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
