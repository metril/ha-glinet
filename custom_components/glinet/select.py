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
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import GlinetError
from .const import DOMAIN, SVC_NETMODE, SVC_REPEATER, SVC_VPN_CLIENT
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
    if "netmode" in configs:
        entities.append(GlinetOperatingModeSelect(coordinator, entry))
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


class GlinetOperatingModeSelect(GlinetEntity, SelectEntity):
    """Switch the working mode via ``netmode.set_mode`` — gated by the arm switch.

    Offers the modes that need no upstream Wi-Fi target — **Router** and
    **Access Point** — and reflects the current mode (``netmode.get_mode``), even if
    it's a repeater/WDS mode set elsewhere. Selecting is refused unless the
    "Mode Change Armed" switch is on; on success the arm auto-disarms. Switching mode
    is disruptive (can change the router's IP / drop connectivity).
    """

    _attr_icon = "mdi:router-wireless-settings"
    _attr_name = "Operating Mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the operating-mode selector."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_operating_mode"

    def _current_mode(self) -> str | None:
        netmode = (self.coordinator.data or {}).get("configs", {}).get("netmode")
        status = (self.coordinator.data or {}).get("status", {})
        return parsers.operating_mode(status, netmode)

    @property
    def options(self) -> list[str]:
        """Return the selectable mode labels (plus the current mode if exotic)."""
        labels = [parsers.MODE_LABELS[m] for m in parsers.MODE_OPTIONS]
        current = self._current_mode()
        if current and current not in parsers.MODE_OPTIONS:
            labels.append(parsers.MODE_LABELS.get(current, current))
        return labels

    @property
    def current_option(self) -> str | None:
        """Return the current mode's label."""
        current = self._current_mode()
        if not current:
            return None
        return parsers.MODE_LABELS.get(current, current)

    async def async_select_option(self, option: str) -> None:
        """Switch the working mode (Router / Access Point), if armed."""
        if not self.coordinator.mode_armed:
            raise HomeAssistantError(
                "Mode change is disarmed. Turn on 'Mode Change Armed' first "
                "(it auto-disarms shortly), then pick the mode."
            )
        target = next(
            (m for m, label in parsers.MODE_LABELS.items() if label == option), None
        )
        if target not in parsers.MODE_OPTIONS:
            raise HomeAssistantError(
                f"Mode '{option}' can't be set directly; use the repeater flow."
            )
        try:
            await self.coordinator.client.call(SVC_NETMODE, "set_mode", {"mode": target})
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to set mode '{option}': {err}") from err
        self.coordinator.disarm_mode()
        self.coordinator.invalidate("netmode")
        await self.coordinator.async_request_refresh()


class GlinetRepeaterNetworkSelect(GlinetEntity, SelectEntity):
    """(Re)connect the Wi-Fi repeater uplink by picking a saved network.

    Options are "Disconnected" plus each saved upstream SSID (the router stores the
    key, so reconnecting needs only the SSID). For a brand-new network, use the
    ``glinet.scan_repeater`` + ``glinet.connect_repeater`` services.
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

    def _saved(self) -> list[str]:
        cfg = (self.coordinator.data or {}).get("configs", {}).get("repeater_saved")
        return [n["ssid"] for n in parsers.repeater_saved_networks(cfg)]

    @property
    def options(self) -> list[str]:
        """Return Disconnected plus saved upstream SSIDs (and the current one)."""
        opts = [DISCONNECTED_OPTION, *self._saved()]
        current = self._current_ssid()
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
        """Return the connected upstream SSID, else Disconnected."""
        return self._current_ssid() or DISCONNECTED_OPTION

    async def async_select_option(self, option: str) -> None:
        """Connect to a saved network, or disconnect."""
        client = self.coordinator.client
        try:
            if option == DISCONNECTED_OPTION:
                await client.call(SVC_REPEATER, "disconnect")
            else:
                await client.call(
                    SVC_REPEATER, "connect", {"ssid": option, "remember": True}
                )
        except GlinetError as err:
            raise HomeAssistantError(
                f"Failed to set repeater network '{option}': {err}"
            ) from err
        self.coordinator.invalidate("repeater_saved")
        await self.coordinator.async_request_refresh()
