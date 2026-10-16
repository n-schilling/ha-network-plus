"""Tests for the diagnostics."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.network_plus.api import NetworkError
from custom_components.network_plus.diagnostics import async_get_config_entry_diagnostics

from .conftest import HOST, IPV4, IPV6, LTE_MAC, MAC, FakeConsole


async def test_diagnostics_hold_nothing_personal(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """No address, user, password, MAC, uplink name or time ends up in the diagnostics."""
    result = await async_get_config_entry_diagnostics(hass, setup_entry)
    text = json.dumps(result)
    secrets = (HOST, MAC, LTE_MAC, IPV4, IPV6, "homeassistant", "secret", "Cable", "2023")
    for secret in (*secrets, "9876", "123456789012345", "Carrier", "LTE backup", "Phone"):
        assert secret not in text
    assert result["gateway"] == {"model": "UDMPRO", "version": "4.0.0"}
    assert [u["key"] for u in result["uplinks"]] == ["WAN", "WAN_LTE_FAILOVER"]
    assert result["uplinks"][0]["has_ipv4"] is True
    assert result["uplinks"][1]["has_ipv6"] is False
    assert result["active"] == "WAN"
    assert result["backup_active"] is False
    assert result["latency"] == 14
    assert result["has_last_transition"] is True
    assert result["speedtest"] == {
        "download": 949.0,
        "upload": 54.0,
        "ping": 18,
        "last_run": True,
        "status": "Success",
        "uplink": "WAN",
        "running": False,
    }
    assert result["lte"] == [
        {
            "model": "ULTEPRO",
            "version": "6.0.0",
            "connected": True,
            "in_use": False,
            "usage_bytes": 1_500_000,
            "soft_limit": 6_000_000_000,
            "hard_limit": 8_000_000_000,
            "limit_enabled": True,
            "rssi": -77,
            "rsrp": -112,
            "state": "ready",
            "sim_pin_required": True,
            "sim_pin_verified": True,
            "sim_pin_tries_left": 3,
        }
    ]
    assert result["vpn_clients"] == 1
    wan = result["uplinks"][0]
    assert (wan["port"], wan["link_speed"], wan["full_duplex"]) == ("wan1", 2500, True)
    assert result["uplinks"][1]["port"] is None
    assert result["drill"] is None


async def test_diagnostics_without_lte_and_clients(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """A console without LTE device or readable clients gives None for both."""
    console.devices = console.devices[:2]
    console.clients_error = NetworkError("no such endpoint")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["lte"] == []
    assert result["vpn_clients"] is None
