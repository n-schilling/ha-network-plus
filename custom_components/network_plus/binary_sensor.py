"""Binary sensors: is an uplink online, is the backup in use."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import NetworkPlusEntity, UplinkEntity, add_dynamic_entities
from .model import Uplink

# Read-only entities of a coordinator: no limit needed
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors."""
    async_add_entities([BackupActiveSensor(entry)])

    def _create(uplink: Uplink) -> list[UplinkOnlineSensor]:
        return [UplinkOnlineSensor(entry, uplink)]

    add_dynamic_entities(entry, async_add_entities, lambda state: state.uplinks, _create)


class UplinkOnlineSensor(UplinkEntity, BinarySensorEntity):
    """Whether the gateway sees the uplink online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "uplink_online"

    def __init__(self, entry: NetworkConfigEntry, uplink: Uplink) -> None:
        """Initialize the sensor."""
        super().__init__(entry, uplink, "online")

    @property
    def is_on(self) -> bool | None:
        """Return whether the uplink is online; None if the gateway reports no known status."""
        uplink = self.uplink
        return uplink.online if uplink else None


class BackupActiveSensor(NetworkPlusEntity, BinarySensorEntity):
    """On while the traffic runs over another uplink than the preferred one."""

    _attr_translation_key = "backup_active"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "backup_active")

    @property
    def is_on(self) -> bool | None:
        """Return whether a backup uplink carries the traffic."""
        return self.coordinator.data.backup_active
