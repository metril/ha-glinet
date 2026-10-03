"""The GL.iNet Router integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import GlinetApiClient
from .const import CONF_HOST, CONF_PASSWORD, DOMAIN
from .coordinator import GlinetDataUpdateCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

type GlinetConfigEntry = ConfigEntry[GlinetDataUpdateCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services (once per Home Assistant run)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GlinetConfigEntry) -> bool:
    """Set up GL.iNet Router from a config entry."""
    session = async_get_clientsession(hass)
    client = GlinetApiClient(
        session=session,
        host=entry.data[CONF_HOST],
        password=entry.data[CONF_PASSWORD],
    )

    coordinator = GlinetDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GlinetConfigEntry) -> bool:
    """Unload a GL.iNet Router config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.client.async_logout()
    return unload_ok
