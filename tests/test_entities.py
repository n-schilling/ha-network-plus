"""Tests for setup, websocket, polling and the entities."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.network_plus.api import NetworkAuthError, NetworkError
from custom_components.network_plus.const import DOMAIN

from .conftest import (
    CLOSE,
    IPV4,
    IPV6,
    LTE_MAC,
    MAC,
    TRANSITION,
    FakeConsole,
    gateway,
    lte_device,
    sync,
)

WAN = "binary_sensor.gateway_cable"
LTE = "binary_sensor.gateway_lte"
BACKUP = "binary_sensor.gateway_backup_uplink_active"
ACTIVE = "sensor.gateway_active_uplink"
LAST = "sensor.gateway_last_lte_failover"
EVENT = "event.gateway_internet"
LATENCY = "sensor.gateway_internet_latency"
VPN = "sensor.gateway_vpn_clients"
LTE_PERCENT = "sensor.lte_backup_lte_data_used"
LTE_USAGE = "sensor.lte_backup_lte_data_this_month"
LTE_LIMIT = "sensor.lte_backup_lte_data_limit"


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run the coordinator's poll, as the 60-second timer would."""
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


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
        "UDMPRO",
        "4.0.0",
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
    # A transition timestamp that lags behind the seen switch is no brief failover
    await console.push(hass, sync(gateway(transition=TRANSITION + 3000)))
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
) -> None:
    """A failover that ended between two polls fires a brief failover."""
    console.set_gateway(gateway(transition=TRANSITION + 5000))
    await _poll(hass, setup_entry)
    assert hass.states.get(EVENT).attributes["event_type"] == "brief_failover"  # type: ignore[union-attr]


async def test_poll_failure_and_recovery(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """Without answer the entities are unavailable, not offline; the next good poll brings them back.

    A push while the poll fails is ignored: the poll decides whether the
    console is healthy, so a failing poll is not hidden behind push data.
    """
    console.error = NetworkError("down")
    await _poll(hass, setup_entry)
    assert hass.states.get(WAN).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    assert hass.states.get(BACKUP).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    await console.push(hass, sync(gateway(wan="offline")))
    assert hass.states.get(WAN).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    console.error = None
    await _poll(hass, setup_entry)
    assert hass.states.get(WAN).state == "on"  # type: ignore[union-attr]


async def test_gateway_missing(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """A console that no longer reports the gateway makes the entities unavailable."""
    console.devices = []
    await _poll(hass, setup_entry)
    assert hass.states.get(ACTIVE).state == STATE_UNAVAILABLE  # type: ignore[union-attr]


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """A rejected login asks for the password."""
    console.error = NetworkAuthError("no")
    await _poll(hass, setup_entry)
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
    """Failed or empty attempts wait longer each time; a connection that delivered is retried soon."""
    coordinator = setup_entry.runtime_data.coordinator
    assert coordinator.websocket_connected is True
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args: Any) -> None:
        if delay:  # asyncio.sleep(0) of Home Assistant itself goes through the patch as well
            delays.append(delay)
        await real_sleep(0)

    async def _wait_for_next_listen() -> None:
        calls = console.listen_calls
        while console.listen_calls == calls:
            await real_sleep(0)

    with patch("custom_components.network_plus.coordinator.asyncio.sleep", fake_sleep):
        # The standing connection delivered a message and closes, then every attempt is refused
        await console.push(hass, sync(gateway()))
        console.listen_error = NetworkError("refused")
        console.messages.put_nowait(CLOSE)
        while console.listen_calls < 5:
            await real_sleep(0)
        assert coordinator.websocket_connected is False
        assert delays[:4] == [5.0, 5.0, 10.0, 20.0]
        # A connection stands again, delivers, and drops with an error: retried soon
        console.listen_error = None
        await console.listening.wait()
        assert coordinator.websocket_connected is True
        await console.push(hass, sync(gateway()))
        console.close_with_error = True
        console.messages.put_nowait(CLOSE)
        await _wait_for_next_listen()
        assert delays[-1] == 5.0
        # Connections the console closes at once, without a message, back off again
        console.close_with_error = False
        await console.listening.wait()
        console.messages.put_nowait(CLOSE)
        await _wait_for_next_listen()
        await console.listening.wait()
        console.messages.put_nowait(CLOSE)
        await _wait_for_next_listen()
    assert delays[-2:] == [5.0, 10.0]
    assert max(delays) <= 300.0


async def test_unload(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Unloading stops the websocket and the entities and gives the session back."""
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(WAN).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    assert console.detached == 1


async def test_speedtest_latency_and_vpn_sensors(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Speed test, latency on the active uplink and remote clients come from the first poll."""
    assert _state(hass, LATENCY) == "14"
    assert hass.states.get(LATENCY).attributes["uplink"] == "wan"  # type: ignore[union-attr]
    assert _state(hass, "sensor.gateway_speed_test_download") == "949.0"
    assert _state(hass, "sensor.gateway_speed_test_upload") == "54.0"
    assert _state(hass, "sensor.gateway_speed_test_ping") == "18"
    last = hass.states.get("sensor.gateway_last_speed_test")
    assert last.state == "2023-11-14T22:13:20+00:00"  # type: ignore[union-attr]
    assert last.attributes["status"] == "Success"  # type: ignore[union-attr]
    vpn = hass.states.get(VPN)
    assert vpn.state == "1"  # type: ignore[union-attr]
    assert vpn.attributes["clients"] == ["Phone"]  # type: ignore[union-attr]
    assert vpn.attributes["types"] == ["teleport"]  # type: ignore[union-attr]


async def test_lte_sensors_and_device(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """The LTE device gets its own device below the gateway, with usage, share and limit."""
    assert _state(hass, LTE_PERCENT) == "0.01875"
    usage = hass.states.get(LTE_USAGE)
    assert usage.state == "0.0015"  # type: ignore[union-attr]
    assert usage.attributes["unit_of_measurement"] == "GB"  # type: ignore[union-attr]
    # The console resets the counter monthly; the statistics take the drop as reset
    assert usage.attributes["state_class"] == "total_increasing"  # type: ignore[union-attr]
    assert "last_reset" not in usage.attributes  # type: ignore[union-attr]
    limit = hass.states.get(LTE_LIMIT)
    assert limit.state == "8.0"  # type: ignore[union-attr]
    assert limit.attributes["warning_bytes"] == 6_000_000_000  # type: ignore[union-attr]
    registry = er.async_get(hass)
    signal = registry.async_get_entity_id("sensor", DOMAIN, f"{LTE_MAC}_signal")
    assert signal is not None
    assert registry.async_get(signal).disabled_by is er.RegistryEntryDisabler.INTEGRATION  # type: ignore[union-attr]
    devices = dr.async_get(hass)
    lte = devices.async_get_device_by_identifier((DOMAIN, LTE_MAC), setup_entry.entry_id)
    gateway_device = devices.async_get_device_by_identifier((DOMAIN, MAC), setup_entry.entry_id)
    assert lte is not None and gateway_device is not None
    assert lte.via_device_id == gateway_device.id
    assert (lte.name, lte.model, lte.sw_version) == ("LTE backup", "ULTEPRO", "6.0.0")


async def test_enabled_address_and_signal_sensors(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """Addresses and the LTE signal show once their sensors are enabled."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for unique_id in (f"{MAC}_ipv4_wan", f"{MAC}_ipv6_wan", f"{LTE_MAC}_signal"):
        registry.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=config_entry, disabled_by=None
        )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    ids = {
        key: str(registry.async_get_entity_id("sensor", DOMAIN, key))
        for key in (f"{MAC}_ipv4_wan", f"{MAC}_ipv6_wan", f"{LTE_MAC}_signal")
    }
    assert _state(hass, ids[f"{MAC}_ipv4_wan"]) == IPV4
    assert _state(hass, ids[f"{MAC}_ipv6_wan"]) == IPV6
    signal = hass.states.get(ids[f"{LTE_MAC}_signal"])
    assert signal.state == "-77"  # type: ignore[union-attr]
    assert signal.attributes["rsrp"] == -112  # type: ignore[union-attr]


async def test_lte_without_limit(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """Without an enforced data plan limit the share and the limit are unknown."""
    console.devices[2] = lte_device(limit=False)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, LTE_PERCENT) == STATE_UNKNOWN
    assert _state(hass, LTE_USAGE) == "0.0015"
    assert _state(hass, LTE_LIMIT) == STATE_UNKNOWN
    assert hass.states.get(LTE_LIMIT).attributes["warning_bytes"] is None  # type: ignore[union-attr]


async def test_push_with_numbers_only_waits_for_poll(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """Changed latency or usage over the websocket shows with the next poll, not at once."""
    await console.push(hass, sync(gateway(latency=99, download=10.0)))
    assert _state(hass, LATENCY) == "14"
    assert _state(hass, "sensor.gateway_speed_test_download") == "949.0"
    console.set_gateway(gateway(latency=99, download=10.0))
    await _poll(hass, setup_entry)
    assert _state(hass, LATENCY) == "99"
    assert _state(hass, "sensor.gateway_speed_test_download") == "10.0"


async def test_speedtest_missing(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """A gateway without speed test results gives unknown sensors."""
    raw = gateway()
    raw["uplink"] = {"comment": "WAN", "latency": 15}
    console.set_gateway(raw)
    await _poll(hass, setup_entry)
    assert _state(hass, "sensor.gateway_speed_test_download") == STATE_UNKNOWN
    assert _state(hass, "sensor.gateway_last_speed_test") == STATE_UNKNOWN
    assert hass.states.get("sensor.gateway_last_speed_test").attributes["status"] is None  # type: ignore[union-attr]


async def test_push_of_lte_state(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A changed LTE state over the websocket is applied at once, with its numbers."""
    await console.push(hass, sync(lte_device(rx=2_000_000, connected="no")))
    assert _state(hass, LTE_PERCENT) == "0.03125"
    # Numbers alone wait for the poll
    await console.push(hass, sync(lte_device(rx=3_000_000, connected="no")))
    assert _state(hass, LTE_PERCENT) == "0.03125"


async def test_lte_appears_later(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """An LTE device adopted later gets its entities without a reload."""
    console.devices = console.devices[:2]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LTE_PERCENT) is None
    await console.push(hass, sync(lte_device()))
    assert _state(hass, LTE_PERCENT) == "0.01875"


async def test_public_ip_change_event(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A new public address fires an event, also when the address was gone in between."""
    raw = gateway()
    del raw["last_wan_interfaces"]
    del raw["wan1"]
    await console.push(hass, sync(raw))
    assert _state(hass, EVENT) == STATE_UNKNOWN
    await console.push(hass, sync(gateway(ipv4="198.51.100.8")))
    event = hass.states.get(EVENT)
    assert event.attributes["event_type"] == "public_ip_changed"  # type: ignore[union-attr]
    assert event.attributes["family"] == "ipv4"  # type: ignore[union-attr]
    assert event.attributes["address"] == "198.51.100.8"  # type: ignore[union-attr]
    assert event.attributes["previous"] == IPV4  # type: ignore[union-attr]


async def test_clients_not_readable(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """Without the clients endpoint the VPN sensor is unknown; the uplinks stay available."""
    console.clients_error = NetworkError("no such endpoint")
    await _poll(hass, setup_entry)
    assert _state(hass, VPN) == STATE_UNKNOWN
    assert hass.states.get(VPN).attributes["clients"] == []  # type: ignore[union-attr]
    assert _state(hass, WAN) == "on"
    console.clients_error = NetworkAuthError("no")
    await _poll(hass, setup_entry)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_partial_update_is_merged(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A device:update carries only changed fields; the rest of the object is kept."""
    await console.push(
        hass,
        {
            "meta": {"message": "device:update"},
            "data": [
                {"firewall_hits_state": 1},  # no MAC: ignored
                {"mac": MAC, "last_wan_status": {"WAN": "offline", "WAN_LTE_FAILOVER": "online"}},
                {"mac": LTE_MAC, "lte_connected": "no"},
            ],
        },
    )
    assert _state(hass, WAN) == "off"
    assert _state(hass, LATENCY) == "14"  # the merged object still has its monitors
    assert hass.states.get(EVENT).attributes["event_type"] == "uplink_down"  # type: ignore[union-attr]
    assert setup_entry.runtime_data.coordinator.data.lte[LTE_MAC].connected is False  # type: ignore[index]


async def test_poll_overlapped_by_push_keeps_the_push(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """A poll answer from before a pushed failover must not turn the failover back."""
    console.devices_gate = asyncio.Event()
    poll = hass.async_create_task(setup_entry.runtime_data.coordinator.async_refresh())
    for _ in range(5):
        await asyncio.sleep(0)  # the poll now waits for its device answer
    # Not console.push: that would wait for every task, the held poll included
    console.messages.put_nowait(sync(gateway(wan="offline", active="WAN_LTE_FAILOVER")))
    for _ in range(5):
        await asyncio.sleep(0)
    assert _state(hass, ACTIVE) == "wan_lte_failover"
    console.devices_gate.set()  # the answer is the snapshot from before the failover
    await poll
    await hass.async_block_till_done()
    assert _state(hass, ACTIVE) == "wan_lte_failover"
    assert hass.states.get(EVENT).attributes["event_type"] == "failover"  # type: ignore[union-attr]
    assert _state(hass, LTE_PERCENT) == "0.01875"  # the LTE device stays known


async def test_uplink_renamed_reloads_entry(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Entity names carry the uplink names; a rename in UniFi Network renews them."""
    coordinator = setup_entry.runtime_data.coordinator
    assert hass.states.get(WAN).attributes["friendly_name"] == "Gateway Cable"  # type: ignore[union-attr]
    # An uplink missing from one configuration answer is no rename
    cable = console.configs.pop(0)
    await _poll(hass, setup_entry)
    assert setup_entry.runtime_data.coordinator is coordinator
    console.configs.insert(0, cable)
    await _poll(hass, setup_entry)
    assert setup_entry.runtime_data.coordinator is coordinator
    console.configs[0]["name"] = "Fibre"
    await _poll(hass, setup_entry)
    assert setup_entry.state is ConfigEntryState.LOADED
    assert setup_entry.runtime_data.coordinator is not coordinator
    assert hass.states.get(WAN).attributes["friendly_name"] == "Gateway Fibre"  # type: ignore[union-attr]


async def test_two_lte_devices(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Every LTE device gets its own device and entities, each with its own values."""
    second = lte_device(mac="02:a1:b2:c3:d4:e7", name="LTE second", rx=3_000_000)
    await console.push(hass, sync(second))
    assert _state(hass, LTE_PERCENT) == "0.01875"
    assert _state(hass, "sensor.lte_second_lte_data_used") == "0.04375"
    devices = dr.async_get(hass)
    assert devices.async_get_device_by_identifier(
        (DOMAIN, "02:a1:b2:c3:d4:e7"), setup_entry.entry_id
    )


async def test_poll_requests_run_together(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
) -> None:
    """Devices, uplink configuration and clients are fetched in parallel; a clients error stays soft."""
    console.clients_error = NetworkError("no such endpoint")
    console.devices_gate = asyncio.Event()
    poll = hass.async_create_task(setup_entry.runtime_data.coordinator.async_refresh())
    for _ in range(5):
        await asyncio.sleep(0)
    console.devices_gate.set()
    await poll
    await hass.async_block_till_done()
    assert _state(hass, WAN) == "on"
    assert _state(hass, VPN) == STATE_UNKNOWN


async def test_unexpected_poll_errors(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """An error that is no network error is not swallowed, for devices or clients."""
    coordinator = setup_entry.runtime_data.coordinator
    console.clients_error = RuntimeError("boom")
    await _poll(hass, setup_entry)
    assert coordinator.last_update_success is False
    console.clients_error = None
    console.error = RuntimeError("boom")
    await _poll(hass, setup_entry)
    assert coordinator.last_update_success is False
    console.error = None
    await _poll(hass, setup_entry)
    assert coordinator.last_update_success is True


async def test_poll_refreshes_devices_that_were_not_pushed(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A gateway push during the poll keeps the gateway's object; the LTE device still takes the poll's."""
    console.devices_gate = asyncio.Event()
    poll = hass.async_create_task(setup_entry.runtime_data.coordinator.async_refresh())
    for _ in range(5):
        await asyncio.sleep(0)
    console.messages.put_nowait(sync(gateway()))  # the gateway, unchanged, during the poll
    for _ in range(5):
        await asyncio.sleep(0)
    console.devices[2] = lte_device(rx=2_000_000)
    console.devices_gate.set()
    await poll
    await hass.async_block_till_done()
    assert _state(hass, LTE_PERCENT) == "0.03125"


async def test_poll_drops_removed_lte_device(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """An LTE device the console no longer lists makes its entities unavailable."""
    console.devices = console.devices[:2]
    await _poll(hass, setup_entry)
    assert _state(hass, LTE_PERCENT) == STATE_UNAVAILABLE


async def test_unexpected_push_does_not_end_the_listener(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A push that raises in a listener is logged; the websocket is connected again."""
    coordinator = setup_entry.runtime_data.coordinator

    def _broken(_event: Any) -> None:
        raise RuntimeError("listener broke")

    coordinator.async_add_event_listener(_broken)
    calls = console.listen_calls
    real_sleep = asyncio.sleep

    async def fake_sleep(_delay: float, *args: Any) -> None:
        await real_sleep(0)

    with patch("custom_components.network_plus.coordinator.asyncio.sleep", fake_sleep):
        await console.push(hass, sync(gateway(wan="offline", active="WAN_LTE_FAILOVER")))
        for _ in range(50):
            if console.listen_calls > calls:
                break
            await real_sleep(0)
        await console.listening.wait()
    assert console.listen_calls == calls + 1
    assert coordinator.websocket_connected is True
    # Odd values in a push are no error either
    raw = gateway(wan="offline", active="WAN_LTE_FAILOVER")
    raw["last_lte_failover_transition_timestamp"] = float("inf")
    raw["uptime_stats"]["WAN"] = {"monitors": 5}
    console.messages.put_nowait(sync(raw))
    for _ in range(5):
        await asyncio.sleep(0)
    assert console.listen_calls == calls + 1


async def test_rejected_login_waits_before_trying_again(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A rejected login is retried only after the longest delay, and the listener comes back."""
    coordinator = setup_entry.runtime_data.coordinator
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args: Any) -> None:
        if delay:  # asyncio.sleep(0) of Home Assistant itself goes through the patch as well
            delays.append(delay)
        await real_sleep(0)

    console.listen_error = NetworkAuthError("no")
    with patch("custom_components.network_plus.coordinator.asyncio.sleep", fake_sleep):
        console.messages.put_nowait(CLOSE)
        while console.listen_calls < 4:
            await real_sleep(0)
        assert coordinator.websocket_connected is False
        assert set(delays[1:]) == {300.0}
        # The console accepts the login again, for example after it booted
        console.listen_error = None
        await console.listening.wait()
    assert coordinator.websocket_connected is True


async def test_push_does_not_postpone_the_poll(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A push with an essential change leaves the poll timer alone."""
    coordinator = setup_entry.runtime_data.coordinator
    timer = coordinator._unsub_refresh
    assert timer is not None
    await console.push(hass, sync(gateway(wan="offline", active="WAN_LTE_FAILOVER")))
    assert _state(hass, ACTIVE) == "wan_lte_failover"
    assert coordinator._unsub_refresh is timer


async def test_brief_failover_waits_for_a_switch_to_follow(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A fresh transition timestamp is no brief failover while a switch may still follow."""
    fresh = int(dt_util.utcnow().timestamp() * 1000)
    # The timestamp arrives a push before the switch: no brief failover, one failover
    await console.push(hass, sync(gateway(transition=fresh)))
    assert _state(hass, EVENT) == STATE_UNKNOWN
    await console.push(
        hass, sync(gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=fresh))
    )
    assert hass.states.get(EVENT).attributes["event_type"] == "failover"  # type: ignore[union-attr]
    await console.push(hass, sync(gateway(transition=fresh)))
    assert hass.states.get(EVENT).attributes["event_type"] == "restored"  # type: ignore[union-attr]
    # A fresh timestamp that no switch follows is a brief failover once the tolerance passed
    freezer.tick(timedelta(seconds=60))
    later = int(dt_util.utcnow().timestamp() * 1000)
    await console.push(hass, sync(gateway(transition=later)))
    assert hass.states.get(EVENT).attributes["event_type"] == "restored"  # type: ignore[union-attr]
    freezer.tick(timedelta(seconds=31))
    console.set_gateway(gateway(transition=later))
    await _poll(hass, setup_entry)
    assert hass.states.get(EVENT).attributes["event_type"] == "brief_failover"  # type: ignore[union-attr]
