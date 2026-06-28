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
        """Return the latest firmware via ``upgrade.check_firmware_online``.

        That check reports only whether a newer firmware is offered (``prompt``),
        not its version number, so when an update is available we surface a marker
        that differs from the installed version to raise the HA "update available"
        state; the upgrade itself is performed from the router UI.
        """
        if parsers.firmware_update_available(self._firmware()):
            return "newer available"
        return self.installed_version

    @property
    def release_summary(self) -> str | None:
        """Hint how to apply the update (no safe RPC to trigger it)."""
        if parsers.firmware_update_available(self._firmware()):
            return (
                "A newer firmware is available. Install it from the GL.iNet router "
                "UI (System → Upgrade)."
            )
        return None
