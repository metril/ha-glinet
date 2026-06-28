"""Device-scoped services for the GL.iNet integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from . import parsers
from .api import GlinetApiClient, GlinetError
from .const import (
    DOMAIN,
    SVC_CLIENTS,
    SVC_REPEATER,
    SVC_WIFI,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_BLOCK_CLIENT = "block_client"
SERVICE_CONNECT_REPEATER = "connect_repeater"
SERVICE_SCAN_REPEATER = "scan_repeater"
SERVICE_SET_WIFI = "set_wifi"

ATTR_DEVICE_ID = "device_id"

BLOCK_CLIENT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required("mac"): cv.string,
        vol.Required("blocked"): cv.boolean,
    }
)

CONNECT_REPEATER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required("ssid"): cv.string,
        vol.Optional("password", default=""): cv.string,
    }
)

SCAN_REPEATER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
    }
)

SET_WIFI_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required("iface_name"): cv.string,
        vol.Optional("ssid"): cv.string,
        vol.Optional("key"): cv.string,
        vol.Optional("enabled"): cv.boolean,
    }
)


def _client_for_device(hass: HomeAssistant, device_id: str) -> GlinetApiClient:
    """Resolve a device_id to its GL.iNet API client."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Unknown device: {device_id}")
    for identifier in device.identifiers:
        if identifier[0] == DOMAIN:
            entry_id = identifier[1]
            data = hass.data.get(DOMAIN, {}).get(entry_id)
            if data:
                return data["client"]
    raise HomeAssistantError(f"Device {device_id} is not a GL.iNet router")


def async_register_services(hass: HomeAssistant) -> None:
    """Register GL.iNet services once per Home Assistant instance."""

    async def _handle_block_client(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        try:
            await client.call(
                SVC_CLIENTS,
                "block_client",
                {"mac": call.data["mac"], "block": call.data["blocked"]},
            )
        except GlinetError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_connect_repeater(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        params: dict[str, Any] = {"ssid": call.data["ssid"]}
        if call.data.get("password"):
            params["key"] = call.data["password"]
        try:
            await client.call(SVC_REPEATER, "connect", params)
        except GlinetError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_scan_repeater(call: ServiceCall) -> ServiceResponse:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        try:
            result = await client.call(SVC_REPEATER, "scan")
        except GlinetError as err:
            raise HomeAssistantError(str(err)) from err
        return {"networks": parsers.repeater_scan_networks(result)}

    async def _handle_set_wifi(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        iface_name = call.data["iface_name"]
        overrides = {k: call.data[k] for k in ("ssid", "key", "enabled") if k in call.data}
        try:
            # An enable-only change is a minimal write; SSID/key changes must echo the
            # iface's full config (the GL.iNet UI's contract), so fetch and merge it.
            if set(overrides) <= {"enabled"}:
                params: dict[str, Any] = {"iface_name": iface_name, **overrides}
            else:
                config = await client.call(SVC_WIFI, "get_config")
                params = parsers.wifi_set_payload(config, iface_name, **overrides)
                if params is None:
                    raise HomeAssistantError(f"Wi-Fi iface {iface_name} not found")
                if "enabled" in overrides:
                    params["enabled"] = overrides["enabled"]
            await client.call(SVC_WIFI, "set_config", params)
        except GlinetError as err:
            raise HomeAssistantError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_BLOCK_CLIENT):
        hass.services.async_register(
            DOMAIN, SERVICE_BLOCK_CLIENT, _handle_block_client, schema=BLOCK_CLIENT_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CONNECT_REPEATER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CONNECT_REPEATER,
            _handle_connect_repeater,
            schema=CONNECT_REPEATER_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SCAN_REPEATER):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SCAN_REPEATER,
            _handle_scan_repeater,
            schema=SCAN_REPEATER_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_WIFI):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_WIFI, _handle_set_wifi, schema=SET_WIFI_SCHEMA
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove GL.iNet services when the last entry is unloaded."""
    for service in (
        SERVICE_BLOCK_CLIENT,
        SERVICE_CONNECT_REPEATER,
        SERVICE_SCAN_REPEATER,
        SERVICE_SET_WIFI,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
