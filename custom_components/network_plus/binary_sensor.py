"""Binary sensors: is an uplink online, is the backup in use, is a link slower than it should be."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import (
    LteEntity,
    NetworkPlusEntity,
    UplinkEntity,
    add_dynamic_entities,
    expected_speed,
    remove_stale_entities,
)
from .model import LteBackup, NetworkState, Uplink

# Read-only entities of a coordinator: no limit needed
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors."""
    async_add_entities([BackupActiveSensor(entry)])

    def _uplink(uplink: Uplink) -> list[BinarySensorEntity]:
        return [UplinkOnlineSensor(entry, uplink)]

    def _checked(uplink: Uplink) -> list[BinarySensorEntity]:
        return [UplinkLinkDegradedSensor(entry, uplink)]

    def _with_expectation(state: NetworkState) -> dict[str, Uplink]:
        return {
            key: uplink
            for key, uplink in state.uplinks.items()
            if uplink.port and expected_speed(entry, key) is not None
        }

    def _lte(lte: LteBackup) -> list[BinarySensorEntity]:
        return [LteSimProblemSensor(entry, lte)]

    add_dynamic_entities(entry, async_add_entities, lambda state: state.uplinks, _uplink)
    add_dynamic_entities(entry, async_add_entities, _with_expectation, _checked)
    add_dynamic_entities(entry, async_add_entities, lambda state: state.lte or {}, _lte)
    # An expectation that was removed takes its sensor along
    state = entry.runtime_data.coordinator.data
    remove_stale_entities(
        hass,
        Platform.BINARY_SENSOR,
        [
            f"{state.gateway.mac}_link_degraded_{key.lower()}"
            for key, uplink in state.uplinks.items()
            if uplink.port and expected_speed(entry, key) is None
        ],
    )


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


class UplinkLinkDegradedSensor(UplinkEntity, BinarySensorEntity):
    """On while the uplink's port negotiated less than the configured link speed, or half duplex.

    Exists only for uplinks with an expected link speed in the options. A
    link that is down is not slow: the sensor is unknown then, the uplink's
    own sensor says offline.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "link_degraded"

    def __init__(self, entry: NetworkConfigEntry, uplink: Uplink) -> None:
        """Initialize the sensor."""
        super().__init__(entry, uplink, "link_degraded")
        self._expected = expected_speed(entry, uplink.key)

    @property
    def is_on(self) -> bool | None:
        """Return whether the link is slower than expected."""
        uplink = self.uplink
        if uplink is None or uplink.link_speed is None or self._expected is None:
            return None
        return uplink.link_speed < self._expected or uplink.full_duplex is False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the negotiated and the expected speed."""
        uplink = self.uplink
        return {
            "link_speed": uplink.link_speed if uplink else None,
            "expected_speed": self._expected,
            "full_duplex": uplink.full_duplex if uplink else None,
        }


class LteSimProblemSensor(LteEntity, BinarySensorEntity):
    """On while the SIM keeps the LTE device from connecting (PIN not verified, PUK needed)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "sim_problem"

    def __init__(self, entry: NetworkConfigEntry, lte: LteBackup) -> None:
        """Initialize the sensor."""
        super().__init__(entry, lte, "sim_problem")

    @property
    def is_on(self) -> bool | None:
        """Return whether the SIM is the problem; None while the console does not say."""
        lte = self.lte
        return lte.sim_problem if lte else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the device's state and the PIN tries left."""
        lte = self.lte
        return {
            "state": lte.state if lte else None,
            "pin_tries_left": lte.sim_pin_tries_left if lte else None,
        }
