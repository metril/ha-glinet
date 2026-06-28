"""Update platform for GL.iNet routers (firmware-available notification)."""

from __future__ import annotations

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .const import DOMAIN
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity


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
    """Reports whether newer router firmware is available (notification only)."""

    _attr_name = "Firmware"
    _attr_supported_features = UpdateEntityFeature(0)

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the firmware update entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_firmware"

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed firmware version."""
        info = self.coordinator.info
        return info.get("firmware_version") or info.get("version")

    @property
    def latest_version(self) -> str | None:
        """Return the latest available firmware, if the router reports one."""
        status = self._status
        latest = parsers.first_path(
            status,
            "system.new_version",
            "new_version",
            "firmware.new_version",
            "upgrade.version",
        )
        # Fall back to the installed version (= "up to date") when none is reported.
        return str(latest) if latest else self.installed_version
