"""Diagnostics for Network Plus.

People attach diagnostics to issues, so they hold only what helps to find a
problem: no address (neither the console's nor a public one), no user,
password or MAC, no uplink or client names (they can name the provider or a
person) and no times of failovers.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import NetworkConfigEntry

UPLINK_FIELDS = ("key", "priority", "online", "latency", "availability")
LTE_FIELDS = (
    "connected",
    "in_use",
    "usage_bytes",
    "soft_limit",
    "hard_limit",
    "limit_enabled",
    "rssi",
    "rsrp",
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NetworkConfigEntry
) -> dict[str, Any]:
    """Return what helps to understand a problem, and nothing personal."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    speedtest = asdict(data.speedtest) if data.speedtest else None
    if speedtest:
        speedtest["last_run"] = speedtest["last_run"] is not None
    lte = asdict(data.lte) if data.lte else None
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}),
        "websocket_connected": coordinator.websocket_connected,
        "last_update_success": coordinator.last_update_success,
        "gateway": {"model": data.gateway.model, "version": data.gateway.version},
        "uplinks": [
            {
                **{field: getattr(u, field) for field in UPLINK_FIELDS},
                "has_ipv4": u.ipv4 is not None,
                "has_ipv6": u.ipv6 is not None,
            }
            for u in data.uplinks.values()
        ],
        "active": data.active,
        "backup_active": data.backup_active,
        "latency": data.latency,
        "has_last_transition": data.last_transition is not None,
        "speedtest": speedtest,
        "lte": {
            "model": data.lte.model,
            "version": data.lte.version,
            **{field: lte[field] for field in LTE_FIELDS},
        }
        if data.lte and lte
        else None,
        "vpn_clients": len(data.vpn_clients) if data.vpn_clients is not None else None,
    }
