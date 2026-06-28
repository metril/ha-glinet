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
    UnitOfSignalStrength,
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
    # data["configs"] key required for this entity to be created (None = always).
    requires_config: str | None = None
    # Extra gate evaluated against coordinator.data (e.g. only if a modem exists).
    gate: Callable[[dict[str, Any]], bool] | None = None


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
    GlinetSensorDescription(
        key="operating_mode",
        name="Operating Mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:router-wireless-settings",
        value_fn=lambda data: parsers.operating_mode(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="repeater_ssid",
        name="Repeater Upstream SSID",
        icon="mdi:wifi-arrow-up-down",
        requires_config="repeater",
        value_fn=lambda data: parsers.repeater_upstream_ssid(
            data.get("configs", {}).get("repeater")
        ),
    ),
    GlinetSensorDescription(
        key="repeater_signal",
        name="Repeater Signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=UnitOfSignalStrength.DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires_config="repeater",
        value_fn=lambda data: parsers.repeater_signal(
            data.get("configs", {}).get("repeater")
        ),
    ),
    GlinetSensorDescription(
        key="repeater_state",
        name="Repeater State",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi-arrow-up-down",
        requires_config="repeater",
        value_fn=lambda data: parsers.repeater_state(
            data.get("configs", {}).get("repeater")
        ),
    ),
    GlinetSensorDescription(
        key="modem_state",
        name="Modem State",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:signal",
        requires_config="modem",
        gate=lambda data: parsers.modem_present(data.get("configs", {}).get("modem"))
        is True,
        value_fn=lambda data: parsers.modem_state(data.get("configs", {}).get("modem")),
    ),
    GlinetSensorDescription(
        key="modem_signal",
        name="Modem Signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=UnitOfSignalStrength.DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_config="modem",
        gate=lambda data: parsers.modem_present(data.get("configs", {}).get("modem"))
        is True,
        value_fn=lambda data: parsers.modem_signal(data.get("configs", {}).get("modem")),
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
    data = coordinator.data or {}
    configs = data.get("configs", {})

    def _included(desc: GlinetSensorDescription) -> bool:
        if desc.requires_config is not None and desc.requires_config not in configs:
            return False
        if desc.gate is not None and not desc.gate(data):
            return False
        return True

    async_add_entities(
        GlinetSensor(coordinator, entry, desc) for desc in SENSORS if _included(desc)
    )


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
