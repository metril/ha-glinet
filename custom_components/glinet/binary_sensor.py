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
        key="wireguard_client",
        name="WireGuard Client",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        requires_config="wg_client",
        value_fn=lambda data: parsers.vpn_connected(
            data.get("configs", {}).get("wg_client")
        ),
    ),
    GlinetBinarySensorDescription(
        key="openvpn_client",
        name="OpenVPN Client",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        requires_config="ovpn_client",
        value_fn=lambda data: parsers.vpn_connected(
            data.get("configs", {}).get("ovpn_client")
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
    configs = (coordinator.data or {}).get("configs", {})
    async_add_entities(
        GlinetBinarySensor(coordinator, entry, desc)
        for desc in BINARY_SENSORS
        if desc.requires_config is None or desc.requires_config in configs
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
