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

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GlinetConfigEntry, parsers
from .api import GlinetError
from .const import (
    SVC_LED,
    SVC_OVPN_SERVER,
    SVC_REPEATER,
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
class GlinetSwitchDescription(SwitchEntityDescription):
    """Describes a GL.iNet switch.

    Subclasses ``SwitchEntityDescription`` (like the sensor/binary_sensor
    descriptions) so Home Assistant can read ``device_class``/``entity_category``
    off the assigned ``entity_description`` without crashing. ``key``/``name``/
    ``icon`` are inherited fields; the added fields below carry our wiring.
    """

    config_key: str = ""  # data["configs"] key gating creation and providing state
    service: str = ""
    kind: str = ""  # "vpn" | "led" | "tailscale" | "tor"
    is_on_fn: Callable[[dict[str, Any] | None], bool | None] = field(
        default=lambda cfg: None
    )


SWITCHES: tuple[GlinetSwitchDescription, ...] = (
    GlinetSwitchDescription(
        key="led",
        translation_key="led",
        config_key="led",
        service=SVC_LED,
        kind="led",
        icon="mdi:led-on",
        is_on_fn=parsers.led_enabled,
    ),
    GlinetSwitchDescription(
        key="wireguard_server",
        translation_key="wireguard_server",
        config_key="wg_server",
        service=SVC_WG_SERVER,
        kind="vpn",
        icon="mdi:server-network",
        is_on_fn=parsers.vpn_connected,
    ),
    GlinetSwitchDescription(
        key="openvpn_server",
        translation_key="openvpn_server",
        config_key="ovpn_server",
        service=SVC_OVPN_SERVER,
        kind="vpn",
        icon="mdi:server-network",
        is_on_fn=parsers.vpn_connected,
    ),
    GlinetSwitchDescription(
        key="tailscale",
        translation_key="tailscale",
        config_key="tailscale",
        service=SVC_TAILSCALE,
        kind="tailscale",
        icon="mdi:vpn",
        is_on_fn=parsers.vpn_connected,
    ),
    GlinetSwitchDescription(
        key="tor",
        translation_key="tor",
        config_key="tor",
        service=SVC_TOR,
        kind="tor",
        icon="mdi:tor",
        is_on_fn=parsers.tor_enabled,
    ),
)


PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlinetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GL.iNet switches for the features this router actually exposes."""
    coordinator = entry.runtime_data
    configs = (coordinator.data or {}).get("configs", {})
    entities: list[SwitchEntity] = [
        GlinetSwitch(coordinator, entry, desc)
        for desc in SWITCHES
        if desc.config_key in configs
    ]
    # One on/off VPN switch (the VPN-client select chooses which profile it acts on).
    if "vpn_client" in configs:
        entities.append(GlinetVpnSwitch(coordinator, entry))
    # One on/off Repeater switch (the Repeater-network select chooses which saved
    # network it connects to).
    if "repeater" in configs:
        entities.append(GlinetRepeaterSwitch(coordinator, entry))
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
        entry: GlinetConfigEntry,
        description: GlinetSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._desc = description
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
    _attr_translation_key = "vpn_client"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: GlinetConfigEntry,
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


class GlinetRepeaterSwitch(GlinetEntity, SwitchEntity):
    """Turn the Wi-Fi repeater uplink on/off; the Repeater-network select picks which.

    On → connect to the target saved network via ``repeater.connect
    {**saved_entry, remember:True}``; off → ``repeater.disconnect``. Live state
    (connected to an upstream) comes from ``repeater.get_status``. The target is
    ``coordinator.repeater_target`` (a saved-network label), set by the select.
    """

    _attr_icon = "mdi:wifi-arrow-up-down"
    _attr_translation_key = "repeater"

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: GlinetConfigEntry,
    ) -> None:
        """Initialize the repeater on/off switch."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_repeater"

    def _label_map(self) -> dict[str, dict[str, Any]]:
        cfg = (self.coordinator.data or {}).get("configs", {}).get("repeater_saved")
        return parsers.repeater_saved_option_map(cfg)

    def _repeater_status(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("configs", {}).get("repeater")

    def _target_entry(self) -> dict[str, Any] | None:
        """Resolve the saved network to connect: stored target, else connected, else first."""
        labels = self._label_map()
        target = self.coordinator.repeater_target
        if target in labels:
            return labels[target]
        # Fall back to the currently-connected network (match by SSID), if saved.
        if parsers.repeater_connected(self._repeater_status()):
            ssid = parsers.repeater_upstream_ssid(self._repeater_status())
            for entry in labels.values():
                if entry.get("ssid") == ssid:
                    return entry
        return next(iter(labels.values()), None)

    @property
    def is_on(self) -> bool | None:
        """Return whether the repeater is connected to an upstream network."""
        return parsers.repeater_connected(self._repeater_status())

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Connect the repeater to the target saved network."""
        entry = self._target_entry()
        if entry is None:
            raise HomeAssistantError("No saved repeater network to connect to")
        try:
            await self.coordinator.client.call(
                SVC_REPEATER, "connect", {**entry, "remember": True}
            )
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to connect repeater: {err}") from err
        self.coordinator.invalidate("repeater_saved")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disconnect the repeater uplink."""
        try:
            await self.coordinator.client.call(SVC_REPEATER, "disconnect")
        except GlinetError as err:
            raise HomeAssistantError(f"Failed to disconnect repeater: {err}") from err
        self.coordinator.invalidate("repeater_saved")
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
        entry: GlinetConfigEntry,
        iface: dict[str, Any],
    ) -> None:
        """Initialize the Wi-Fi switch for an iface."""
        super().__init__(coordinator, entry)
        self._iface_name = iface["iface_name"]
        band = _BAND_LABEL.get(str(iface.get("band")), str(iface.get("band") or ""))
        self._attr_translation_key = "guest_wifi" if iface.get("guest") else "wifi"
        self._attr_translation_placeholders = {"band": band}
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
        await self.coordinator.async_request_refresh()
