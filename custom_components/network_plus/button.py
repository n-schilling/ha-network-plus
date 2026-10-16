"""Buttons "Run speed test" and "Run speed test over backup uplink".

Only with the admin opt-in, like the switch. A speed test runs over the
uplink that carries the traffic; the second button switches the preferred
uplink off first, waits for the failover, runs the test, waits for the
result and switches the uplink on again, whatever happened in between.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import NetworkPlusEntity, remove_stale_entities

BUTTON_KEYS = ("run_speedtest", "run_backup_speedtest")

# Two buttons per entry, and their writes must not overlap
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons, only when admin credentials were given."""
    if entry.runtime_data.admin_client is not None:
        async_add_entities([SpeedtestButton(entry), BackupSpeedtestButton(entry)])
        return
    # The opt-in was withdrawn: buttons from before must not linger
    mac = entry.runtime_data.coordinator.data.gateway.mac
    remove_stale_entities(hass, Platform.BUTTON, [f"{mac}_{key}" for key in BUTTON_KEYS])


class SpeedtestButton(NetworkPlusEntity, ButtonEntity):
    """Starts a speed test on the gateway; the sensors show the result when it is done."""

    _attr_translation_key = "run_speedtest"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the button."""
        super().__init__(entry, "run_speedtest")

    async def async_press(self) -> None:
        """Start the speed test."""
        await self.coordinator.async_start_speedtest()


class BackupSpeedtestButton(NetworkPlusEntity, ButtonEntity):
    """Measures the backup uplink: enforce it, run the test, restore the preferred uplink.

    The press starts the sequence and returns at once; the attribute
    ``in_progress`` and the switch show what is going on, the speed test
    sensors carry the result with the backup as ``uplink``.
    """

    _attr_translation_key = "run_backup_speedtest"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the button."""
        super().__init__(entry, "run_backup_speedtest")

    @property
    def available(self) -> bool:
        """Return whether the preferred uplink is known in the console's configuration."""
        primary = self.coordinator.primary_uplink
        return super().available and primary is not None and primary.config_id is not None

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return whether a measurement of the backup uplink is going on."""
        return {"in_progress": self.coordinator.backup_speedtest is not None}

    async def async_press(self) -> None:
        """Start the measurement, unless that would be unwise."""
        await self.coordinator.async_start_backup_speedtest()
