"""DataUpdateCoordinator for GL.iNet routers.

GL.iNet firmware 4.x has no push channel reachable by an external client (the
``/rpc`` JSON-RPC bridge is request/response only; ubus ``subscribe`` is local to
the router, and there is no WebSocket/SSE/local-MQTT). So, like the IPMI
integration, this polls on a user-configurable interval.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
    MODE_ARM_TIMEOUT,
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
    SVC_WIFI,
)

_LOGGER = logging.getLogger(__name__)

# Reads grouped by how fast their data changes. All are probed once on the first
# refresh; only the ones that succeed are polled afterwards, so models lacking a
# feature (no modem, no Tailscale, …) don't error every cycle.
#
# FAST: dynamic status, fetched every poll cycle.
_FAST_READS: tuple[tuple[str, str, str], ...] = (
    ("ddns", SVC_DDNS, "get_status"),
    ("vpn_client", SVC_VPN_CLIENT, "get_status"),
    ("wg_server", SVC_WG_SERVER, "get_status"),
    ("ovpn_server", SVC_OVPN_SERVER, "get_status"),
    ("tailscale", SVC_TAILSCALE, "get_status"),
    ("repeater", SVC_REPEATER, "get_status"),
    ("cable", SVC_CABLE, "get_status"),
    ("tethering", SVC_TETHERING, "get_status"),
    ("modem", SVC_MODEM, "get_status"),
)

# CONFIG: rarely changes; polled on the (configurable) config interval. Writes
# invalidate the relevant key so an edit reflects on the next refresh immediately.
_CONFIG_READS: tuple[tuple[str, str, str], ...] = (
    ("led", SVC_LED, "get_config"),
    ("ddns_config", SVC_DDNS, "get_config"),
    ("tor", SVC_TOR, "get_config"),
    ("wifi_config", SVC_WIFI, "get_config"),
    ("netmode", SVC_NETMODE, "get_mode"),
    ("repeater_saved", SVC_REPEATER, "get_saved_ap_list"),
)

# SLOW: fixed long interval (e.g. an online firmware check that hits GL's servers).
_SLOW_READS: tuple[tuple[str, str, str], ...] = (
    ("firmware", SVC_UPGRADE, "check_firmware_online"),
)
_SLOW_READ_INTERVAL = timedelta(hours=6)


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

        # Shared UI state (not from the router): the chosen VPN target, whether a
        # mode change is "armed", and the last on-demand repeater scan result.
        self.vpn_target: Any = None
        self.mode_armed: bool = False
        self.repeater_scan: dict[str, Any] | None = None
        self._arm_unsub: Any = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"GL.iNet {entry.title}",
            update_interval=timedelta(seconds=scan_interval),
        )

    def invalidate(self, *keys: str) -> None:
        """Force the next refresh to re-fetch these throttled (config/slow) reads.

        Called by write paths so a config edit is reflected immediately on the
        following ``async_request_refresh`` rather than waiting out the slow tier.
        """
        for key in keys:
            self._slow_last.pop(key, None)

    @callback
    def arm_mode(self) -> None:
        """Arm the operating-mode switch; auto-disarm after ``MODE_ARM_TIMEOUT``."""
        if self._arm_unsub is not None:
            self._arm_unsub()
        self.mode_armed = True

        @callback
        def _auto_disarm(_now: Any) -> None:
            self._arm_unsub = None
            self.disarm_mode()

        self._arm_unsub = async_call_later(self.hass, MODE_ARM_TIMEOUT, _auto_disarm)
        self.async_update_listeners()

    @callback
    def disarm_mode(self) -> None:
        """Disarm the operating-mode switch and cancel any pending auto-disarm."""
        if self._arm_unsub is not None:
            self._arm_unsub()
            self._arm_unsub = None
        if self.mode_armed:
            self.mode_armed = False
            self.async_update_listeners()

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
            # system.get_info is largely static — fetch once and cache.
            if not self._info:
                self._info = await self.client.get_info()
                self._features = parse_features(self._info)

            status = await self.client.get_status()
            clients = await self.client.get_clients()
            configs = await self._fetch_optional()
        except GlinetAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GlinetConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except GlinetError as err:
            raise UpdateFailed(str(err)) from err

        return {
            DATA_INFO: self._info,
            DATA_STATUS: status,
            DATA_CLIENTS: clients,
            DATA_FEATURES: self._features,
            DATA_CONFIGS: configs,
        }

    async def _fetch_optional(self) -> dict[str, Any]:
        """Poll the tiered control-surface reads, probing support on first run.

        FAST reads run every cycle; CONFIG reads run at the config interval; SLOW
        reads at a fixed long interval. A connection/auth failure propagates; any
        other RPC error marks that service unsupported so it is skipped next time.
        """
        first_run = self._supported is None
        if first_run:
            self._supported = {}

        configs: dict[str, Any] = {}
        for key, service, method in _FAST_READS:
            await self._fetch_one(key, service, method, configs, first_run)

        await self._fetch_throttled(
            _CONFIG_READS, self._config_interval, configs, first_run
        )
        await self._fetch_throttled(
            _SLOW_READS, _SLOW_READ_INTERVAL.total_seconds(), configs, first_run
        )
        return configs

    async def _fetch_one(
        self,
        key: str,
        service: str,
        method: str,
        configs: dict[str, Any],
        first_run: bool,
    ) -> None:
        """Fetch a single read into ``configs`` (every cycle), handling support."""
        if not first_run and not self._supported.get(key):
            return
        try:
            result = await self.client.call(service, method)
        except (GlinetConnectionError, GlinetAuthError):
            raise
        except GlinetApiError as err:
            if first_run:
                _LOGGER.debug("Read %s.%s unsupported: %s", service, method, err)
            self._supported[key] = False
            return
        self._supported[key] = True
        if isinstance(result, dict):
            configs[key] = result

    async def _fetch_throttled(
        self,
        reads: tuple[tuple[str, str, str], ...],
        interval: float,
        configs: dict[str, Any],
        first_run: bool,
    ) -> None:
        """Refresh throttled reads at most every ``interval`` s; reuse cache otherwise.

        ``invalidate(key)`` drops the key's timestamp so it re-fetches next cycle.
        """
        now = self.hass.loop.time()
        for key, service, method in reads:
            if not first_run and not self._supported.get(key):
                continue
            due = (key not in self._slow_last) or (now - self._slow_last[key] >= interval)
            if due:
                try:
                    result = await self.client.call(service, method)
                except (GlinetConnectionError, GlinetAuthError):
                    raise
                except GlinetApiError as err:
                    if first_run:
                        _LOGGER.debug("Read %s.%s unsupported: %s", service, method, err)
                    self._supported[key] = False
                    continue
                self._supported[key] = True
                self._slow_last[key] = now
                if isinstance(result, dict):
                    self._slow_cache[key] = result
            if key in self._slow_cache:
                configs[key] = self._slow_cache[key]
