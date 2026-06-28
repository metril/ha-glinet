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
    if not config:
        return None
    status_list = config.get("status_list")
    if isinstance(status_list, list):
        for entry in status_list:
            if isinstance(entry, dict) and entry.get("enabled"):
                return entry.get("name")
    return None


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
