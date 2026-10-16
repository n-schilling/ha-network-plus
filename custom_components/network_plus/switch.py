"""Switch "Enforce backup uplink": switch the preferred uplink off so the gateway fails over.

Only with the admin opt-in. The switch guards itself: it refuses while the
backup already carries the traffic or the LTE data plan is nearly used up,
and it switches back by itself after a while. The mechanics live in the
coordinator, shared with the button "Run speed test over backup uplink".
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetworkConfigEntry
from .entity import NetworkPlusEntity, remove_stale_entities

# One switch per entry, and its writes must not overlap
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NetworkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch, only when admin credentials were given."""
    if entry.runtime_data.admin_client is not None:
        async_add_entities([EnforceBackupSwitch(entry)])
        return
    # The opt-in was withdrawn: a switch from before must not linger
    mac = entry.runtime_data.coordinator.data.gateway.mac
    remove_stale_entities(hass, Platform.SWITCH, [f"{mac}_enforce_backup"])


class EnforceBackupSwitch(NetworkPlusEntity, SwitchEntity):
    """On while the preferred uplink is switched off in the console."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "enforce_backup"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(entry, "enforce_backup")

    @property
    def available(self) -> bool:
        """Return whether the preferred uplink is known in the console's configuration."""
        primary = self.coordinator.primary_uplink
        return super().available and primary is not None and primary.config_id is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the preferred uplink is switched off."""
        return self.coordinator.backup_enforced

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the enforcing started and when it ends by itself."""
        started = self.coordinator.enforced_at
        return {
            "enforced_since": started.isoformat() if started else None,
            "auto_off_at": (started + self.coordinator.auto_off).isoformat() if started else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the preferred uplink off, unless that would be unwise."""
        await self.coordinator.async_enforce_backup()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the preferred uplink on again."""
        await self.coordinator.async_release_backup()
