"""Sensors: active uplink, last failover, and per uplink latency and availability."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import NetworkPlusEntity, UplinkEntity, add_uplink_entities
from .model import Uplink

# Read-only entities of a coordinator: no limit needed
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    async_add_entities([ActiveUplinkSensor(entry), LastTransitionSensor(entry)])

    def _create(uplink: Uplink) -> list[SensorEntity]:
        return [UplinkLatencySensor(entry, uplink), UplinkAvailabilitySensor(entry, uplink)]

    add_uplink_entities(entry, async_add_entities, _create)


class ActiveUplinkSensor(NetworkPlusEntity, SensorEntity):
    """The uplink that carries the traffic, by its network group (wan, wan2, …)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "active_uplink"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "active_uplink")

    @property
    def options(self) -> list[str]:
        """Return the uplinks the gateway reports."""
        return [key.lower() for key in self.coordinator.data.uplinks]

    @property
    def native_value(self) -> str | None:
        """Return the active uplink."""
        active = self.coordinator.data.active
        return active.lower() if active else None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return the active uplink's name as set in UniFi Network."""
        data = self.coordinator.data
        uplink = data.uplinks.get(data.active) if data.active else None
        return {"uplink_name": uplink.name if uplink else None}


class LastTransitionSensor(NetworkPlusEntity, SensorEntity):
    """When the gateway last switched to or from the LTE backup."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_transition"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "last_transition")

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last switch."""
        return self.coordinator.data.last_transition


class UplinkLatencySensor(UplinkEntity, SensorEntity):
    """Average latency of the gateway's monitors on the uplink."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "uplink_latency"

    def __init__(self, entry: NetworkConfigEntry, uplink: Uplink) -> None:
        """Initialize the sensor."""
        super().__init__(entry, uplink, "latency")

    @property
    def native_value(self) -> int | None:
        """Return the latency."""
        uplink = self.uplink
        return uplink.latency if uplink else None


class UplinkAvailabilitySensor(UplinkEntity, SensorEntity):
    """Share of the gateway's monitoring period in which the uplink was available."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_translation_key = "uplink_availability"

    def __init__(self, entry: NetworkConfigEntry, uplink: Uplink) -> None:
        """Initialize the sensor."""
        super().__init__(entry, uplink, "availability")

    @property
    def native_value(self) -> float | None:
        """Return the availability."""
        uplink = self.uplink
        return uplink.availability if uplink else None
