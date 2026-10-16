"""Tests for setup, websocket, polling and the entities."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.network_plus.api import NetworkAuthError, NetworkError
from custom_components.network_plus.const import DOMAIN

from .conftest import CLOSE, MAC, TRANSITION, FakeConsole, gateway, sync

WAN = "binary_sensor.gateway_cable"
LTE = "binary_sensor.gateway_lte"
BACKUP = "binary_sensor.gateway_backup_uplink_active"
ACTIVE = "sensor.gateway_active_uplink"
LAST = "sensor.gateway_last_lte_failover"
EVENT = "event.gateway_internet"


async def test_entities_after_setup(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Every monitored uplink gets its entities; the device carries the MAC."""
    assert hass.states.get(WAN).state == "on"  # type: ignore[union-attr]
    assert hass.states.get(LTE).state == "on"  # type: ignore[union-attr]
    assert hass.states.get(BACKUP).state == "off"  # type: ignore[union-attr]
    active = hass.states.get(ACTIVE)
    assert active.state == "wan"  # type: ignore[union-attr]
    assert active.attributes["uplink_name"] == "Cable"  # type: ignore[union-attr]
    assert active.attributes["options"] == ["wan", "wan_lte_failover"]  # type: ignore[union-attr]
    assert hass.states.get(LAST).state == "2023-11-14T22:13:20+00:00"  # type: ignore[union-attr]
    assert hass.states.get(EVENT).state == STATE_UNKNOWN  # type: ignore[union-attr]
    # Latency and availability are there, but disabled by default
    registry = er.async_get(hass)
    latency = registry.async_get("sensor.gateway_cable_latency")
    assert latency is not None and latency.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get("sensor.gateway_lte_availability") is not None
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, MAC), setup_entry.entry_id)
    assert device is not None
    assert (dr.CONNECTION_NETWORK_MAC, MAC) in device.connections
    assert (device.manufacturer, device.model, device.sw_version) == (
        "Ubiquiti",
        "UDMPROSE",
        "5.1.33",
    )


async def test_enabled_diagnostic_sensors(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """Latency and availability show the gateway's values once enabled."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for key in ("latency_wan", "availability_wan_lte_failover"):
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{MAC}_{key}", config_entry=config_entry, disabled_by=None
        )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    states = {
        key: hass.states.get(str(registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_{key}")))
        for key in ("latency_wan", "availability_wan_lte_failover")
    }
    assert states["latency_wan"].state == "14"  # type: ignore[union-attr]
    assert states["availability_wan_lte_failover"].state == "99.5"  # type: ignore[union-attr]


async def test_websocket_failover(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A failover pushed by the websocket shows at once and fires events."""
    await console.push(
        hass,
        sync(gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000)),
    )
    assert hass.states.get(WAN).state == "off"  # type: ignore[union-attr]
    assert hass.states.get(BACKUP).state == "on"  # type: ignore[union-attr]
    assert hass.states.get(ACTIVE).state == "wan_lte_failover"  # type: ignore[union-attr]
    event = hass.states.get(EVENT)
    assert event.attributes["event_type"] == "failover"  # type: ignore[union-attr]
    assert event.attributes["to_uplink_name"] == "LTE"  # type: ignore[union-attr]

    await console.push(hass, sync(gateway(transition=TRANSITION + 2000), "device:update"))
    assert hass.states.get(BACKUP).state == "off"  # type: ignore[union-attr]
    assert hass.states.get(EVENT).attributes["event_type"] == "restored"  # type: ignore[union-attr]


async def test_websocket_ignores_other_messages(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Other message types, other devices and partial objects change nothing."""
    coordinator = setup_entry.runtime_data.coordinator
    before = coordinator.data
    offline = gateway(wan="offline")
    await console.push(hass, {"meta": {"message": "client:sync"}, "data": [offline]})
    await console.push(hass, {"meta": "x"})
    await console.push(hass, {"meta": {"message": "device:sync"}, "data": "x"})
    await console.push(hass, sync({"mac": "02:00:00:00:00:02", "last_wan_status": {}}))
    await console.push(hass, sync({"mac": MAC, "uplink": {}}))
    await console.push(hass, {"meta": {"message": "device:sync"}, "data": ["junk"]})
    await console.push(hass, sync(gateway()))
    assert coordinator.data == before
    assert coordinator.websocket_connected is True
    assert hass.states.get(WAN).state == "on"  # type: ignore[union-attr]


async def test_brief_failover_seen_by_polling(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failover that ended between two polls fires a brief failover."""
    console.set_gateway(gateway(transition=TRANSITION + 5000))
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(EVENT).attributes["event_type"] == "brief_failover"  # type: ignore[union-attr]


async def test_poll_failure_and_recovery(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without answer the entities are unavailable, not offline; the websocket brings them back."""
    console.error = NetworkError("down")
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(WAN).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    assert hass.states.get(BACKUP).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    await console.push(hass, sync(gateway()))
    assert hass.states.get(WAN).state == "on"  # type: ignore[union-attr]


async def test_gateway_missing(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A console that no longer reports the gateway makes the entities unavailable."""
    console.devices = []
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ACTIVE).state == STATE_UNAVAILABLE  # type: ignore[union-attr]


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A rejected login asks for the password."""
    console.error = NetworkAuthError("no")
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_setup_retry(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """An unreachable console is retried later."""
    console.error = NetworkError("down")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_uplink_appears_and_disappears(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A new uplink gets entities; a vanished one becomes unavailable."""
    raw = gateway()
    raw["last_wan_status"]["WAN2"] = "online"
    await console.push(hass, sync(raw))
    assert hass.states.get("binary_sensor.gateway_internet_2").state == "on"  # type: ignore[union-attr]
    assert "wan2" in hass.states.get(ACTIVE).attributes["options"]  # type: ignore[union-attr]

    await console.push(hass, sync(gateway()))
    assert hass.states.get("binary_sensor.gateway_internet_2").state == STATE_UNAVAILABLE  # type: ignore[union-attr]


async def test_unknown_active_uplink(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Without an active uplink the sensors are unknown."""
    await console.push(hass, sync(gateway(wan="offline", lte="offline", active="none")))
    assert hass.states.get(ACTIVE).state == STATE_UNKNOWN  # type: ignore[union-attr]
    assert hass.states.get(ACTIVE).attributes["uplink_name"] is None  # type: ignore[union-attr]
    assert hass.states.get(BACKUP).state == STATE_UNKNOWN  # type: ignore[union-attr]


async def test_websocket_reconnects(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A closed or refused websocket is connected again with growing delays."""
    coordinator = setup_entry.runtime_data.coordinator
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args: Any) -> None:
        delays.append(delay)
        await real_sleep(0)

    with patch("custom_components.network_plus.coordinator.asyncio.sleep", fake_sleep):
        console.listen_error = NetworkError("refused")
        console.messages.put_nowait(CLOSE)
        for _ in range(20):
            await real_sleep(0)
        assert coordinator.websocket_connected is False
        console.listen_error = None
        await console.listening.wait()
        console.messages.put_nowait(CLOSE)
        await real_sleep(0)
        await console.listening.wait()
    assert delays[0] == 5.0
    assert delays[1:3] == [10.0, 20.0]
    assert max(delays) <= 300.0
    assert console.listen_calls >= 3


async def test_unload(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Unloading stops the websocket and the entities."""
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(WAN).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
