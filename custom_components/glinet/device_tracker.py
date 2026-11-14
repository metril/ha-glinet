"""Device tracker platform for GL.iNet routers (per-client presence)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import parsers
from .const import CONF_ENABLE_DEVICE_TRACKER
from .coordinator import GlinetConfigEntry, GlinetDataUpdateCoordinator
from .entity import GlinetEntity


PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlinetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GL.iNet client device trackers, adding new clients as they appear."""
    if not entry.options.get(CONF_ENABLE_DEVICE_TRACKER, True):
        return

    coordinator = entry.runtime_data
    tracked: set[str] = set()

    @callback
    def _add_new_clients() -> None:
        clients = (coordinator.data or {}).get("clients", [])
        new_entities = []
        for client in clients:
            mac = parsers.client_mac(client)
            if not mac or mac in tracked:
                continue
            tracked.add(mac)
            new_entities.append(GlinetDeviceTracker(coordinator, entry, mac))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_clients()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_clients))


class GlinetDeviceTracker(GlinetEntity, ScannerEntity):
    """Track a single client connected to the router."""

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
        mac: str,
    ) -> None:
        """Initialize the tracker for a client MAC."""
        super().__init__(coordinator, entry)
        self._mac = mac
        self._attr_unique_id = f"{entry.entry_id}_client_{mac}"
        # Client trackers are their own thing — don't attach to the router device.
        self._attr_device_info = None

    def _client(self) -> dict[str, Any] | None:
        return self.coordinator.clients_by_mac.get(self._mac)

    @property
    def name(self) -> str | None:
        """Return the client's display name."""
        client = self._client()
        if client:
            return parsers.client_name(client)
        return self._mac

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        """Return whether the client is currently connected."""
        client = self._client()
        return bool(client and parsers.client_is_online(client))

    @property
    def ip_address(self) -> str | None:
        """Return the client's IP address."""
        client = self._client()
        return client.get("ip") if client else None

    @property
    def mac_address(self) -> str:
        """Return the client's MAC address."""
        return self._mac

    @property
    def has_entity_name(self) -> bool:
        """Trackers use the client name directly, not the device-prefixed name."""
        return False
