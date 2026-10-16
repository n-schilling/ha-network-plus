"""Button "Run speed test": start a speed test on the gateway.

Only with the admin opt-in, like the switch. The test runs over the uplink
that carries the traffic; combined with the switch "Enforce backup uplink"
that is a way to measure the backup.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import NetworkPlusEntity, remove_stale_entities

# One button per entry, and its writes must not overlap
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the button, only when admin credentials were given."""
    if entry.runtime_data.admin_client is not None:
        async_add_entities([SpeedtestButton(entry)])
        return
    # The opt-in was withdrawn: a button from before must not linger
    mac = entry.runtime_data.coordinator.data.gateway.mac
    remove_stale_entities(hass, Platform.BUTTON, [f"{mac}_run_speedtest"])


class SpeedtestButton(NetworkPlusEntity, ButtonEntity):
    """Starts a speed test on the gateway; the sensors show the result when it is done."""

    _attr_translation_key = "run_speedtest"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the button."""
        super().__init__(entry, "run_speedtest")

    async def async_press(self) -> None:
        """Start the speed test."""
        await self.coordinator.async_start_speedtest()
