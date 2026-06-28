"""Switch platform for GL.iNet routers.

Covers the controllable surfaces: router LEDs, VPN clients/servers, and Tailscale.

NOTE: GL.iNet's ``set_config``/``start`` payload shapes are only partially
documented and vary by firmware. The write paths here use the documented method
names and merge the current config where possible; they should be verified on a
live router (see CLAUDE.md). State reads come from the coordinator's optional
config polls, and creation of each switch is gated on that config being present,
so a model lacking a feature simply won't show the switch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import GlinetError
from .const import (
    DOMAIN,
    SVC_LED,
    SVC_OVPN_SERVER,
    SVC_TAILSCALE,
    SVC_VPN_CLIENT,
    SVC_WG_SERVER,
)
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GlinetSwitchDescription:
    """Describes a GL.iNet switch."""

    key: str
    name: str
    config_key: str  # data["configs"] key gating creation and providing state
    service: str
    kind: str  # "vpn" | "led" | "tailscale"
    icon: str | None = None
    is_on_fn: Callable[[dict[str, Any] | None], bool | None] = field(
        default=lambda cfg: None
    )


SWITCHES: tuple[GlinetSwitchDescription, ...] = (
    GlinetSwitchDescription(
        key="led",
        name="LEDs",
        config_key="led",
        service=SVC_LED,
        kind="led",
        icon="mdi:led-on",
        is_on_fn=parsers.led_enabled,
    ),
    GlinetSwitchDescription(
        key="wireguard_server",
        name="WireGuard Server",
        config_key="wg_server",
        service=SVC_WG_SERVER,
        kind="vpn",
        icon="mdi:server-network",
        is_on_fn=parsers.vpn_connected,
    ),
    GlinetSwitchDescription(
        key="openvpn_server",
        name="OpenVPN Server",
        config_key="ovpn_server",
        service=SVC_OVPN_SERVER,
        kind="vpn",
        icon="mdi:server-network",
        is_on_fn=parsers.vpn_connected,
    ),
    GlinetSwitchDescription(
        key="tailscale",
        name="Tailscale",
        config_key="tailscale",
        service=SVC_TAILSCALE,
        kind="tailscale",
        icon="mdi:vpn",
        is_on_fn=parsers.vpn_connected,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet switches for the features this router actually exposes."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    configs = (coordinator.data or {}).get("configs", {})
    async_add_entities(
        GlinetSwitch(coordinator, entry, desc)
        for desc in SWITCHES
        if desc.config_key in configs
    )

    # Dynamic VPN client switches, one per configured tunnel (added as they appear).
    known_tunnels: set[Any] = set()

    @callback
    def _add_vpn_clients() -> None:
        vpn_cfg = (coordinator.data or {}).get("configs", {}).get("vpn_client")
        new = []
        for profile in parsers.vpn_client_profiles(vpn_cfg):
            tid = profile.get("tunnel_id")
            if tid in known_tunnels:
                continue
            known_tunnels.add(tid)
            new.append(GlinetVpnClientSwitch(coordinator, entry, tid, profile.get("name")))
        if new:
            async_add_entities(new)

    _add_vpn_clients()
    entry.async_on_unload(coordinator.async_add_listener(_add_vpn_clients))


class GlinetSwitch(GlinetEntity, SwitchEntity):
    """A GL.iNet switch."""

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
        description: GlinetSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self.entity_description = description  # type: ignore[assignment]
        self._desc = description
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    def _config(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("configs", {}).get(
            self._desc.config_key
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the switch is on."""
        return self._desc.is_on_fn(self._config())

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._set(False)

    async def _set(self, enable: bool) -> None:
        client = self.coordinator.client
        desc = self._desc
        try:
            if desc.kind == "led":
                config = dict(self._config() or {})
                config["led_enable"] = enable
                await client.call(desc.service, "set_config", config)
            elif desc.kind == "tailscale":
                await client.call(desc.service, "set_config", {"enabled": enable})
            else:  # vpn: start/stop, passing through peer/group ids when known
                if enable:
                    await client.call(desc.service, "start", self._start_params())
                else:
                    await client.call(desc.service, "stop")
        except GlinetError as err:
            raise HomeAssistantError(
                f"Failed to set {desc.name}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()

    def _start_params(self) -> dict[str, Any]:
        """Derive start parameters from the current VPN status, if present."""
        config = self._config() or {}
        params: dict[str, Any] = {}
        for key in ("group_id", "peer_id", "client_id"):
            value = config.get(key)
            if value is not None:
                params[key] = value
        return params


class GlinetVpnClientSwitch(GlinetEntity, SwitchEntity):
    """Enable/disable a single VPN client tunnel (WireGuard/OpenVPN/etc.).

    Toggling calls ``vpn-client.set_tunnel {enabled, tunnel_id}`` — the exact call
    the GL.iNet UI uses. State comes from ``vpn-client.get_status``.
    """

    _attr_icon = "mdi:vpn"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
        tunnel_id: Any,
        name: str | None,
    ) -> None:
        """Initialize the VPN client switch for a tunnel."""
        super().__init__(coordinator, entry)
        self._tunnel_id = tunnel_id
        self._attr_name = f"VPN {name}" if name else f"VPN Client {tunnel_id}"
        self._attr_unique_id = f"{entry.entry_id}_vpn_client_{tunnel_id}"

    def _vpn_config(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("configs", {}).get("vpn_client")

    @property
    def is_on(self) -> bool | None:
        """Return whether this tunnel is enabled."""
        return parsers.vpn_client_tunnel_enabled(self._vpn_config(), self._tunnel_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable (connect) this VPN client tunnel."""
        await self._set_tunnel(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable (disconnect) this VPN client tunnel."""
        await self._set_tunnel(False)

    async def _set_tunnel(self, enabled: bool) -> None:
        try:
            await self.coordinator.client.call(
                SVC_VPN_CLIENT,
                "set_tunnel",
                {"enabled": enabled, "tunnel_id": self._tunnel_id},
            )
        except GlinetError as err:
            raise HomeAssistantError(
                f"Failed to set VPN tunnel {self._tunnel_id}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
