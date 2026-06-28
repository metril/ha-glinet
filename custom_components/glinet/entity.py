"""Base entity for the GL.iNet integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import GlinetDataUpdateCoordinator


def _first(info: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present, truthy value among ``keys`` in ``info``."""
    for key in keys:
        value = info.get(key)
        if value:
            return value
    return default


class GlinetEntity(CoordinatorEntity[GlinetDataUpdateCoordinator]):
    """Base entity carrying shared device info for a GL.iNet router."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._entry = entry
        info = coordinator.info
        mac = _first(info, "mac", "factory_mac", "lan_mac")
        board = info.get("board_info") or {}
        model = board.get("model") or _first(info, "model", "product", default="GL.iNet Router")

        connections = {(CONNECTION_NETWORK_MAC, mac)} if mac else set()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            connections=connections,
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=_first(info, "firmware_version", "version"),
            configuration_url=f"http://{entry.data['host']}",
        )

    @property
    def _status(self) -> dict[str, Any]:
        """Return the latest ``system.get_status`` payload."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("status", {})
