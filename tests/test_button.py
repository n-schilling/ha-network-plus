"""Tests for the button "Run speed test"."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from custom_components.network_plus.api import (
    NetworkAuthError,
    NetworkError,
    NetworkPermissionError,
)
from custom_components.network_plus.const import DOMAIN, ISSUE_ADMIN

from .conftest import MAC, FakeConsole, gateway, sync

BUTTON = "button.gateway_run_speed_test"


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: BUTTON}, blocking=True
    )
    await hass.async_block_till_done()


async def test_press_starts_a_speedtest(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Pressing sends the command for the gateway with the admin credentials."""
    assert hass.states.get(BUTTON) is not None
    await _press(hass)
    assert console.speedtests == [MAC]
    assert ("192.0.2.1", "netadmin", "admin-secret") in console.created_with


async def test_press_refused_while_a_test_runs(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """While the gateway reports a running test, the button does not start another."""
    await console.push(hass, sync(gateway(speedtest_summary=1)))
    with pytest.raises(ServiceValidationError, match="already running"):
        await _press(hass)
    assert console.speedtests == []
    await console.push(hass, sync(gateway(speedtest_summary=2)))
    await _press(hass)
    assert console.speedtests == [MAC]


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (NetworkAuthError("no"), "rejected the admin credentials"),
        (NetworkPermissionError("403"), "may not change"),
        (NetworkError("busy"), "did not start the speed test: busy"),
    ],
)
async def test_press_errors(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    error: Exception,
    match: str,
) -> None:
    """Errors of the console come back as readable errors; missing rights raise the issue."""
    console.write_error = error
    with pytest.raises(HomeAssistantError, match=match):
        await _press(hass)
    assert console.speedtests == []
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_ADMIN}_{setup_admin_entry.entry_id}"
    )
    assert (issue is not None) is isinstance(error, NetworkPermissionError)


async def test_no_button_without_admin(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """Without the admin opt-in there is no button, an old one is removed, and nothing starts."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN, f"{MAC}_run_speedtest", config_entry=config_entry
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BUTTON) is None
    assert registry.async_get_entity_id("button", DOMAIN, f"{MAC}_run_speedtest") is None
    with pytest.raises(HomeAssistantError, match="admin"):
        await config_entry.runtime_data.coordinator.async_start_speedtest()
    assert console.speedtests == []
