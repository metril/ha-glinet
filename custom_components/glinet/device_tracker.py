"""Device tracker platform for GL.iNet routers (per-client presence)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryChange
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GlinetConfigEntry, parsers
from .const import (
    SUBENTRY_TRACKED_CLIENT,
    TRACKER_MODE_ALL,
    TRACKER_MODE_OFF,
)
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity


PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlinetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GL.iNet client device trackers, adding new clients as they appear."""
    mode = parsers.device_tracker_mode(entry.options)
    coordinator = entry.runtime_data
    reg = er.async_get(hass)

    def _tracker_entries() -> list[er.RegistryEntry]:
        return [
            e
            for e in er.async_entries_for_config_entry(reg, entry.entry_id)
            if e.domain == "device_tracker"
        ]

    if mode == TRACKER_MODE_OFF:
        for reg_entry in _tracker_entries():
            reg.async_remove(reg_entry.entity_id)
        return

    tracked: set[str] = set()

    if mode == TRACKER_MODE_ALL:

        @callback
        def _add_new_clients() -> None:
            new_entities = []
            for client in (coordinator.data or {}).get("clients", []):
                mac = parsers.normalize_mac(parsers.client_mac(client))
                if not mac or mac in tracked:
                    continue
                tracked.add(mac)
                new_entities.append(GlinetDeviceTracker(coordinator, entry, mac))
            if new_entities:
                async_add_entities(new_entities)

        _add_new_clients()
        entry.async_on_unload(coordinator.async_add_listener(_add_new_clients))
        return

    # Selected: one tracker per "tracked_client" subentry.
    def _subentry_mac(sub: Any) -> str | None:
        return parsers.normalize_mac(sub.data.get("mac"))

    wanted = {
        m
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TRACKED_CLIENT and (m := _subentry_mac(sub))
    }
    for reg_entry in _tracker_entries():
        if parsers.normalize_mac(reg_entry.unique_id) not in wanted:
            reg.async_remove(reg_entry.entity_id)

    added: set[str] = set()

    @callback
    def _add_subentries() -> None:
        for sub in entry.subentries.values():
            if sub.subentry_type != SUBENTRY_TRACKED_CLIENT or sub.subentry_id in added:
                continue
            mac = _subentry_mac(sub)
            if not mac:
                continue
            added.add(sub.subentry_id)
            async_add_entities(
                [
                    GlinetDeviceTracker(
                        coordinator, entry, mac, fallback_name=sub.data.get("name")
                    )
                ],
                config_subentry_id=sub.subentry_id,
            )

    @callback
    def _entry_changed(change: ConfigEntryChange, changed: Any) -> None:
        if change is ConfigEntryChange.UPDATED and changed.entry_id == entry.entry_id:
            _add_subentries()

    _add_subentries()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _entry_changed)
    )


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
        fallback_name: str | None = None,
    ) -> None:
        """Initialize the tracker for a client MAC."""
        super().__init__(coordinator, entry)
        self._mac = mac
        self._fallback_name = fallback_name or None

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable trackers by default (ScannerEntity disables unknown devices)."""
        return True

    def _client(self) -> dict[str, Any] | None:
        for client in (self.coordinator.data or {}).get("clients", []):
            if parsers.normalize_mac(parsers.client_mac(client)) == self._mac:
                return client
        return None

    @property
    def name(self) -> str | None:
        """Return the client's display name."""
        client = self._client()
        if client:
            return parsers.client_name(client) or self._fallback_name or self._mac
        return self._fallback_name or self._mac

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
