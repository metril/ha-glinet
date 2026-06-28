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
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GlinetApiClient,
    GlinetApiError,
    GlinetAuthError,
    GlinetConnectionError,
    GlinetError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DATA_CLIENTS,
    DATA_CONFIGS,
    DATA_FEATURES,
    DATA_INFO,
    DATA_STATUS,
    DEFAULT_SCAN_INTERVAL,
    SVC_DDNS,
    SVC_LED,
    SVC_OVPN_CLIENT,
    SVC_OVPN_SERVER,
    SVC_TAILSCALE,
    SVC_WG_CLIENT,
    SVC_WG_SERVER,
)

_LOGGER = logging.getLogger(__name__)

# Optional control-surface reads: (data key, service, method). Probed once on the
# first refresh; only the ones that succeed are polled afterwards, so models that
# lack a feature (no modem, no Tailscale, etc.) don't error every cycle.
_OPTIONAL_READS: tuple[tuple[str, str, str], ...] = (
    ("led", SVC_LED, "get_config"),
    ("ddns", SVC_DDNS, "get_status"),
    ("wg_client", SVC_WG_CLIENT, "get_status"),
    ("ovpn_client", SVC_OVPN_CLIENT, "get_status"),
    ("wg_server", SVC_WG_SERVER, "get_status"),
    ("ovpn_server", SVC_OVPN_SERVER, "get_status"),
    ("tailscale", SVC_TAILSCALE, "get_status"),
)


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
        self._info: dict[str, Any] = {}
        self._features: set[str] = set()
        # data key -> service supports being polled (set after first probe)
        self._supported: dict[str, bool] | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"GL.iNet {entry.title}",
            update_interval=timedelta(seconds=scan_interval),
        )

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
        """Poll optional control-surface reads, probing support on first run.

        A connection/auth failure propagates (handled by the caller); any other
        RPC error just marks that service unsupported so it is skipped next time.
        """
        first_run = self._supported is None
        if first_run:
            self._supported = {}

        configs: dict[str, Any] = {}
        for key, service, method in _OPTIONAL_READS:
            if not first_run and not self._supported.get(key):
                continue
            try:
                result = await self.client.call(service, method)
            except (GlinetConnectionError, GlinetAuthError):
                raise
            except GlinetApiError as err:
                if first_run:
                    _LOGGER.debug("Optional read %s.%s unsupported: %s", service, method, err)
                self._supported[key] = False
                continue
            self._supported[key] = True
            if isinstance(result, dict):
                configs[key] = result
        return configs
