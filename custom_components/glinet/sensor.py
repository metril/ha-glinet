"""Sensor platform for GL.iNet routers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import GlinetConfigEntry, parsers
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
        translation_key="uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-outline",
        value_fn=lambda data: parsers.uptime(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: parsers.cpu_temperature(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="load_average",
        translation_key="load_average",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
        value_fn=lambda data: parsers.load_average(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="memory_used",
        translation_key="memory_used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:memory",
        value_fn=lambda data: parsers.memory_used_percent(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="connected_clients",
        translation_key="connected_clients",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:devices",
        value_fn=parsers.client_count,
    ),
    GlinetSensorDescription(
        key="wan_public_ip",
        translation_key="wan_public_ip",
        icon="mdi:ip-network",
        value_fn=parsers.wan_public_ip,
    ),
    GlinetSensorDescription(
        key="wan_interface",
        translation_key="wan_interface",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:transit-connection-variant",
        value_fn=lambda data: parsers.active_wan_interface(data.get("status", {})),
    ),
    GlinetSensorDescription(
        key="vpn_client_profile",
        translation_key="vpn_client_profile",
        icon="mdi:vpn",
        value_fn=lambda data: parsers.vpn_client_active_name(
            data.get("configs", {}).get("vpn_client")
        ),
    ),
    GlinetSensorDescription(
        key="operating_mode",
        translation_key="operating_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:router-wireless-settings",
        value_fn=lambda data: parsers.operating_mode(
            data.get("status", {}), data.get("configs", {}).get("netmode")
        ),
    ),
    GlinetSensorDescription(
        key="repeater_ssid",
        translation_key="repeater_ssid",
        icon="mdi:wifi-arrow-up-down",
        requires_config="repeater",
        value_fn=lambda data: parsers.repeater_upstream_ssid(
            data.get("configs", {}).get("repeater")
        ),
    ),
    GlinetSensorDescription(
        key="repeater_signal",
        translation_key="repeater_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
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
        translation_key="repeater_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi-arrow-up-down",
        requires_config="repeater",
        value_fn=lambda data: parsers.repeater_state(
            data.get("configs", {}).get("repeater")
        ),
    ),
    GlinetSensorDescription(
        key="modem_state",
        translation_key="modem_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:signal",
        requires_config="modem",
        gate=lambda data: parsers.modem_present(data.get("configs", {}).get("modem"))
        is True,
        value_fn=lambda data: parsers.modem_state(data.get("configs", {}).get("modem")),
    ),
    GlinetSensorDescription(
        key="modem_signal",
        translation_key="modem_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_config="modem",
        gate=lambda data: parsers.modem_present(data.get("configs", {}).get("modem"))
        is True,
        value_fn=lambda data: parsers.modem_signal(data.get("configs", {}).get("modem")),
    ),
)


PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlinetConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GL.iNet sensors."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    configs = data.get("configs", {})

    def _included(desc: GlinetSensorDescription) -> bool:
        if desc.requires_config is not None and desc.requires_config not in configs:
            return False
        if desc.gate is not None and not desc.gate(data):
            return False
        return True

    entities: list[SensorEntity] = [
        GlinetSensor(coordinator, entry, desc) for desc in SENSORS if _included(desc)
    ]
    entities.append(GlinetLastBootSensor(coordinator, entry))
    async_add_entities(entities)


class GlinetSensor(GlinetEntity, SensorEntity):
    """A GL.iNet sensor."""

    entity_description: GlinetSensorDescription

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: GlinetConfigEntry,
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


class GlinetLastBootSensor(GlinetEntity, SensorEntity):
    """Timestamp of the router's last boot (uptime subtracted from now)."""

    _attr_translation_key = "last_boot"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: GlinetDataUpdateCoordinator,
        entry: GlinetConfigEntry,
    ) -> None:
        """Initialize the last boot sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_boot"
        self._boot: datetime | None = None

    @property
    def native_value(self) -> datetime | None:
        """Return the boot time, only moving it when it drifts by more than 5 s."""
        if self.coordinator.data is None:
            return self._boot
        seconds = parsers.uptime(self.coordinator.data.get("status", {}))
        if seconds is None:
            return self._boot
        new = parsers.boot_time(seconds, dt_util.utcnow())
        if self._boot is None or abs(new - self._boot) > timedelta(seconds=5):
            self._boot = new
        return self._boot
