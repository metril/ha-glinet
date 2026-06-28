"""Sensor platform for GL.iNet routers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .const import DOMAIN
from .coordinator import GlinetDataUpdateCoordinator
from .entity import GlinetEntity


@dataclass(frozen=True)
class GlinetSensorDescription(SensorEntityDescription):
    """Describes a GL.iNet sensor."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda data: None


SENSORS: tuple[GlinetSensorDescription, ...] = (
    GlinetSensorDescription(
        key="uptime",
        name="Uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-outline",
        value_fn=lambda data: parsers.uptime(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="cpu_temperature",
        name="CPU Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: parsers.cpu_temperature(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="load_average",
        name="Load Average",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
        value_fn=lambda data: parsers.load_average(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="memory_used",
        name="Memory Used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:memory",
        value_fn=lambda data: parsers.memory_used_percent(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="connected_clients",
        name="Connected Clients",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:devices",
        value_fn=parsers.client_count,
    ),
    GlinetSensorDescription(
        key="wan_public_ip",
        name="WAN IP",
        icon="mdi:ip-network",
        value_fn=parsers.wan_public_ip,
    ),
    GlinetSensorDescription(
        key="wan_interface",
        name="WAN Interface",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:transit-connection-variant",
        value_fn=lambda data: parsers.active_wan_interface(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="vpn_client_profile",
        name="VPN Client Profile",
        icon="mdi:vpn",
        value_fn=lambda data: parsers.vpn_client_active_name(
            data.get("configs", {}).get("vpn_client")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet sensors."""
    coordinator: GlinetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities(GlinetSensor(coordinator, entry, desc) for desc in SENSORS)


class GlinetSensor(GlinetEntity, SensorEntity):
    """A GL.iNet sensor."""

    entity_description: GlinetSensorDescription

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: ConfigEntry,
        description: GlinetSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
