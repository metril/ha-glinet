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
    SVC_TOR,
    SVC_VPN_CLIENT,
    SVC_WG_SERVER,
    SVC_WIFI,
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
    GlinetSwitchDescription(
        key="tor",
        name="Tor",
        config_key="tor",
        service=SVC_TOR,
        kind="tor",
        icon="mdi:tor",
        is_on_fn=parsers.tor_enabled,
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
    entities: list[SwitchEntity] = [
        GlinetSwitch(coordinator, entry, desc)
        for desc in SWITCHES
        if desc.config_key in configs
    ]
    # One on/off VPN switch (the VPN-client select chooses which profile it acts on).
    if "vpn_client" in configs:
        entities.append(GlinetVpnSwitch(coordinator, entry))
    async_add_entities(entities)

    # Dynamic Wi-Fi radio switches, one per iface reported in system.get_status.wifi.
    known_ifaces: set[str] = set()

    @callback
    def _add_wifi() -> None:
        status = (coordinator.data or {}).get("status", {})
        new = []
        for iface in parsers.wifi_status_ifaces(status):
            name = iface["iface_name"]
            if name in known_ifaces:
                continue
            known_ifaces.add(name)
            new.append(GlinetWifiSwitch(coordinator, entry, iface))
        if new:
            async_add_entities(new)

    _add_wifi()
    entry.async_on_unload(coordinator.async_add_listener(_add_wifi))


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
            elif desc.kind == "tor":
                # Mirror the UI's torForm: enable + the existing countries/manual.
                config = self._config() or {}
                await client.call(
                    desc.service,
                    "set_config",
                    {
                        "enable": enable,
                        "countries": config.get("countries", []),
                        "manual": bool(config.get("manual", False)),
                    },
                )
            else:  # vpn: start/stop, passing through peer/group ids when known
                if enable:
                    await client.call(desc.service, "start", self._start_params())
                else:
                    await client.call(desc.service, "stop")
        except GlinetError as err:
            raise HomeAssistantError(
                f"Failed to set {desc.name}: {err}"
            ) from err
        # LED/Tor live in the slow config tier — re-read them now, not next interval.
        self.coordinator.invalidate(desc.config_key)
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


class GlinetVpnSwitch(GlinetEntity, SwitchEntity):
    """Turn the VPN client on/off; the VPN-client select chooses which profile.

    On → enable the targeted tunnel (and disable any other active one); off →
    disable whichever tunnel is active. Both via ``vpn-client.set_tunnel
    {enabled, tunnel_id}``. State (any tunnel active) comes from
    ``vpn-client.get_status``.
    """

    _attr_icon = "mdi:vpn"
    _attr_name = "VPN Client"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the single VPN on/off switch."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_vpn_client"

    def _vpn_config(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("configs", {}).get("vpn_client")

    def _target_tunnel(self) -> Any:
        """Resolve the target tunnel: stored target, else active, else first."""
        labels = parsers.vpn_client_option_map(self._vpn_config())
        valid_ids = set(labels.values())
        if self.coordinator.vpn_target in valid_ids:
            return self.coordinator.vpn_target
        active = parsers.vpn_client_active_tunnel(self._vpn_config())
        if active is not None:
            return active
        return next(iter(valid_ids), None)

    @property
    def is_on(self) -> bool | None:
        """Return whether any VPN client tunnel is active."""
        return parsers.vpn_client_connected(self._vpn_config())

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the targeted VPN tunnel (disabling any other active one)."""
        target = self._target_tunnel()
        if target is None:
            raise HomeAssistantError("No VPN client profile configured")
        client = self.coordinator.client
        try:
            for profile in parsers.vpn_client_profiles(self._vpn_config()):
                tid = profile.get("tunnel_id")
                if profile.get("enabled") and tid != target:
                    await client.call(
                        SVC_VPN_CLIENT, "set_tunnel", {"enabled": False, "tunnel_id": tid}
                    )
            await client.call(
                SVC_VPN_CLIENT, "set_tunnel", {"enabled": True, "tunnel_id": target}
            )
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to enable VPN: {err}") from err
        self.coordinator.invalidate("vpn_client")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable whichever VPN tunnel is currently active."""
        client = self.coordinator.client
        try:
            for profile in parsers.vpn_client_profiles(self._vpn_config()):
                if profile.get("enabled"):
                    await client.call(
                        SVC_VPN_CLIENT,
                        "set_tunnel",
                        {"enabled": False, "tunnel_id": profile.get("tunnel_id")},
                    )
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to disable VPN: {err}") from err
        self.coordinator.invalidate("vpn_client")
        await self.coordinator.async_request_refresh()




_BAND_LABEL = {"2G": "2.4 GHz", "5G": "5 GHz", "6G": "6 GHz"}


class GlinetWifiSwitch(GlinetEntity, SwitchEntity):
    """Enable/disable a single Wi-Fi radio/SSID.

    Toggling calls ``wifi.set_config {iface_name, enabled}`` — the exact call the
    GL.iNet UI uses (the key is ``iface_name``, e.g. ``wifi2g``/``guest5g``). Live
    state comes from ``system.get_status.wifi[].up``.
    """

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
        iface: dict[str, Any],
    ) -> None:
        """Initialize the Wi-Fi switch for an iface."""
        super().__init__(coordinator, entry)
        self._iface_name = iface["iface_name"]
        band = _BAND_LABEL.get(str(iface.get("band")), str(iface.get("band") or ""))
        kind = "Guest Wi-Fi" if iface.get("guest") else "Wi-Fi"
        self._attr_name = f"{band} {kind}".strip()
        self._attr_icon = "mdi:wifi-lock" if iface.get("guest") else "mdi:wifi"
        self._attr_unique_id = f"{entry.entry_id}_wifi_{self._iface_name}"

    @property
    def is_on(self) -> bool | None:
        """Return whether this Wi-Fi iface is up."""
        status = (self.coordinator.data or {}).get("status", {})
        return parsers.wifi_iface_up(status, self._iface_name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable this Wi-Fi iface."""
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable this Wi-Fi iface."""
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        try:
            await self.coordinator.client.call(
                SVC_WIFI, "set_config", {"iface_name": self._iface_name, "enabled": enabled}
            )
        except GlinetError as err:
            raise HomeAssistantError(
                f"Failed to set Wi-Fi {self._iface_name}: {err}"
            ) from err
        self.coordinator.invalidate("wifi_config")
        await self.coordinator.async_request_refresh()
