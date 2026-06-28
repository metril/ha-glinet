"""Text platform for GL.iNet routers — editable Wi-Fi SSID and password.

Each non-guest and guest Wi-Fi iface gets an **SSID** text entity and a **password**
text entity. Writing calls ``wifi.set_config`` with the iface's full config echoed
back (the exact request the GL.iNet UI sends on "Apply"), changing only the edited
field. The keying field is ``iface_name`` (e.g. ``wifi2g``).

Security note: the password entity's state is the live Wi-Fi key (mirroring the
router UI, which pre-fills it). It is rendered masked in the UI, but — like any HA
text entity — the value lives in the state machine. Disable the entity if you'd
rather not store it.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import GlinetError
from .const import DOMAIN, SVC_WIFI
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity

_LOGGER = logging.getLogger(__name__)

_BAND_LABEL = {"2G": "2.4 GHz", "5G": "5 GHz", "6G": "6 GHz"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Wi-Fi SSID/password text entities (one pair per iface)."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    known: set[str] = set()

    @callback
    def _add() -> None:
        wifi_config = (coordinator.data or {}).get("configs", {}).get("wifi_config")
        new: list[TextEntity] = []
        for iface in parsers.wifi_config_ifaces(wifi_config):
            name = iface["iface_name"]
            if name in known:
                continue
            known.add(name)
            new.append(GlinetWifiText(coordinator, entry, iface, "ssid"))
            new.append(GlinetWifiText(coordinator, entry, iface, "key"))
        if new:
            async_add_entities(new)

    _add()
    entry.async_on_unload(coordinator.async_add_listener(_add))


class GlinetWifiText(GlinetEntity, TextEntity):
    """An editable Wi-Fi SSID or password field for one iface."""

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
        iface: dict[str, Any],
        field: str,
    ) -> None:
        """Initialize the text entity (``field`` is ``ssid`` or ``key``)."""
        super().__init__(coordinator, entry)
        self._iface_name = iface["iface_name"]
        self._field = field
        band = _BAND_LABEL.get(str(iface.get("band")), str(iface.get("band") or ""))
        kind = "Guest " if iface.get("guest") else ""
        label = "SSID" if field == "ssid" else "Password"
        self._attr_name = f"{band} {kind}Wi-Fi {label}".strip()
        self._attr_unique_id = f"{entry.entry_id}_wifitext_{self._iface_name}_{field}"
        if field == "key":
            self._attr_mode = TextMode.PASSWORD
            self._attr_icon = "mdi:wifi-lock"
            self._attr_native_min = 8
            self._attr_native_max = 63
        else:
            self._attr_icon = "mdi:wifi-cog"
            self._attr_native_max = 32

    def _wifi_config(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("configs", {}).get("wifi_config")

    @property
    def native_value(self) -> str | None:
        """Return the current SSID or key for this iface."""
        value = parsers.wifi_iface_value(self._wifi_config(), self._iface_name, self._field)
        return str(value) if value is not None else None

    async def async_set_value(self, value: str) -> None:
        """Write a new SSID or password, echoing the rest of the iface config."""
        payload = parsers.wifi_set_payload(
            self._wifi_config(), self._iface_name, **{self._field: value}
        )
        if payload is None:
            raise HomeAssistantError(f"Wi-Fi iface {self._iface_name} not found")
        try:
            await self.coordinator.client.call(SVC_WIFI, "set_config", payload)
        except GlinetError as err:
            raise HomeAssistantError(
                f"Failed to update Wi-Fi {self._iface_name} {self._field}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
