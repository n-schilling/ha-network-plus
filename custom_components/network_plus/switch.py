"""Switch "Enforce backup uplink": switch the preferred uplink off so the gateway fails over.

Only with the admin opt-in. The switch guards itself: it refuses while the
backup already carries the traffic or the LTE data plan is nearly used up,
and it switches back by itself after a while.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUTO_OFF_MINUTES,
    CONF_ENFORCED_AT,
    CONF_ENFORCED_WAN_TYPE,
    CONF_LTE_LIMIT_PERCENT,
    DEFAULT_AUTO_OFF_MINUTES,
    DEFAULT_LTE_LIMIT_PERCENT,
    DOMAIN,
    WAN_TYPE_DISABLED,
)
from .coordinator import NetworkConfigEntry
from .entity import NetworkPlusEntity
from .model import Uplink

_LOGGER = logging.getLogger(__name__)

# The WAN type to restore when none was recorded (a console freshly set up)
FALLBACK_WAN_TYPE = "dhcp"

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
    registry = er.async_get(hass)
    unique_id = f"{entry.runtime_data.coordinator.data.gateway.mac}_enforce_backup"
    if entity_id := registry.async_get_entity_id(Platform.SWITCH, DOMAIN, unique_id):
        registry.async_remove(entity_id)


class EnforceBackupSwitch(NetworkPlusEntity, SwitchEntity):
    """On while the preferred uplink is switched off in the console."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "enforce_backup"

    def __init__(self, entry: NetworkConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(entry, "enforce_backup")
        self._entry = entry
        self._unsub_timer: CALLBACK_TYPE | None = None

    @property
    def _primary(self) -> Uplink | None:
        data = self.coordinator.data
        return data.uplinks.get(data.primary) if data.primary else None

    @property
    def available(self) -> bool:
        """Return whether the preferred uplink is known in the console's configuration."""
        primary = self._primary
        return super().available and primary is not None and primary.config_id is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the preferred uplink is switched off."""
        primary = self._primary
        if primary is None or primary.wan_type is None:
            return None
        return primary.wan_type == WAN_TYPE_DISABLED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the enforcing started and when it ends by itself."""
        started = self._enforced_at
        return {
            "enforced_since": started.isoformat() if started else None,
            "auto_off_at": (started + self._auto_off).isoformat() if started else None,
        }

    async def async_added_to_hass(self) -> None:
        """Arm the automatic switch-off again after a restart, or catch up on it."""
        await super().async_added_to_hass()
        if self.is_on and (started := self._enforced_at) is not None:
            remaining = started + self._auto_off - dt_util.utcnow()
            self._arm(max(remaining, timedelta(0)))

    async def async_will_remove_from_hass(self) -> None:
        """Stop the timer; the console keeps its state."""
        self._cancel()
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the preferred uplink off, unless that would be unwise."""
        data = self.coordinator.data
        primary = self._primary
        if primary is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_wan_config")
        if data.backup_active:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="backup_already_active"
            )
        limit = self._entry.options.get(CONF_LTE_LIMIT_PERCENT, DEFAULT_LTE_LIMIT_PERCENT)
        for lte in (data.lte or {}).values():
            if lte.usage_percent is not None and lte.usage_percent >= limit:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="lte_data_plan_used",
                    translation_placeholders={
                        "percent": f"{lte.usage_percent:.0f}",
                        "limit": str(limit),
                    },
                )
        previous = primary.wan_type if primary.wan_type != WAN_TYPE_DISABLED else None
        self._remember(previous or self._restore_type, dt_util.utcnow())
        await self.coordinator.async_set_wan_type(primary.key, WAN_TYPE_DISABLED)
        self._arm(self._auto_off)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the preferred uplink on again."""
        primary = self._primary
        if primary is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_wan_config")
        self._cancel()
        await self.coordinator.async_set_wan_type(primary.key, self._restore_type)
        self._remember(None, None)
        self.async_write_ha_state()

    @property
    def _auto_off(self) -> timedelta:
        return timedelta(
            minutes=self._entry.options.get(CONF_AUTO_OFF_MINUTES, DEFAULT_AUTO_OFF_MINUTES)
        )

    @property
    def _restore_type(self) -> str:
        return str(self._entry.data.get(CONF_ENFORCED_WAN_TYPE) or FALLBACK_WAN_TYPE)

    @property
    def _enforced_at(self) -> datetime | None:
        return dt_util.parse_datetime(str(self._entry.data.get(CONF_ENFORCED_AT) or ""))

    def _remember(self, wan_type: str | None, started: datetime | None) -> None:
        """Keep the WAN type to restore and the start in the entry, across restarts."""
        data = {
            k: v
            for k, v in self._entry.data.items()
            if k not in (CONF_ENFORCED_WAN_TYPE, CONF_ENFORCED_AT)
        }
        if wan_type is not None and started is not None:
            data[CONF_ENFORCED_WAN_TYPE] = wan_type
            data[CONF_ENFORCED_AT] = started.isoformat()
        self.hass.config_entries.async_update_entry(self._entry, data=data)

    @callback
    def _arm(self, delay: timedelta) -> None:
        self._cancel()
        self._unsub_timer = async_call_later(self.hass, delay, self._async_auto_off)

    @callback
    def _cancel(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _async_auto_off(self, _now: datetime) -> None:
        """Switch back after the configured time."""
        self._unsub_timer = None
        if not self.is_on:
            return
        try:
            await self.async_turn_off()
        except HomeAssistantError as err:
            _LOGGER.warning("Could not switch the preferred uplink on again: %s", err)
            self._arm(timedelta(minutes=1))
