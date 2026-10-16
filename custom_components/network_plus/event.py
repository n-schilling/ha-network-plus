"""Event entity "Internet": failovers and uplinks going down or up."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import NetworkPlusEntity
from .model import EVENT_TYPES, UplinkEvent

# Read-only entities of a coordinator: no limit needed
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the event entity."""
    async_add_entities([InternetEvent(entry)])


class InternetEvent(NetworkPlusEntity, EventEntity):
    """Fires when the gateway fails over, returns, or an uplink changes."""

    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "internet"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the entity."""
        super().__init__(entry, "internet")

    async def async_added_to_hass(self) -> None:
        """Listen to the coordinator's events."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_event_listener(self._handle_event))

    @callback
    def _handle_event(self, event: UplinkEvent) -> None:
        self._trigger_event(event.type, event.attributes)
        self.async_write_ha_state()
