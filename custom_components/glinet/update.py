"""Update platform for GL.iNet routers (firmware online upgrade)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .const import DOMAIN, SVC_UPGRADE
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)

# Online-upgrade RPC (captured from the GL.iNet UI's own /rpc): start the upgrade
# with ``upgrade.upgrade_online {keep_config, keep_package}``. The router downloads
# the image and reboots to flash it; progress is via ``get_online_upgrade_status``.
_INSTALL_METHOD = "upgrade_online"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the GL.iNet firmware update entity."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities([GlinetFirmwareUpdate(coordinator, entry)])


class GlinetFirmwareUpdate(GlinetEntity, UpdateEntity):
    """Reports available router firmware and performs the online upgrade."""

    _attr_name = "Firmware"
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the firmware update entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_firmware"

    def _firmware(self) -> dict | None:
        return (self.coordinator.data or {}).get("configs", {}).get("firmware")

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed firmware version."""
        info = self.coordinator.info
        return (
            parsers.firmware_current_version(self._firmware())
            or info.get("firmware_version")
            or info.get("version")
        )

    @property
    def latest_version(self) -> str | None:
        """Return the offered new firmware version, else the installed one.

        ``upgrade.check_firmware_online`` reports the offered version in
        ``version_new`` only when an update is available; otherwise we return the
        installed version so Home Assistant shows the entity as up to date.
        """
        return parsers.firmware_latest_version(self._firmware()) or self.installed_version

    @property
    def release_summary(self) -> str | None:
        """Note what installing does (it reboots the router)."""
        if parsers.firmware_update_available(self._firmware()):
            return (
                "Installing downloads the new firmware and reboots the router to "
                "flash it (settings are kept). The router is offline for a few "
                "minutes and this integration shows unavailable until it returns."
            )
        return None

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Start the online firmware upgrade (keeps settings, reboots the router)."""
        if not parsers.firmware_update_available(self._firmware()):
            raise HomeAssistantError("Firmware is already up to date")

        _LOGGER.warning(
            "Starting GL.iNet online firmware upgrade; the router will download the "
            "image and reboot to flash it — the integration will be unavailable for "
            "a few minutes"
        )
        try:
            # keep_package mirrors keep_config exactly, as the UI does. Generous
            # timeout: the call kicks off the download before returning.
            await self.coordinator.client.call(
                SVC_UPGRADE,
                _INSTALL_METHOD,
                {"keep_config": True, "keep_package": True},
                timeout=120,
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Failed to start firmware upgrade: {err}") from err
        # Do not poll progress: the router reboots to flash and drops the link.
