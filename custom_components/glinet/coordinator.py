"""DataUpdateCoordinator for GL.iNet routers.

GL.iNet firmware 4.x has no push channel reachable by an external client (the
``/rpc`` JSON-RPC bridge is request/response only; ubus ``subscribe`` is local to
the router, and there is no WebSocket/SSE/local-MQTT). So, like the IPMI
integration, this polls on a user-configurable interval.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import parsers
from .api import (
    GlinetApiClient,
    GlinetApiError,
    GlinetAuthError,
    GlinetConnectionError,
    GlinetError,
)
from .const import (
    CONF_CONFIG_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    DATA_CLIENTS,
    DATA_CONFIGS,
    DATA_FEATURES,
    DATA_INFO,
    DATA_STATUS,
    DEFAULT_CONFIG_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SVC_CABLE,
    SVC_DDNS,
    SVC_LED,
    SVC_MODEM,
    SVC_NETMODE,
    SVC_OVPN_SERVER,
    SVC_REPEATER,
    SVC_TAILSCALE,
    SVC_TETHERING,
    SVC_TOR,
    SVC_UPGRADE,
    SVC_VPN_CLIENT,
    SVC_WG_SERVER,
)

_LOGGER = logging.getLogger(__name__)

# Reads grouped by how fast their data changes. All are probed once on the first
# refresh; only the ones that succeed are polled afterwards, so models lacking a
# feature (no modem, no Tailscale, …) don't error every cycle.
#
# FAST: dynamic status, fetched every poll cycle.
_FAST_READS: tuple[tuple[str, str, str], ...] = (
    ("vpn_client", SVC_VPN_CLIENT, "get_status"),
    ("tailscale", SVC_TAILSCALE, "get_status"),
    ("repeater", SVC_REPEATER, "get_status"),
    ("cable", SVC_CABLE, "get_status"),
    ("tethering", SVC_TETHERING, "get_status"),
    ("modem", SVC_MODEM, "get_status"),
    ("wg_server", SVC_WG_SERVER, "get_status"),
    ("ovpn_server", SVC_OVPN_SERVER, "get_status"),
)

# CONFIG: rarely changes; polled on the (configurable) config interval. Writes
# invalidate the relevant key so an edit reflects on the next refresh immediately.
_CONFIG_READS: tuple[tuple[str, str, str], ...] = (
    ("ddns", SVC_DDNS, "get_status"),
    ("led", SVC_LED, "get_config"),
    ("ddns_config", SVC_DDNS, "get_config"),
    ("tor", SVC_TOR, "get_config"),
    ("netmode", SVC_NETMODE, "get_mode"),
    ("repeater_saved", SVC_REPEATER, "get_saved_ap_list"),
)

# SLOW: fixed long interval (e.g. an online firmware check that hits GL's servers).
_SLOW_READS: tuple[tuple[str, str, str], ...] = (
    ("firmware", SVC_UPGRADE, "check_firmware_online"),
)
_SLOW_READ_INTERVAL = timedelta(hours=6)
# Max concurrent RPC reads per refresh (the router's uhttpd is single-threaded-ish).
_READ_CONCURRENCY = 3


def parse_features(info: dict[str, Any]) -> set[str]:
    """Normalize ``hardware_feature``/``software_feature`` into a flat name set.

    GL.iNet returns feature info in a few shapes across models/firmware (a dict of
    booleans, a list of names, or nested dicts). This flattens whatever is present
    into a set of truthy feature names so platforms can gate model-specific
    entities (e.g. ``modem``, ``battery``).
    """
    features: set[str] = set()
    for key in ("hardware_feature", "software_feature", "feature"):
        block = info.get(key)
        if isinstance(block, dict):
            for name, value in block.items():
                if value:
                    features.add(name)
        elif isinstance(block, list):
            features.update(str(name) for name in block)
    return features


class GlinetDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls a single GL.iNet router."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: GlinetApiClient,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        self.entry = entry
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._config_interval = entry.options.get(
            CONF_CONFIG_SCAN_INTERVAL, DEFAULT_CONFIG_SCAN_INTERVAL
        )
        self._info: dict[str, Any] = {}
        self._features: set[str] = set()
        # data key -> service supports being polled (set after first probe)
        self._supported: dict[str, bool] | None = None
        # Throttled-read caches: last value + monotonic timestamp of last fetch.
        self._slow_cache: dict[str, Any] = {}
        self._slow_last: dict[str, float] = {}
        # Lowercased MAC -> client dict, rebuilt every update.
        self.clients_by_mac: dict[str, dict[str, Any]] = {}

        # Shared UI state (not from the router): the chosen VPN target and the
        # chosen repeater target (a saved-network label, see select/switch).
        self.vpn_target: Any = None
        self.repeater_target: Any = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"GL.iNet {entry.title}",
            update_interval=timedelta(seconds=scan_interval),
            config_entry=entry,
        )

    def invalidate(self, *keys: str) -> None:
        """Force the next refresh to re-fetch these throttled (config/slow) reads.

        Called by write paths so a config edit is reflected immediately on the
        following ``async_request_refresh`` rather than waiting out the slow tier.
        """
        for key in keys:
            self._slow_last.pop(key, None)

    @property
    def info(self) -> dict[str, Any]:
        """Return cached device info (model/firmware/mac)."""
        return self._info

    @property
    def features(self) -> set[str]:
        """Return the set of detected feature flags."""
        return self._features

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch status + clients (and info on first run)."""
        try:
            # system.get_info is largely static — fetch once, re-read on the slow tier.
            if not self._info:
                self._info = await self.client.get_info()
                self._features = parse_features(self._info)
                self._slow_last["info"] = self.hass.loop.time()
            else:
                await self._refresh_info()

            status = await self.client.get_status()
            clients = await self.client.get_clients()
            configs = await self._fetch_optional()
        except GlinetAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GlinetError as err:
            raise UpdateFailed(str(err)) from err

        self.clients_by_mac = {
            mac: c
            for c in (clients if isinstance(clients, list) else [])
            if isinstance(c, dict) and (mac := parsers.client_mac(c))
        }

        return {
            DATA_INFO: self._info,
            DATA_STATUS: status,
            DATA_CLIENTS: clients,
            DATA_FEATURES: self._features,
            DATA_CONFIGS: configs,
        }

    async def _refresh_info(self) -> None:
        """Re-read system.get_info on the slow tier; sync the registry sw_version."""
        now = self.hass.loop.time()
        last = self._slow_last.get("info")
        if last is not None and now - last < _SLOW_READ_INTERVAL.total_seconds():
            return
        try:
            info = await self.client.get_info()
        except GlinetApiError as err:
            _LOGGER.debug("system.get_info re-read failed: %s", err)
            return
        self._slow_last["info"] = now
        if not isinstance(info, dict) or not info:
            return
        old = self._info.get("firmware_version") or self._info.get("version")
        new = info.get("firmware_version") or info.get("version")
        self._info = info
        self._features = parse_features(info)
        if new and new != old:
            registry = dr.async_get(self.hass)
            device = registry.async_get_device(identifiers={(DOMAIN, self.entry.entry_id)})
            if device is not None:
                registry.async_update_device(device.id, sw_version=new)

    async def _fetch_optional(self) -> dict[str, Any]:
        """Poll the tiered control-surface reads concurrently, probing on first run.

        FAST reads run every cycle; CONFIG reads run at the config interval; SLOW
        reads at a fixed long interval. Connection/auth failures propagate. On the
        first run any other RPC error marks that service unsupported (never polled
        again); on later runs it is just logged and the cached value is kept.
        """
        first_run = self._supported is None
        if first_run:
            self._supported = {}

        now = self.hass.loop.time()
        sem = asyncio.Semaphore(_READ_CONCURRENCY)
        jobs: list[tuple[str, str, str, bool]] = []  # key, service, method, throttled
        for reads, interval in (
            (_FAST_READS, None),
            (_CONFIG_READS, self._config_interval),
            (_SLOW_READS, _SLOW_READ_INTERVAL.total_seconds()),
        ):
            for key, service, method in reads:
                if not first_run and not self._supported.get(key):
                    continue
                if interval is not None:
                    last = self._slow_last.get(key)
                    if last is not None and now - last < interval:
                        continue
                jobs.append((key, service, method, interval is not None))

        async def _run(key: str, service: str, method: str, throttled: bool) -> None:
            async with sem:
                try:
                    result = await self.client.call(service, method)
                except (GlinetConnectionError, GlinetAuthError):
                    raise
                except GlinetApiError as err:
                    if first_run:
                        _LOGGER.debug("Read %s.%s unsupported: %s", service, method, err)
                        self._supported[key] = False
                    else:
                        _LOGGER.debug("Read %s.%s failed (kept cache): %s", service, method, err)
                    return
            self._supported[key] = True
            if throttled:
                self._slow_last[key] = now
            if isinstance(result, dict):
                self._slow_cache[key] = result

        results = await asyncio.gather(
            *(_run(*job) for job in jobs), return_exceptions=True
        )
        for res in results:
            if isinstance(res, BaseException):
                raise res

        return {
            key: self._slow_cache[key]
            for key in self._slow_cache
            if self._supported.get(key)
        }


type GlinetConfigEntry = ConfigEntry[GlinetDataUpdateCoordinator]
# Name used in the cross-agent contract.
GlinetCoordinator = GlinetDataUpdateCoordinator
