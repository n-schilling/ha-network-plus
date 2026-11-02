"""Sensors of the gateway, its uplinks and the LTE backup."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import LteEntity, NetworkPlusEntity, add_dynamic_entities
from .model import LteBackup

# Read-only entities of a coordinator: no limit needed
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors; the uplinks' details, speed tests and drills are on the page."""
    async_add_entities([ActiveUplinkSensor(entry), InternetLatencySensor(entry)])

    def _lte(lte: LteBackup) -> list[SensorEntity]:
        return [
            LteUsagePercentSensor(entry, lte),
            LteUsageSensor(entry, lte),
            LteLimitSensor(entry, lte),
            LteSignalSensor(entry, lte),
        ]

    add_dynamic_entities(entry, async_add_entities, lambda state: state.lte or {}, _lte)


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


class LteUsagePercentSensor(LteEntity, SensorEntity):
    """How much of the LTE data plan is used this billing period.

    With a billing day other than the 1st, the attribute ``month_percent``
    is the share the console counts for the calendar month, which its own
    warning and data limits go by.
    """

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

    @property
    def extra_state_attributes(self) -> dict[str, float | None] | None:
        """Return the console's share of the calendar month, with a billing day only."""
        if self.coordinator.lte_billing_day == 1:
            return None
        lte = self.lte
        return {"month_percent": lte.month_percent if lte else None}


class LteUsageSensor(LteEntity, SensorEntity):
    """LTE data used this billing period.

    With the billing day on the 1st this is the console's monthly counter. The
    console resets it at its own month boundary; with ``TOTAL_INCREASING`` the
    statistics take that drop as the reset instead of a guessed ``last_reset``.

    With another billing day the integration sums the period up from the
    console's reports and knows when it started: ``TOTAL`` with the period's
    start as ``last_reset``. A value that drops a little within the period
    (a report the console rolled up late) is then a correction, not a reset.
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
        self._billing_period = entry.runtime_data.coordinator.lte_billing_day != 1
        if self._billing_period:
            self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> int | None:
        """Return the bytes used."""
        lte = self.lte
        return lte.plan_bytes if lte else None

    @property
    def last_reset(self) -> datetime | None:
        """Return when the billing period started; with a billing day only."""
        lte = self.lte
        return lte.period_start if lte and self._billing_period else None

    @property
    def extra_state_attributes(self) -> dict[str, int | None] | None:
        """Return the console's monthly counter, with a billing day only."""
        if not self._billing_period:
            return None
        lte = self.lte
        return {"month_bytes": lte.usage_bytes if lte else None}


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
