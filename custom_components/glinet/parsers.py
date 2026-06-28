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
# The working mode is read via ``netmode.get_mode`` → ``{mode: "router"|"ap"|"relay"
# |"wds", ...}`` and set via ``netmode.set_mode {mode}`` (the ``netmode`` service —
# confirmed from the router UI's own traffic). ``system.get_status.system.mode`` is a
# coarse integer fallback when ``netmode`` isn't present.
_MODE_INT_NAMES: dict[int, str] = {0: "router"}

# Selectable modes that need no upstream Wi-Fi target (relay/wds join an AP and are
# driven by the repeater flow instead).
MODE_OPTIONS: tuple[str, ...] = ("router", "ap")
MODE_LABELS: dict[str, str] = {
    "router": "Router",
    "ap": "Access Point",
    "relay": "Repeater",
    "wds": "WDS",
    "mesh": "Mesh",
}


def operating_mode(status: dict[str, Any], netmode: dict[str, Any] | None = None) -> str | None:
    """Return the router's working-mode name.

    Prefers the authoritative ``netmode.get_mode`` payload; falls back to the
    integer ``system.get_status.system.mode``.
    """
    if isinstance(netmode, dict):
        mode = netmode.get("mode")
        if mode:
            return str(mode)
    value = first_path(status, "system.mode", "mode")
    if value is None:
        return None
    try:
        return _MODE_INT_NAMES.get(int(value), f"mode_{int(value)}")
    except (TypeError, ValueError):
        return str(value)


# --- Wi-Fi interfaces (control) ---------------------------------------------
# A radio/SSID is toggled with ``wifi.set_config {iface_name, enabled}``; a full
# SSID/key change echoes the iface's complete config back (``iface_name`` is the key
# that matters — e.g. ``wifi2g``/``wifi5g``/``guest2g``/``guest5g``). These helpers
# expose the per-iface view and build the exact write payload.

# Per-iface writable fields plus the band-level fields the UI echoes on a full save.
_WIFI_IFACE_FIELDS = ("ssid", "key", "encryption", "hidden")
_WIFI_BAND_FIELDS = ("device", "hwmode", "channel", "htmode", "txpower", "random_bssid")


def wifi_status_ifaces(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Return per-iface live state from ``system.get_status.wifi``.

    Each entry: ``{iface_name, band, guest, up, ssid}``.
    """
    wifi = status.get("wifi")
    if not isinstance(wifi, list):
        return []
    out = []
    for entry in wifi:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        out.append(
            {
                "iface_name": entry.get("name"),
                "band": entry.get("band"),
                "guest": bool(entry.get("guest")),
                "up": bool(entry.get("up")),
                "ssid": entry.get("ssid"),
            }
        )
    return out


def wifi_iface_up(status: dict[str, Any], iface_name: str) -> bool | None:
    """Return whether a named Wi-Fi iface (e.g. ``wifi2g``) is up."""
    for entry in wifi_status_ifaces(status):
        if entry["iface_name"] == iface_name:
            return entry["up"]
    return None


def wifi_config_ifaces(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten ``wifi.get_config`` into per-iface dicts carrying band-level context.

    Each entry merges the iface fields (``ssid``/``key``/``encryption``/``hidden``/
    ``guest``/``name``) with its band's ``device``/``hwmode``/``channel``/``htmode``/
    ``txpower``/``random_bssid`` — everything needed to round-trip ``set_config``.
    """
    if not config:
        return []
    res = config.get("res")
    if not isinstance(res, list):
        return []
    out: list[dict[str, Any]] = []
    for band in res:
        if not isinstance(band, dict):
            continue
        band_ctx = {k: band.get(k) for k in _WIFI_BAND_FIELDS}
        for iface in band.get("ifaces", []) or []:
            if not isinstance(iface, dict) or not iface.get("name"):
                continue
            merged = {
                "iface_name": iface.get("name"),
                "band": band.get("band"),
                "guest": bool(iface.get("guest")),
                **{k: iface.get(k) for k in _WIFI_IFACE_FIELDS},
                **band_ctx,
            }
            out.append(merged)
    return out


def wifi_set_payload(
    config: dict[str, Any] | None, iface_name: str, **override: Any
) -> dict[str, Any] | None:
    """Build the full ``wifi.set_config`` payload for an iface with overrides.

    Mirrors the request the GL.iNet UI sends on a Wi-Fi "Apply": the iface's
    ``ssid``/``key``/``encryption``/``hidden`` plus band ``device``/``hwmode``/
    ``channel``/``htmode``/``txpower``/``random_bssid``. Returns ``None`` if the iface
    isn't found.
    """
    for iface in wifi_config_ifaces(config):
        if iface["iface_name"] == iface_name:
            payload = {"iface_name": iface_name}
            for key in (*_WIFI_IFACE_FIELDS, *_WIFI_BAND_FIELDS):
                if iface.get(key) is not None:
                    payload[key] = iface[key]
            payload.update(override)
            return payload
    return None


def wifi_iface_value(config: dict[str, Any] | None, iface_name: str, field: str) -> Any:
    """Return a single field (e.g. ``ssid``) for a named iface from wifi config."""
    for iface in wifi_config_ifaces(config):
        if iface["iface_name"] == iface_name:
            return iface.get(field)
    return None


# --- Firmware update --------------------------------------------------------
# ``upgrade.check_firmware_online`` → ``{current_version, prompt, current_type,
# current_compile_time}``; ``prompt`` true = a newer firmware is offered.

def firmware_update_available(config: dict[str, Any] | None) -> bool | None:
    """Whether a firmware update is available (``prompt``)."""
    if not config:
        return None
    return bool(config.get("prompt"))


def firmware_current_version(config: dict[str, Any] | None) -> str | None:
    """Return the installed firmware version from the upgrade check."""
    if not config:
        return None
    value = config.get("current_version")
    return str(value) if value else None


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
