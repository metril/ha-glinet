"""Button platform for GL.iNet routers."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import GlinetError
from .const import DOMAIN, SVC_REPEATER, SVC_SYSTEM
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)

# repeater.scan walks every band; the router UI allows up to ~300s. 60s is plenty
# in practice and keeps the button responsive.
SCAN_TIMEOUT = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the GL.iNet buttons."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    buttons: list[ButtonEntity] = [GlinetRebootButton(coordinator, entry)]
    if "repeater" in (coordinator.data or {}).get("configs", {}):
        buttons.append(GlinetRepeaterDisconnectButton(coordinator, entry))
        buttons.append(GlinetRepeaterScanButton(coordinator, entry))
    async_add_entities(buttons)


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


class GlinetRepeaterDisconnectButton(GlinetEntity, ButtonEntity):
    """Disconnect the router's Wi-Fi repeater uplink (``repeater.disconnect``)."""

    _attr_name = "Disconnect Repeater"
    _attr_icon = "mdi:wifi-off"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the repeater-disconnect button."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_repeater_disconnect"

    async def async_press(self) -> None:
        """Disconnect the upstream repeater connection."""
        try:
            await self.coordinator.client.call(SVC_REPEATER, "disconnect")
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to disconnect repeater: {err}") from err
        self.coordinator.invalidate("repeater_saved")
        await self.coordinator.async_request_refresh()


class GlinetRepeaterScanButton(GlinetEntity, ButtonEntity):
    """Scan for nearby Wi-Fi networks and publish them to the Repeater Scan sensor.

    Scanning is on-demand (it's slow), so it isn't part of the poll loop. The result
    is stored on the coordinator and read by ``sensor.glinet_repeater_scan``.
    """

    _attr_name = "Scan Repeater Networks"
    _attr_icon = "mdi:wifi-sync"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the repeater-scan button."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_repeater_scan"

    async def async_press(self) -> None:
        """Run a repeater scan and stash the result on the coordinator."""
        try:
            # Scanning all bands can take tens of seconds — use a generous timeout.
            result = await self.coordinator.client.call(
                SVC_REPEATER, "scan", timeout=SCAN_TIMEOUT
            )
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to scan repeater networks: {err}") from err
        self.coordinator.repeater_scan = result if isinstance(result, dict) else None
        # Push the new scan to the sensor without a full router poll.
        self.coordinator.async_update_listeners()
