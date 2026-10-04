"""Device tracker platform for GL.iNet routers (per-client presence)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GlinetConfigEntry, parsers
from .const import CONF_ENABLE_DEVICE_TRACKER
from .coordinator import GlinetDataUpdateCoordinator
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
    """Track a single client connected to the router.

    The unique id is the client MAC (``ScannerEntity``'s contract), so the same client
    seen by two GL.iNet entries yields a single tracker.
    """

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: GlinetConfigEntry,
        mac: str,
    ) -> None:
        """Initialize the tracker for a client MAC."""
        super().__init__(coordinator, entry)
        self._mac = mac

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable trackers by default (ScannerEntity disables unknown devices)."""
        return True

    def _client(self) -> dict[str, Any] | None:
        for client in (self.coordinator.data or {}).get("clients", []):
            if parsers.client_mac(client) == self._mac:
                return client
        return None

    @property
    def name(self) -> str | None:
        """Return the client's display name."""
        client = self._client()
        if client:
            return parsers.client_name(client)
        return self._mac

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
    def hostname(self) -> str | None:
        """Return the client's hostname."""
        client = self._client()
        if not client:
            return None
        return parsers.client_hostname(client)
