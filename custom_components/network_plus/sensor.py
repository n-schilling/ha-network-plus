"""Sensors of the gateway, its uplinks and the LTE backup."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import (
    LteEntity,
    NetworkPlusEntity,
    UplinkEntity,
    add_lte_entities,
    add_uplink_entities,
)
from .model import LteBackup, Uplink

# Read-only entities of a coordinator: no limit needed
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    async_add_entities(
        [
            ActiveUplinkSensor(entry),
            InternetLatencySensor(entry),
            LastTransitionSensor(entry),
            SpeedtestSensor(entry, "download"),
            SpeedtestSensor(entry, "upload"),
            SpeedtestSensor(entry, "ping"),
            SpeedtestLastRunSensor(entry),
            VpnClientsSensor(entry),
        ]
    )

    def _uplink(uplink: Uplink) -> list[SensorEntity]:
        return [
            UplinkLatencySensor(entry, uplink),
            UplinkAvailabilitySensor(entry, uplink),
            UplinkAddressSensor(entry, uplink, "ipv4"),
            UplinkAddressSensor(entry, uplink, "ipv6"),
        ]

    def _lte(lte: LteBackup) -> list[SensorEntity]:
        return [
            LteUsagePercentSensor(entry, lte),
            LteUsageSensor(entry, lte),
            LteLimitSensor(entry, lte),
            LteSignalSensor(entry, lte),
        ]

    add_uplink_entities(entry, async_add_entities, _uplink)
    add_lte_entities(entry, async_add_entities, _lte)


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


class InternetLatencySensor(NetworkPlusEntity, SensorEntity):
    """Latency on the uplink that carries the traffic, sampled every minute.

    One series across failovers: a threshold helper or an automation decides
    from here when the internet is too slow, not only which uplink is active.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "internet_latency"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "internet_latency")

    @property
    def native_value(self) -> int | None:
        """Return the latency."""
        return self.coordinator.data.latency

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return the uplink the latency was measured on."""
        active = self.coordinator.data.active
        return {"uplink": active.lower() if active else None}


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


class SpeedtestSensor(NetworkPlusEntity, SensorEntity):
    """Download, upload or ping of the gateway's last speed test."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: NetworkConfigEntry, key: str) -> None:
        """Initialize the sensor for ``key``: download, upload or ping."""
        super().__init__(entry, f"speedtest_{key}")
        self._key = key
        self._attr_translation_key = f"speedtest_{key}"
        if key == "ping":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
        else:
            self._attr_device_class = SensorDeviceClass.DATA_RATE
            self._attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
            self._attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | int | None:
        """Return the value of the last speed test."""
        speedtest = self.coordinator.data.speedtest
        value: float | int | None = getattr(speedtest, self._key) if speedtest else None
        return value


class SpeedtestLastRunSensor(NetworkPlusEntity, SensorEntity):
    """When the gateway last ran a speed test."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "speedtest_last_run"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "speedtest_last_run")

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last speed test."""
        speedtest = self.coordinator.data.speedtest
        return speedtest.last_run if speedtest else None

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return the result of the last speed test as the gateway reports it."""
        speedtest = self.coordinator.data.speedtest
        return {"status": speedtest.status if speedtest else None}


class VpnClientsSensor(NetworkPlusEntity, SensorEntity):
    """How many remote clients (Teleport, VPN) are connected."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "vpn_clients"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(entry, "vpn_clients")

    @property
    def native_value(self) -> int | None:
        """Return the number of remote clients; None if the console did not tell."""
        clients = self.coordinator.data.vpn_clients
        return len(clients) if clients is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Return the names and kinds of the remote clients."""
        clients = self.coordinator.data.vpn_clients or ()
        return {
            "clients": [c.name for c in clients],
            "types": sorted({c.type.lower() for c in clients}),
        }


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


class UplinkAddressSensor(UplinkEntity, SensorEntity):
    """Public IPv4 or IPv6 address of the uplink's port.

    Disabled by default: an address in the recorder is a choice to make.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: NetworkConfigEntry, uplink: Uplink, family: str) -> None:
        """Initialize the sensor for ``family``: ipv4 or ipv6."""
        super().__init__(entry, uplink, family)
        self._family = family
        self._attr_translation_key = f"uplink_{family}"

    @property
    def native_value(self) -> str | None:
        """Return the address."""
        uplink = self.uplink
        value: str | None = getattr(uplink, self._family) if uplink else None
        return value


class LteUsagePercentSensor(LteEntity, SensorEntity):
    """How much of the LTE data plan is used this month."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_translation_key = "lte_usage_percent"

    def __init__(self, entry: NetworkConfigEntry, lte: LteBackup) -> None:
        """Initialize the sensor."""
        super().__init__(entry, lte, "usage_percent")

    @property
    def native_value(self) -> float | None:
        """Return the share; None without a data plan limit."""
        lte = self.lte
        return lte.usage_percent if lte else None


class LteUsageSensor(LteEntity, SensorEntity):
    """LTE data used this month.

    The console resets the counter at its own month boundary; with
    ``TOTAL_INCREASING`` the statistics take that drop as the reset instead
    of a guessed ``last_reset``.
    """

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 2
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_translation_key = "lte_usage"

    def __init__(self, entry: NetworkConfigEntry, lte: LteBackup) -> None:
        """Initialize the sensor."""
        super().__init__(entry, lte, "usage")

    @property
    def native_value(self) -> int | None:
        """Return the bytes used."""
        lte = self.lte
        return lte.usage_bytes if lte else None


class LteLimitSensor(LteEntity, SensorEntity):
    """The LTE data plan set in UniFi Network."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 1
    _attr_translation_key = "lte_limit"

    def __init__(self, entry: NetworkConfigEntry, lte: LteBackup) -> None:
        """Initialize the sensor."""
        super().__init__(entry, lte, "limit")

    @property
    def native_value(self) -> int | None:
        """Return the limit; None if none is set."""
        lte = self.lte
        return lte.hard_limit if lte and lte.limit_enabled else None

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        """Return the warning limit."""
        lte = self.lte
        return {"warning_bytes": lte.soft_limit if lte and lte.limit_enabled else None}


class LteSignalSensor(LteEntity, SensorEntity):
    """Signal strength of the LTE device."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "lte_signal"

    def __init__(self, entry: NetworkConfigEntry, lte: LteBackup) -> None:
        """Initialize the sensor."""
        super().__init__(entry, lte, "signal")

    @property
    def native_value(self) -> int | None:
        """Return the RSSI."""
        lte = self.lte
        return lte.rssi if lte else None

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        """Return the RSRP."""
        lte = self.lte
        return {"rsrp": lte.rsrp if lte else None}
