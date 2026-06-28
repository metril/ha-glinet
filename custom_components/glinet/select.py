"""Select platform for GL.iNet routers.

Three selectors (each created only if the router exposes the backing data):

- **VPN client** — choose *which* configured profile is the target. It does NOT turn
  the VPN on/off (that's the VPN switch); selecting while a VPN is already active
  switches over to the new profile immediately, otherwise it just records the target.
- **Operating mode** — Router / Access Point via ``netmode.set_mode``, **gated** by the
  "Mode Change Armed" switch so it can't be triggered by accident (switching mode is
  disruptive — it can change the router's IP).
- **Repeater network** — pick a *saved* upstream network to (re)connect as a repeater,
  or "Disconnected" to drop the uplink.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import GlinetError
from .const import DOMAIN, SVC_REPEATER, SVC_VPN_CLIENT
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)

DISCONNECTED_OPTION = "Disconnected"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet selects for whatever the router exposes."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    configs = (coordinator.data or {}).get("configs", {})
    entities: list[SelectEntity] = []
    if "vpn_client" in configs:
        entities.append(GlinetVpnClientSelect(coordinator, entry))
    if "repeater" in configs:
        entities.append(GlinetRepeaterNetworkSelect(coordinator, entry))
    async_add_entities(entities)


class GlinetVpnClientSelect(GlinetEntity, SelectEntity):
    """Choose which VPN client profile is the target (on/off is the VPN switch)."""

    _attr_icon = "mdi:vpn"
    _attr_name = "VPN Client"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the VPN client selector."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_vpn_client_select"

    def _vpn_config(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("configs", {}).get("vpn_client")

    def _target_tunnel(self) -> Any:
        """Resolve the effective target: stored target, else active, else first."""
        labels = parsers.vpn_client_option_map(self._vpn_config())
        valid_ids = set(labels.values())
        if self.coordinator.vpn_target in valid_ids:
            return self.coordinator.vpn_target
        active = parsers.vpn_client_active_tunnel(self._vpn_config())
        if active is not None:
            return active
        return next(iter(valid_ids), None)

    @property
    def options(self) -> list[str]:
        """Return one option per configured VPN profile."""
        return list(parsers.vpn_client_option_map(self._vpn_config()).keys())

    @property
    def current_option(self) -> str | None:
        """Return the active profile's label, else the targeted profile's label."""
        target = self._target_tunnel()
        for label, tunnel_id in parsers.vpn_client_option_map(self._vpn_config()).items():
            if tunnel_id == target:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the target profile; switch over immediately if a VPN is active."""
        labels = parsers.vpn_client_option_map(self._vpn_config())
        target = labels.get(option)
        if target is None:
            raise HomeAssistantError(f"Unknown VPN profile: {option}")
        self.coordinator.vpn_target = target
        active = parsers.vpn_client_active_tunnel(self._vpn_config())
        if active is not None and active != target:
            # A VPN is running — switch the active tunnel to the new target.
            client = self.coordinator.client
            try:
                await client.call(
                    SVC_VPN_CLIENT, "set_tunnel", {"enabled": False, "tunnel_id": active}
                )
                await client.call(
                    SVC_VPN_CLIENT, "set_tunnel", {"enabled": True, "tunnel_id": target}
                )
            except GlinetError as err:
                raise HomeAssistantError(
                    f"Failed to switch VPN to '{option}': {err}"
                ) from err
        self.coordinator.invalidate("vpn_client")
        await self.coordinator.async_request_refresh()


class GlinetRepeaterNetworkSelect(GlinetEntity, SelectEntity):
    """(Re)connect the Wi-Fi repeater uplink by picking a *saved* network.

    Options are "Disconnected" plus each saved upstream network (the router stores the
    key, so reconnecting needs only the saved config). Same-named saved networks are
    disambiguated by their stored config (see ``parsers.repeater_saved_option_map``).
    For a brand-new network, use the ``glinet.scan_repeater`` +
    ``glinet.connect_repeater`` services.
    """

    _attr_icon = "mdi:wifi-arrow-up-down"
    _attr_name = "Repeater Network"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the repeater-network selector."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_repeater_network"

    def _label_map(self) -> dict[str, dict[str, Any]]:
        cfg = (self.coordinator.data or {}).get("configs", {}).get("repeater_saved")
        return parsers.repeater_saved_option_map(cfg)

    @property
    def options(self) -> list[str]:
        """Return Disconnected plus each saved network's (disambiguated) label."""
        opts = [DISCONNECTED_OPTION, *self._label_map().keys()]
        current = self.current_option
        if current and current not in opts:
            opts.append(current)
        return opts

    def _current_ssid(self) -> str | None:
        repeater = (self.coordinator.data or {}).get("configs", {}).get("repeater")
        if parsers.repeater_connected(repeater):
            return parsers.repeater_upstream_ssid(repeater)
        return None

    @property
    def current_option(self) -> str | None:
        """Return the connected network's label (matched by SSID), else Disconnected."""
        ssid = self._current_ssid()
        if not ssid:
            return DISCONNECTED_OPTION
        for label, entry in self._label_map().items():
            if entry.get("ssid") == ssid:
                return label
        return ssid  # connected to something not in the saved list

    async def async_select_option(self, option: str) -> None:
        """Connect to the chosen saved network (full config), or disconnect."""
        client = self.coordinator.client
        try:
            if option == DISCONNECTED_OPTION:
                await client.call(SVC_REPEATER, "disconnect")
            else:
                entry = self._label_map().get(option)
                if entry is None:
                    raise HomeAssistantError(f"Unknown saved network: {option}")
                # Pass the full saved config (as the UI does) so the right duplicate
                # is used, plus remember=true.
                await client.call(
                    SVC_REPEATER, "connect", {**entry, "remember": True}
                )
        except GlinetError as err:
            raise HomeAssistantError(
                f"Failed to set repeater network '{option}': {err}"
            ) from err
        self.coordinator.invalidate("repeater_saved")
        await self.coordinator.async_request_refresh()
