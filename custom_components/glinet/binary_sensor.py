"""Binary sensor platform for GL.iNet routers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .const import DOMAIN
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity


@dataclass(frozen=True)
class GlinetBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a GL.iNet binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda data: None
    # data["configs"] key required for this entity to be created (None = always).
    requires_config: str | None = None
    # Extra gate evaluated against coordinator.data (e.g. only if a modem exists).
    gate: Callable[[dict[str, Any]], bool] | None = None


BINARY_SENSORS: tuple[GlinetBinarySensorDescription, ...] = (
    GlinetBinarySensorDescription(
        key="internet",
        name="Internet",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: parsers.internet_online(data.get("status", {})),
    ),
    GlinetBinarySensorDescription(
        key="wan",
        name="WAN",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: parsers.wan_connected(data.get("status", {})),
    ),
    GlinetBinarySensorDescription(
        key="wifi_2g",
        name="2.4 GHz Wi-Fi",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:wifi",
        value_fn=lambda data: parsers.wifi_band_up(data.get("status", {}), "2G", False),
    ),
    GlinetBinarySensorDescription(
        key="wifi_5g",
        name="5 GHz Wi-Fi",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:wifi",
        value_fn=lambda data: parsers.wifi_band_up(data.get("status", {}), "5G", False),
    ),
    GlinetBinarySensorDescription(
        key="guest_wifi",
        name="Guest Wi-Fi",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:wifi-lock",
        value_fn=lambda data: parsers.guest_wifi_up(data.get("status", {})),
    ),
    GlinetBinarySensorDescription(
        key="vpn_client",
        name="VPN Client",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        requires_config="vpn_client",
        value_fn=lambda data: parsers.vpn_client_connected(
            data.get("configs", {}).get("vpn_client")
        ),
    ),
    GlinetBinarySensorDescription(
        key="tailscale",
        name="Tailscale",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        requires_config="tailscale",
        value_fn=lambda data: parsers.vpn_connected(
            data.get("configs", {}).get("tailscale")
        ),
    ),
    GlinetBinarySensorDescription(
        key="repeater",
        name="Repeater",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:wifi-arrow-up-down",
        requires_config="repeater",
        value_fn=lambda data: parsers.repeater_connected(
            data.get("configs", {}).get("repeater")
        ),
    ),
    GlinetBinarySensorDescription(
        key="cable",
        name="WAN Cable",
        device_class=BinarySensorDeviceClass.PLUG,
        icon="mdi:ethernet-cable",
        requires_config="cable",
        value_fn=lambda data: parsers.cable_connected(
            data.get("configs", {}).get("cable")
        ),
    ),
    GlinetBinarySensorDescription(
        key="tethering",
        name="USB Tethering",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:usb",
        requires_config="tethering",
        value_fn=lambda data: parsers.tethering_active(
            data.get("configs", {}).get("tethering")
        ),
    ),
    GlinetBinarySensorDescription(
        key="ddns",
        name="Dynamic DNS",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:dns",
        requires_config="ddns_config",
        value_fn=lambda data: parsers.ddns_enabled(
            data.get("configs", {}).get("ddns_config")
        ),
    ),
    GlinetBinarySensorDescription(
        key="modem",
        name="Cellular Modem",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:signal",
        requires_config="modem",
        gate=lambda data: parsers.modem_present(data.get("configs", {}).get("modem"))
        is True,
        value_fn=lambda data: parsers.modem_present(
            data.get("configs", {}).get("modem")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet binary sensors (gating optional ones on available data)."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    data = coordinator.data or {}
    configs = data.get("configs", {})

    def _included(desc: GlinetBinarySensorDescription) -> bool:
        if desc.requires_config is not None and desc.requires_config not in configs:
            return False
        if desc.gate is not None and not desc.gate(data):
            return False
        return True

    async_add_entities(
        GlinetBinarySensor(coordinator, entry, desc)
        for desc in BINARY_SENSORS
        if _included(desc)
    )


class GlinetBinarySensor(GlinetEntity, BinarySensorEntity):
    """A GL.iNet binary sensor."""

    entity_description: GlinetBinarySensorDescription

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
        description: GlinetBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return whether the binary sensor is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
