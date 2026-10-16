"""Diagnostics for Network Plus.

People attach diagnostics to issues, so they hold only what helps to find a
problem: no address, user, password or MAC, no uplink names (they can name the
provider) and no times of failovers.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import NetworkConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NetworkConfigEntry
) -> dict[str, Any]:
    """Return what helps to understand a problem, and nothing personal."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}),
        "websocket_connected": coordinator.websocket_connected,
        "last_update_success": coordinator.last_update_success,
        "gateway": {"model": data.gateway.model, "version": data.gateway.version},
        "uplinks": [
            {
                "key": u.key,
                "priority": u.priority,
                "online": u.online,
                "latency": u.latency,
                "availability": u.availability,
            }
            for u in data.uplinks.values()
        ],
        "active": data.active,
        "backup_active": data.backup_active,
        "has_last_transition": data.last_transition is not None,
    }
