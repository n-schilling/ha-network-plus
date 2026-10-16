"""Tests for the buttons "Run speed test" and "Run speed test over backup uplink"."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from custom_components.network_plus.api import (
    NetworkAuthError,
    NetworkError,
    NetworkPermissionError,
)
from custom_components.network_plus.const import CONF_DRILL, DOMAIN, ISSUE_ADMIN

from .conftest import MAC, SPEEDTEST_RUN, TRANSITION, FakeConsole, gateway, lte_device, sync

BUTTON = "button.gateway_run_speed_test"
BACKUP_BUTTON = "button.gateway_run_speed_test_over_backup_uplink"
SWITCH = "switch.gateway_enforce_backup_uplink"


async def _press(hass: HomeAssistant, entity_id: str = BUTTON) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


def _in_progress(hass: HomeAssistant) -> bool:
    return bool(hass.states.get(BACKUP_BUTTON).attributes["in_progress"])  # type: ignore[union-attr]


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def _task(entry: MockConfigEntry) -> asyncio.Task[None]:
    task = entry.runtime_data.coordinator.backup_speedtest
    assert task is not None
    return task


async def _settle(hass: HomeAssistant) -> None:
    """Let the background task run until it waits for the gateway."""
    for _ in range(20):
        await asyncio.sleep(0)
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
    """Without the admin opt-in there are no buttons, old ones are removed, and nothing starts."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for key in ("run_speedtest", "run_backup_speedtest"):
        registry.async_get_or_create("button", DOMAIN, f"{MAC}_{key}", config_entry=config_entry)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BUTTON) is None
    assert hass.states.get(BACKUP_BUTTON) is None
    for key in ("run_speedtest", "run_backup_speedtest"):
        assert registry.async_get_entity_id("button", DOMAIN, f"{MAC}_{key}") is None
    coordinator = config_entry.runtime_data.coordinator
    with pytest.raises(HomeAssistantError, match="admin"):
        await coordinator.async_start_speedtest()
    with pytest.raises(HomeAssistantError, match="admin"):
        await coordinator.async_start_backup_speedtest()
    assert console.speedtests == []


async def test_backup_speedtest_runs_the_whole_sequence(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Enforce, wait for the failover, test, wait for the result, restore; the drill is recorded."""
    assert _in_progress(hass) is False
    await _press(hass, BACKUP_BUTTON)
    task = _task(setup_admin_entry)
    await _settle(hass)
    assert _in_progress(hass) is True
    assert console.writes == [("net-wan", "disabled")]
    assert _state(hass, SWITCH) == STATE_ON
    assert console.speedtests == []
    # The gateway fails over: the test starts
    freezer.tick(timedelta(seconds=15))
    failed_over = gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000)
    console.set_gateway(failed_over)
    await console.push(hass, sync(failed_over))
    await _settle(hass)
    assert console.speedtests == [MAC]
    assert console.writes == [("net-wan", "disabled")]
    # The result arrives over the backup: the preferred uplink comes back
    finished = gateway(
        wan="offline",
        active="WAN_LTE_FAILOVER",
        transition=TRANSITION + 1000,
        interface="gre1",
        download=40.0,
        speedtest_run=SPEEDTEST_RUN + 60,
    )
    console.set_gateway(finished)
    await console.push(hass, sync(finished))
    await task
    await hass.async_block_till_done()
    assert console.writes == [("net-wan", "disabled"), ("net-wan", "dhcp")]
    assert _state(hass, SWITCH) == STATE_OFF
    assert _in_progress(hass) is False
    download = hass.states.get("sensor.gateway_speed_test_download")
    assert (download.state, download.attributes["uplink"]) == ("40.0", "wan_lte_failover")  # type: ignore[union-attr]
    drill = setup_admin_entry.data[CONF_DRILL]
    assert drill["failover_at"] is not None and drill["ended_at"] is not None
    # Pressing again starts a new one
    back = gateway(interface="gre1", download=40.0, speedtest_run=SPEEDTEST_RUN + 60)
    console.set_gateway(back)
    await console.push(hass, sync(back))
    await _press(hass, BACKUP_BUTTON)
    assert _in_progress(hass) is True
    assert console.writes[-1] == ("net-wan", "disabled")


async def test_backup_speedtest_refusals(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """The button refuses what the switch refuses, a running test and a second measurement."""
    await console.push(hass, sync(gateway(wan="offline", active="WAN_LTE_FAILOVER")))
    with pytest.raises(ServiceValidationError, match="already"):
        await _press(hass, BACKUP_BUTTON)
    await console.push(hass, sync(gateway(speedtest_summary=1)))
    with pytest.raises(ServiceValidationError, match="already running"):
        await _press(hass, BACKUP_BUTTON)
    await console.push(hass, sync(gateway()))
    console.devices[2] = lte_device(rx=6_500_000_000, tx=0)  # 81 % of 8 GB
    await _poll(hass, setup_admin_entry)
    with pytest.raises(ServiceValidationError, match="81 %"):
        await _press(hass, BACKUP_BUTTON)
    assert console.writes == []
    console.devices[2] = lte_device()
    await _poll(hass, setup_admin_entry)
    with patch("custom_components.network_plus.coordinator.FAILOVER_TIMEOUT", 0.5):
        await _press(hass, BACKUP_BUTTON)
        task = _task(setup_admin_entry)
        with pytest.raises(ServiceValidationError, match="already in progress"):
            await _press(hass, BACKUP_BUTTON)
        assert console.writes == [("net-wan", "disabled")]
        # Let the measurement end without a failover
        await task
    assert console.writes[-1] == ("net-wan", "dhcp")


async def test_backup_speedtest_without_failover(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the gateway does not switch in time, the uplink comes back and no test runs."""
    with patch("custom_components.network_plus.coordinator.FAILOVER_TIMEOUT", 0.01):
        await _press(hass, BACKUP_BUTTON)
        task = _task(setup_admin_entry)
        await task
    await hass.async_block_till_done()
    assert console.speedtests == []
    assert console.writes == [("net-wan", "disabled"), ("net-wan", "dhcp")]
    assert _in_progress(hass) is False
    assert "did not switch to the backup in time" in caplog.text


async def test_backup_speedtest_without_result(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When no result arrives in time, the uplink comes back anyway."""
    with patch("custom_components.network_plus.coordinator.RESULT_TIMEOUT", 0.01):
        await _press(hass, BACKUP_BUTTON)
        task = _task(setup_admin_entry)
        failed_over = gateway(
            wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000
        )
        console.set_gateway(failed_over)
        await console.push(hass, sync(failed_over))
        await task
    assert console.speedtests == [MAC]
    assert console.writes == [("net-wan", "disabled"), ("net-wan", "dhcp")]
    assert "no result in time" in caplog.text


async def test_backup_speedtest_restore_fails(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refused switch-back is logged; the timer brings the uplink back later."""
    caplog.set_level(logging.WARNING)
    await _press(hass, BACKUP_BUTTON)
    task = _task(setup_admin_entry)
    failed_over = gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000)
    console.set_gateway(failed_over)
    await console.push(hass, sync(failed_over))
    await _settle(hass)
    assert console.speedtests == [MAC]
    console.write_error = NetworkError("busy")
    finished = gateway(
        wan="offline",
        active="WAN_LTE_FAILOVER",
        transition=TRANSITION + 1000,
        speedtest_run=SPEEDTEST_RUN + 60,
    )
    console.set_gateway(finished)
    await console.push(hass, sync(finished))
    await task
    assert console.writes == [("net-wan", "disabled")]
    assert "did not take the change: busy" in caplog.text
    assert _state(hass, SWITCH) == STATE_ON
    console.write_error = None
    freezer.tick(timedelta(minutes=15, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert console.writes[-1] == ("net-wan", "dhcp")
    assert _state(hass, SWITCH) == STATE_OFF


async def test_backup_button_unavailable_without_configuration(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Without the uplink in the console's configuration the button is unavailable."""
    del console.configs[0]["_id"]
    await _poll(hass, setup_admin_entry)
    assert _state(hass, BACKUP_BUTTON) == STATE_UNAVAILABLE
    raw = gateway()
    raw["last_wan_status"] = {}
    await console.push(hass, sync(raw))
    with pytest.raises(HomeAssistantError, match="not found"):
        await setup_admin_entry.runtime_data.coordinator.async_start_backup_speedtest()


async def test_wait_returns_at_once_when_already_true(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry
) -> None:
    """A condition that already holds needs no state change."""
    coordinator = setup_admin_entry.runtime_data.coordinator
    assert await coordinator._async_wait(lambda _s: True, 1) is True
