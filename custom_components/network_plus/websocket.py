"""The WebSocket command of the page: the consoles, their uplinks now and their history.

For administrators only: the page shows public addresses and the names of
remote clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import probatio

from homeassistant.components.websocket_api import async_register_command
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import require_admin, websocket_command
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .coordinator import NetworkConfigEntry
from .entity import expected_speed
from .model import LteBackup, NetworkState, Uplink

# The version of the integration, as manifest.json has it; the page shows it at the bottom
DATA_VERSION: HassKey[str] = HassKey(f"{DOMAIN}_version")


async def async_register_websocket(hass: HomeAssistant) -> None:
    """Register the command."""
    hass.data[DATA_VERSION] = str((await async_get_integration(hass, DOMAIN)).version)
    async_register_command(hass, websocket_overview)


@require_admin
@websocket_command(
    {
        probatio.Required("type"): "network_plus/overview",
        probatio.Optional("entry_id"): str,
    }
)
@callback
def websocket_overview(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the consoles, and for the one asked for (else the first) its state and history."""
    entries: list[NetworkConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    consoles = [
        {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "loaded": entry.state is ConfigEntryState.LOADED,
        }
        for entry in entries
    ]
    wanted = msg.get("entry_id")
    entry = next(
        (e for e in entries if e.entry_id == wanted) if wanted else iter(entries),
        None,
    )
    if wanted and entry is None:
        connection.send_error(msg["id"], "not_found", "No such console")
        return
    console: dict[str, Any] | None = None
    if entry is not None and entry.state is ConfigEntryState.LOADED:
        runtime = entry.runtime_data
        console = {
            "entry_id": entry.entry_id,
            "title": entry.title,
            **state_dict(entry, runtime.coordinator.data),
            "admin": runtime.admin_client is not None,
            "backup_enforced": runtime.coordinator.backup_enforced,
            "history": runtime.history.as_dict(),
        }
    connection.send_result(
        msg["id"],
        {"consoles": consoles, "console": console, "version": hass.data.get(DATA_VERSION)},
    )


def state_dict(entry: NetworkConfigEntry, state: NetworkState) -> dict[str, Any]:
    """Return the gateway's state as the page shows it."""
    speedtest = state.speedtest
    return {
        "gateway": {"model": state.gateway.model, "version": state.gateway.version},
        "active": state.active,
        "primary": state.primary,
        "latency": state.latency,
        "uplinks": [_uplink(entry, uplink) for uplink in state.uplinks.values()],
        "lte": [_lte(lte) for lte in (state.lte or {}).values()],
        "speedtest_running": speedtest.running if speedtest else None,
        "remote": None
        if state.vpn_clients is None
        else [{"name": c.name, "type": c.type, "since": _iso(c.since)} for c in state.vpn_clients],
    }


def _uplink(entry: NetworkConfigEntry, uplink: Uplink) -> dict[str, Any]:
    return {
        "key": uplink.key,
        "name": uplink.name,
        "priority": uplink.priority,
        "online": uplink.online,
        "latency": uplink.latency,
        "availability": uplink.availability,
        "ipv4": uplink.ipv4,
        "ipv6": uplink.ipv6,
        "wan_type": uplink.wan_type,
        "port": uplink.port,
        "link_speed": uplink.link_speed,
        "full_duplex": uplink.full_duplex,
        "expected_speed": expected_speed(entry, uplink.key),
    }


def _lte(lte: LteBackup) -> dict[str, Any]:
    return {
        "name": lte.name,
        "model": lte.model,
        "connected": lte.connected,
        "in_use": lte.in_use,
        "state": lte.state,
        "used_bytes": lte.plan_bytes,
        "used_percent": lte.usage_percent,
        "limit_bytes": lte.hard_limit if lte.limit_enabled else None,
        "warning_bytes": lte.soft_limit if lte.limit_enabled else None,
        "period_start": _iso(lte.period_start),
        "rssi": lte.rssi,
        "rsrp": lte.rsrp,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
