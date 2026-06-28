"""Field extraction from GL.iNet RPC payloads.

The exact key layout of ``system.get_status`` varies across models and firmware
revisions, and GL.iNet's API docs have been intermittently offline. Every value
the entities read is extracted here, each via a list of candidate paths with
fallbacks, so there is exactly ONE place to adjust if a field path differs on a
given router. Each helper returns ``None`` when nothing matches, so entities show
``unknown`` rather than crashing.
"""

from __future__ import annotations

from typing import Any


def _dig(data: Any, path: str) -> Any:
    """Follow a dotted path; list indices allowed as numbers (e.g. ``a.0.b``)."""
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def first_path(data: dict[str, Any], *paths: str) -> Any:
    """Return the first non-None value among the candidate dotted paths."""
    for path in paths:
        value = _dig(data, path)
        if value is not None:
            return value
    return None


# --- System sensors ---------------------------------------------------------

def uptime(status: dict[str, Any]) -> int | None:
    value = first_path(status, "system.uptime", "uptime")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def cpu_temperature(status: dict[str, Any]) -> float | None:
    value = first_path(
        status,
        "system.cpu.temperature",
        "system.cpu_temperature",
        "cpu.temperature",
        "cpu_temperature",
        "temperature",
    )
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_average(status: dict[str, Any]) -> float | None:
    value = first_path(
        status, "system.load_average.0", "load_average.0", "load_average", "load"
    )
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def memory_used_percent(status: dict[str, Any]) -> float | None:
    total = first_path(status, "system.memory_total", "memory_total", "memory.total")
    free = first_path(
        status,
        "system.memory_available",
        "memory_available",
        "system.memory_free",
        "memory_free",
        "memory.free",
        "memory.available",
    )
    # buff/cache is reclaimable, so treat it as available for a realistic "used".
    buff_cache = first_path(status, "system.memory_buff_cache", "memory_buff_cache") or 0
    try:
        total_f = float(total)
        free_f = float(free) + float(buff_cache)
        if total_f <= 0:
            return None
        return round((total_f - free_f) / total_f * 100, 1)
    except (TypeError, ValueError):
        return None


# --- WAN / connectivity -----------------------------------------------------
# ``system.get_status`` reports ``network`` as a list of interface dicts, each
# with ``online``/``up`` flags (e.g. wan, wwan, tethering, modem_*). There is no
# single connectivity flag, so we derive it from that list.

def _network_interfaces(status: dict[str, Any]) -> list[dict[str, Any]]:
    network = status.get("network")
    if isinstance(network, list):
        return [n for n in network if isinstance(n, dict)]
    if isinstance(network, dict):  # tolerate alternate firmware shape
        return [v for v in network.values() if isinstance(v, dict)]
    return []


def internet_online(status: dict[str, Any]) -> bool | None:
    ifaces = _network_interfaces(status)
    if not ifaces:
        return None
    return any(bool(n.get("online")) for n in ifaces)


def wan_connected(status: dict[str, Any]) -> bool | None:
    ifaces = _network_interfaces(status)
    if not ifaces:
        return None
    return any(bool(n.get("up")) and bool(n.get("online")) for n in ifaces)


def active_wan_interface(status: dict[str, Any]) -> str | None:
    """Return the name of the first online WAN interface."""
    for iface in _network_interfaces(status):
        if iface.get("online"):
            return iface.get("interface")
    return None


def wan_public_ip(data: dict[str, Any]) -> str | None:
    """Return a WAN IP from ddns.get_status (first non-empty interface IP)."""
    ddns = data.get("configs", {}).get("ddns") or {}
    ips = ddns.get("ips")
    if isinstance(ips, list):
        for entry in ips:
            ip_list = entry.get("ip") if isinstance(entry, dict) else None
            if isinstance(ip_list, list) and ip_list:
                return str(ip_list[0])
    return None


# --- Clients ----------------------------------------------------------------

def client_count(data: dict[str, Any]) -> int | None:
    clients = data.get("clients")
    if isinstance(clients, list):
        online = [c for c in clients if _client_online(c)]
        return len(online) if online else len(clients)
    status = data.get("status", {})
    value = first_path(status, "client.online", "client.total", "clients.total")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _client_online(client: dict[str, Any]) -> bool:
    value = client.get("online")
    if value is None:
        return True
    if isinstance(value, str):
        return value.lower() in ("1", "true", "online", "yes")
    return bool(value)


def client_mac(client: dict[str, Any]) -> str | None:
    mac = client.get("mac") or client.get("macaddr")
    return str(mac).lower() if mac else None


def client_name(client: dict[str, Any]) -> str | None:
    return (
        client.get("alias")
        or client.get("name")
        or client.get("hostname")
        or client.get("ip")
    )


def client_is_online(client: dict[str, Any]) -> bool:
    return _client_online(client)


# --- VPN / control surface --------------------------------------------------

# GL.iNet VPN ``get_status`` reports an integer ``status``: 0=stopped, 1=connecting,
# 2=connected (some firmware uses 1=connected). Treat >=1 with a connected flag as up.
def vpn_connected(config: dict[str, Any] | None) -> bool | None:
    """Return whether a VPN service (wg/ovpn/tailscale) is connected.

    Handles both top-level ``status`` (ovpn-server, tailscale) and the nested
    ``server.status`` shape used by wg-server.
    """
    if not config:
        return None
    status = first_path(config, "status", "server.status", "state", "connected", "running", "enable")
    if status is None:
        return None
    if isinstance(status, bool):
        return status
    if isinstance(status, str):
        return status.lower() in ("2", "connected", "running", "up", "online", "true", "yes")
    try:
        return int(status) >= 2 or (int(status) == 1 and "rx_bytes" in config)
    except (TypeError, ValueError):
        return bool(status)


def vpn_rx_bytes(config: dict[str, Any] | None) -> int | None:
    """Return VPN received bytes if reported."""
    if not config:
        return None
    value = first_path(config, "rx_bytes", "rx", "download")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def vpn_tx_bytes(config: dict[str, Any] | None) -> int | None:
    """Return VPN transmitted bytes if reported."""
    if not config:
        return None
    value = first_path(config, "tx_bytes", "tx", "upload")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def vpn_client_connected(config: dict[str, Any] | None) -> bool | None:
    """Whether a VPN client is active, from the unified ``vpn-client.get_status``.

    Shape: ``{mode, status_list:[{enabled, name, tunnel_id}]}``. ``mode`` is the
    active VPN mode (0 = none); a profile's ``enabled`` flags the active one.
    """
    if not config:
        return None
    mode = config.get("mode")
    if mode is not None:
        try:
            return int(mode) != 0
        except (TypeError, ValueError):
            pass
    status_list = config.get("status_list")
    if isinstance(status_list, list):
        return any(bool(e.get("enabled")) for e in status_list if isinstance(e, dict))
    return None


def vpn_client_active_name(config: dict[str, Any] | None) -> str | None:
    """Return the name of the active (enabled) VPN client profile, if any."""
    for entry in vpn_client_profiles(config):
        if entry.get("enabled"):
            return entry.get("name")
    return None


def vpn_client_profiles(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the configured VPN client profiles from ``vpn-client.get_status``.

    Each profile carries ``tunnel_id``, ``name``, ``enabled``, ``type`` (and
    ``group_id``/``peer_id``). Toggling a profile uses
    ``vpn-client.set_tunnel {enabled, tunnel_id}``.
    """
    if not config:
        return []
    status_list = config.get("status_list")
    if isinstance(status_list, list):
        return [e for e in status_list if isinstance(e, dict) and e.get("tunnel_id") is not None]
    return []


def vpn_client_tunnel_enabled(
    config: dict[str, Any] | None, tunnel_id: Any
) -> bool | None:
    """Return whether a specific VPN client tunnel is enabled."""
    for entry in vpn_client_profiles(config):
        if entry.get("tunnel_id") == tunnel_id:
            return bool(entry.get("enabled"))
    return None


def vpn_client_active_tunnel(config: dict[str, Any] | None) -> Any:
    """Return the tunnel_id of the active (enabled) VPN client profile, or None."""
    for entry in vpn_client_profiles(config):
        if entry.get("enabled"):
            return entry.get("tunnel_id")
    return None


def vpn_client_option_map(config: dict[str, Any] | None) -> dict[str, Any]:
    """Map a stable display label -> tunnel_id for each configured VPN profile.

    Profile names can collide, so any duplicate label is disambiguated with its
    tunnel id. Order follows the router's ``status_list``. This is the pure logic
    behind the VPN client selector; the entity layer adds the "Off" option.
    """
    seen: dict[str, int] = {}
    labels: dict[str, Any] = {}
    for profile in vpn_client_profiles(config):
        tunnel_id = profile.get("tunnel_id")
        name = profile.get("name") or f"Tunnel {tunnel_id}"
        seen[name] = seen.get(name, 0) + 1
        label = name if seen[name] == 1 else f"{name} ({tunnel_id})"
        labels[label] = tunnel_id
    return labels


def led_enabled(config: dict[str, Any] | None) -> bool | None:
    """Return whether the router LEDs are enabled."""
    if not config:
        return None
    value = first_path(config, "led_enable", "enable", "enabled")
    if value is None:
        return None
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes")
    return bool(value)


def wifi_band_up(status: dict[str, Any], band: str, guest: bool) -> bool | None:
    """Whether the SSID for a band ('2G'/'5G') and guest flag is up.

    Reads the ``wifi`` list from ``system.get_status``, whose entries carry
    ``band``, ``guest`` and ``up``.
    """
    wifi = status.get("wifi")
    if not isinstance(wifi, list):
        return None
    matched = None
    for entry in wifi:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("band", "")).upper() == band.upper() and bool(
            entry.get("guest")
        ) == guest:
            matched = entry
            break
    if matched is None:
        return None
    return bool(matched.get("up"))


def guest_wifi_up(status: dict[str, Any]) -> bool | None:
    """Whether any guest SSID is up."""
    wifi = status.get("wifi")
    if not isinstance(wifi, list):
        return None
    guest_entries = [e for e in wifi if isinstance(e, dict) and e.get("guest")]
    if not guest_entries:
        return None
    return any(bool(e.get("up")) for e in guest_entries)


# --- Operating mode ---------------------------------------------------------
# ``system.get_status.system.mode`` is an integer working-mode. Only ``0`` (router)
# is confirmed on real hardware; other values are mapped best-effort and otherwise
# surfaced raw. NOTE: firmware 4.x exposes no RPC to *change* the working mode, so
# this is read-only. ("Repeater" as an internet source is a separate thing — it is
# a WAN uplink within router mode; see the repeater_* helpers.)
_MODE_NAMES: dict[int, str] = {0: "router"}


def operating_mode(status: dict[str, Any]) -> str | None:
    """Return the router's working mode name (read-only)."""
    value = first_path(status, "system.mode", "mode")
    if value is None:
        return None
    try:
        return _MODE_NAMES.get(int(value), f"mode_{int(value)}")
    except (TypeError, ValueError):
        return str(value)


# --- Repeater (WiFi-as-WAN uplink) ------------------------------------------
# ``repeater.get_status`` describes the upstream the router is joined to as a
# client: ``{running, state, state_s, ssid, signal, channel, connected, network,
# ipv4:{...}, config:{...}}``. ``state`` 2 / ``state_s`` "connected" = up.

def repeater_connected(config: dict[str, Any] | None) -> bool | None:
    """Whether the router is connected to an upstream WiFi as a repeater."""
    if not config:
        return None
    state_s = config.get("state_s")
    if isinstance(state_s, str):
        return state_s.lower() == "connected"
    state = config.get("state")
    if state is not None:
        try:
            return int(state) >= 2
        except (TypeError, ValueError):
            pass
    if "running" in config:
        return bool(config.get("running"))
    return None


def repeater_upstream_ssid(config: dict[str, Any] | None) -> str | None:
    """Return the SSID of the upstream network the repeater is joined to."""
    if not config:
        return None
    ssid = first_path(config, "ssid", "config.ssid")
    return str(ssid) if ssid else None


def repeater_signal(config: dict[str, Any] | None) -> int | None:
    """Return the upstream signal strength in dBm, if reported."""
    if not config:
        return None
    value = config.get("signal")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def repeater_state(config: dict[str, Any] | None) -> str | None:
    """Return the repeater connection state string (e.g. 'connected')."""
    if not config:
        return None
    value = config.get("state_s")
    return str(value) if value else None


def repeater_scan_networks(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize ``repeater.scan`` results into a stable list of networks.

    Each entry: ``{ssid, bssid, band, channel, signal, encrypted, saved}``.
    """
    if not result:
        return []
    entries = result.get("res") if isinstance(result, dict) else result
    if not isinstance(entries, list):
        return []
    networks: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        encryption = entry.get("encryption")
        if isinstance(encryption, dict):
            encrypted = bool(encryption.get("enabled"))
        else:
            encrypted = bool(encryption)
        networks.append(
            {
                "ssid": entry.get("ssid"),
                "bssid": entry.get("bssid"),
                "band": entry.get("band"),
                "channel": entry.get("channel"),
                "signal": entry.get("signal"),
                "encrypted": encrypted,
                "saved": bool(entry.get("saved")),
            }
        )
    return networks


# --- Cable / tethering / tor / ddns / modem ---------------------------------

def cable_connected(config: dict[str, Any] | None) -> bool | None:
    """Whether a WAN cable carrier is present (``cable.get_status.status``)."""
    if not config:
        return None
    value = config.get("status")
    try:
        return int(value) >= 2 if value is not None else None
    except (TypeError, ValueError):
        return None


def tethering_active(config: dict[str, Any] | None) -> bool | None:
    """Whether USB tethering is active (``tethering.get_status``)."""
    if not config:
        return None
    status = config.get("status")
    if status is not None:
        try:
            return int(status) >= 1
        except (TypeError, ValueError):
            pass
    devices = config.get("devices")
    if isinstance(devices, list):
        return len(devices) > 0
    return None


def tor_enabled(config: dict[str, Any] | None) -> bool | None:
    """Whether Tor is enabled (``tor.get_config.enable``)."""
    if not config:
        return None
    value = first_path(config, "enable", "enabled")
    if value is None:
        return None
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes")
    return bool(value)


def ddns_enabled(config: dict[str, Any] | None) -> bool | None:
    """Whether dynamic DNS is enabled (``ddns.get_config.enable_ddns``)."""
    if not config:
        return None
    value = first_path(config, "enable_ddns", "enable", "enabled")
    if value is None:
        return None
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes")
    return bool(value)


def _modems(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not config:
        return []
    modems = config.get("modems")
    if isinstance(modems, list):
        return [m for m in modems if isinstance(m, dict)]
    return []


def modem_present(config: dict[str, Any] | None) -> bool | None:
    """Whether a cellular modem is present/detected."""
    if not config:
        return None
    return len(_modems(config)) > 0


def modem_state(config: dict[str, Any] | None) -> str | None:
    """Return the first modem's connection state string, if any."""
    modems = _modems(config)
    if not modems:
        return None
    value = first_path(modems[0], "state", "status", "sim_status")
    return str(value) if value is not None else None


def modem_signal(config: dict[str, Any] | None) -> int | None:
    """Return the first modem's signal (dBm or %), if reported."""
    modems = _modems(config)
    if not modems:
        return None
    value = first_path(modems[0], "signal", "rssi", "csq")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
