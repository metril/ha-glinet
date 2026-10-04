"""Diagnostics support for GL.iNet."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import GlinetConfigEntry

TO_REDACT = {
    "password",
    "sn_bak",
    "device_id",
    "cert",
    "key",
    "sid",
    "mac",
    "macaddr",
    "bssid",
    "ip",
    "ipv4",
    "ipv6",
    "ssid",
    "alias",
    "name",
    "hostname",
    "ddns",
    "domain",
    "serial",
    "sn",
    "lan_mac",
    "factory_mac",
    "wan_mac",
    "public_key",
    "private_key",
    "host",
}


def _jsonable(value: Any) -> Any:
    """Convert sets (feature flags) to sorted lists so the payload serializes."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GlinetConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "info": async_redact_data(_jsonable(coordinator.info), TO_REDACT),
        "data": async_redact_data(_jsonable(coordinator.data or {}), TO_REDACT),
        "supported_reads": coordinator.supported_reads,
    }
