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
        status, "system.cpu_temperature", "cpu.temperature", "cpu_temperature", "temperature"
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
    try:
        total_f = float(total)
        free_f = float(free)
        if total_f <= 0:
            return None
        return round((total_f - free_f) / total_f * 100, 1)
    except (TypeError, ValueError):
        return None


# --- WAN / connectivity -----------------------------------------------------

def internet_online(status: dict[str, Any]) -> bool | None:
    value = first_path(
        status, "network.online", "online", "internet", "wan.online", "system.online"
    )
    if value is None:
        return None
    if isinstance(value, str):
        return value.lower() in ("1", "true", "online", "connected", "yes")
    return bool(value)


def wan_connected(status: dict[str, Any]) -> bool | None:
    value = first_path(status, "wan.connected", "wan.status", "network.wan.connected")
    if value is None:
        return internet_online(status)
    if isinstance(value, str):
        return value.lower() in ("1", "true", "connected", "up", "online", "yes")
    return bool(value)


def wan_public_ip(status: dict[str, Any]) -> str | None:
    value = first_path(
        status, "wan.ip", "wan.ipv4", "ip_public", "public_ip", "network.wan.ip"
    )
    return str(value) if value else None


def wan_protocol(status: dict[str, Any]) -> str | None:
    value = first_path(status, "wan.proto", "wan.protocol", "network.wan.proto")
    return str(value) if value else None


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
    return client.get("name") or client.get("hostname") or client.get("ip")


def client_is_online(client: dict[str, Any]) -> bool:
    return _client_online(client)


# --- VPN / control surface --------------------------------------------------

# GL.iNet VPN ``get_status`` reports an integer ``status``: 0=stopped, 1=connecting,
# 2=connected (some firmware uses 1=connected). Treat >=1 with a connected flag as up.
def vpn_connected(config: dict[str, Any] | None) -> bool | None:
    """Return whether a VPN service (wg/ovpn/tailscale) is connected."""
    if not config:
        return None
    status = first_path(config, "status", "state", "connected", "running", "enable")
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


def wifi_radio_enabled(wifi: dict[str, Any] | None, band: str) -> bool | None:
    """Return whether the radio for ``band`` ('2g'/'5g') is enabled.

    ``wifi`` is the ``wifi.get_status`` payload; its exact shape varies, so this
    scans any list of interface/radio dicts for one matching the band.
    """
    if not wifi:
        return None
    radios = wifi.get("res") or wifi.get("interfaces") or wifi.get("wifi") or wifi
    candidates: list[dict[str, Any]] = []
    if isinstance(radios, list):
        candidates = [r for r in radios if isinstance(r, dict)]
    elif isinstance(radios, dict):
        candidates = [v for v in radios.values() if isinstance(v, dict)]
    for radio in candidates:
        name = str(
            radio.get("band") or radio.get("ifname") or radio.get("name") or ""
        ).lower()
        if band == "2g" and ("2" in name or "2.4" in name):
            return _truthy(radio.get("enabled", radio.get("enable")))
        if band == "5g" and "5" in name:
            return _truthy(radio.get("enabled", radio.get("enable")))
    return None


def _truthy(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes", "up")
    return bool(value)
