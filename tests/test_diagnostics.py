"""Tests for the diagnostics."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.network_plus.diagnostics import async_get_config_entry_diagnostics

from .conftest import HOST, MAC


async def test_diagnostics_hold_nothing_personal(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """No address, user, password, MAC, uplink name or time ends up in the diagnostics."""
    result = await async_get_config_entry_diagnostics(hass, setup_entry)
    text = json.dumps(result)
    for secret in (HOST, MAC, "homeassistant", "secret", "Cable", "2023"):
        assert secret not in text
    assert result["gateway"] == {"model": "UDMPROSE", "version": "5.1.33"}
    assert [u["key"] for u in result["uplinks"]] == ["WAN", "WAN_LTE_FAILOVER"]
    assert result["active"] == "WAN"
    assert result["backup_active"] is False
    assert result["has_last_transition"] is True
