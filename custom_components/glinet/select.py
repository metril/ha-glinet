"""Select platform for GL.iNet routers.

Currently exposes a single **VPN client selector**: pick which configured VPN
profile is active (or "Off"). This is the clean answer to "which VPN should I use"
when several tunnels are configured, and complements the per-tunnel switches in
``switch.py``. Both drive the confirmed ``vpn-client.set_tunnel {enabled, tunnel_id}``
call; status comes from ``vpn-client.get_status``.

(An operating-mode select was investigated but firmware 4.x exposes no RPC to change
the working mode, so mode is surfaced read-only as a diagnostic sensor instead.)
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
from .const import DOMAIN, SVC_NETMODE, SVC_VPN_CLIENT
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)

OFF_OPTION = "Off"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet selects (the VPN client selector, if VPN is configured)."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    configs = (coordinator.data or {}).get("configs", {})
    entities: list[SelectEntity] = []
    if "vpn_client" in configs:
        entities.append(GlinetVpnClientSelect(coordinator, entry))
    if "netmode" in configs:
        entities.append(GlinetOperatingModeSelect(coordinator, entry))
    async_add_entities(entities)


class GlinetVpnClientSelect(GlinetEntity, SelectEntity):
    """Select which configured VPN client profile is active (or Off)."""

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

    @property
    def options(self) -> list[str]:
        """Return Off plus one option per configured VPN profile."""
        return [OFF_OPTION, *parsers.vpn_client_option_map(self._vpn_config()).keys()]

    @property
    def current_option(self) -> str | None:
        """Return the active profile's label, or Off."""
        active_id = parsers.vpn_client_active_tunnel(self._vpn_config())
        if active_id is None:
            return OFF_OPTION
        for label, tunnel_id in parsers.vpn_client_option_map(self._vpn_config()).items():
            if tunnel_id == active_id:
                return label
        return OFF_OPTION

    async def async_select_option(self, option: str) -> None:
        """Activate the chosen profile (or turn the active one off)."""
        client = self.coordinator.client
        labels = parsers.vpn_client_option_map(self._vpn_config())
        try:
            if option == OFF_OPTION:
                # Disable whichever tunnel is currently enabled.
                for profile in parsers.vpn_client_profiles(self._vpn_config()):
                    if profile.get("enabled"):
                        await client.call(
                            SVC_VPN_CLIENT,
                            "set_tunnel",
                            {"enabled": False, "tunnel_id": profile.get("tunnel_id")},
                        )
            else:
                target = labels.get(option)
                if target is None:
                    raise HomeAssistantError(f"Unknown VPN profile: {option}")
                # Disable any other active tunnel first, then enable the target.
                for profile in parsers.vpn_client_profiles(self._vpn_config()):
                    tid = profile.get("tunnel_id")
                    if profile.get("enabled") and tid != target:
                        await client.call(
                            SVC_VPN_CLIENT,
                            "set_tunnel",
                            {"enabled": False, "tunnel_id": tid},
                        )
                await client.call(
                    SVC_VPN_CLIENT,
                    "set_tunnel",
                    {"enabled": True, "tunnel_id": target},
                )
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to select VPN '{option}': {err}") from err
        await self.coordinator.async_request_refresh()


class GlinetOperatingModeSelect(GlinetEntity, SelectEntity):
    """Switch the router's working mode via ``netmode.set_mode``.

    Offers the modes that need no upstream Wi-Fi target — **Router** and
    **Access Point** — and reflects the current mode (read via ``netmode.get_mode``)
    even if it's a repeater/WDS mode set elsewhere. Switching mode is disruptive: it
    can change the router's IP and briefly drop connectivity. (Repeater/relay/WDS
    modes join an upstream AP and are driven by the repeater scan/connect flow.)
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
        """Switch the working mode (Router / Access Point)."""
        target = next(
            (m for m, label in parsers.MODE_LABELS.items() if label == option), None
        )
        if target not in parsers.MODE_OPTIONS:
            raise HomeAssistantError(
                f"Mode '{option}' can't be set directly; use the repeater flow."
            )
        try:
            await self.coordinator.client.call(
                SVC_NETMODE, "set_mode", {"mode": target}
            )
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to set mode '{option}': {err}") from err
        await self.coordinator.async_request_refresh()
