"""Tests for the history of the page: switches, speed tests, drills, remote sessions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.network_plus.api import NetworkError
from custom_components.network_plus.const import (
    CONF_HISTORY_DAYS,
    EVENT_DRILL,
    EVENT_SPEEDTEST,
    HISTORY_SAVE_DELAY,
)
from custom_components.network_plus.history import storage_key
from custom_components.network_plus.model import UplinkEvent

from .conftest import SPEEDTEST_RUN, TRANSITION, FakeConsole, gateway, sync, teleport_client


def _history(entry: MockConfigEntry) -> Any:
    return entry.runtime_data.history


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_switches_and_uplinks_are_kept(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Failover, return, an uplink going down and up and a new address are kept, no addresses."""
    failed_over = gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000)
    await console.push(hass, sync(failed_over))
    await console.push(hass, sync(gateway(transition=TRANSITION + 2000)))
    await console.push(hass, sync(gateway(ipv4="198.51.100.8", transition=TRANSITION + 2000)))
    events = [
        {key: value for key, value in event.items() if key != "at"}
        for event in _history(setup_entry).events
    ]
    assert events == [
        {"type": "uplink_down", "uplink": "WAN", "uplink_name": "Cable"},
        {
            "type": "failover",
            "from_uplink": "WAN",
            "from_uplink_name": "Cable",
            "to_uplink": "WAN_LTE_FAILOVER",
            "to_uplink_name": "LTE",
        },
        {"type": "uplink_up", "uplink": "WAN", "uplink_name": "Cable"},
        {
            "type": "restored",
            "from_uplink": "WAN_LTE_FAILOVER",
            "from_uplink_name": "LTE",
            "to_uplink": "WAN",
            "to_uplink_name": "Cable",
        },
        {"type": "public_ip_changed", "uplink": "WAN", "uplink_name": "Cable", "family": "ipv4"},
    ]


async def test_brief_failover_keeps_its_time(
    hass: HomeAssistant, setup_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """A brief failover is kept at the time the console reported, a drill event not at all."""
    freezer.move_to("2024-01-02T00:00:00+00:00")
    history = _history(setup_entry)
    history.add_event(UplinkEvent("brief_failover", {"at": "2024-01-01T00:00:00+00:00"}))
    history.add_event(UplinkEvent("drill_completed", {}))
    assert history.events == [{"at": "2024-01-01T00:00:00+00:00", "type": "brief_failover"}]


async def test_speedtests_and_drills_are_kept_once(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A finished speed test and a completed drill are kept once; other consoles' are not."""
    freezer.move_to("2023-11-15T00:00:00+00:00")
    finished = gateway(interface="gre1", download=40.0, speedtest_run=SPEEDTEST_RUN + 60)
    await console.push(hass, sync(finished))
    entry_id = setup_entry.entry_id
    hass.bus.async_fire(
        EVENT_SPEEDTEST, {"entry_id": entry_id, "last_run": "2023-11-14T22:14:20+00:00"}
    )
    hass.bus.async_fire(
        EVENT_SPEEDTEST, {"entry_id": "other", "last_run": "2024-01-01T00:00:00+00:00"}
    )
    hass.bus.async_fire(EVENT_SPEEDTEST, {"entry_id": entry_id, "last_run": None})
    drill = {
        "entry_id": entry_id,
        "started_at": "2024-01-01T12:00:00+00:00",
        "failover_seconds": 20.0,
        "restore_seconds": 15.0,
        "data_used_bytes": 10,
    }
    hass.bus.async_fire(EVENT_DRILL, drill)
    hass.bus.async_fire(EVENT_DRILL, drill)
    hass.bus.async_fire(EVENT_DRILL, {**drill, "entry_id": "other"})
    hass.bus.async_fire(EVENT_DRILL, {"entry_id": entry_id})
    await hass.async_block_till_done()
    history = _history(setup_entry)
    assert history.speedtests == [
        {
            "at": "2023-11-14T22:14:20+00:00",
            "uplink": "wan_lte_failover",
            "uplink_name": "LTE",
            "download": 40.0,
            "upload": 54.0,
            "ping": 18,
        }
    ]
    assert history.drills == [
        {
            "at": "2024-01-01T12:00:00+00:00",
            "failover_seconds": 20.0,
            "restore_seconds": 15.0,
            "data_used_bytes": 10,
        }
    ]


async def test_remote_sessions(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A remote client opens a session when it appears and closes it when it leaves."""
    history = _history(setup_entry)
    assert history.as_dict()["sessions"] == [
        {"at": "2023-11-14T22:13:20+00:00", "until": None, "type": "TELEPORT", "name": "Phone"}
    ]
    console.clients = [teleport_client(), {"type": "VPN", "display_name": "Laptop"}]
    freezer.tick(timedelta(minutes=1))
    await _poll(hass, setup_entry)
    joined = dt_util.utcnow().isoformat()
    assert history.as_dict()["sessions"][1] == {
        "at": joined,
        "until": None,
        "type": "VPN",
        "name": "Laptop",
    }
    console.clients = []
    freezer.tick(timedelta(minutes=1))
    await _poll(hass, setup_entry)
    left = dt_util.utcnow().isoformat()
    assert [s["until"] for s in history.sessions] == [left, left]
    # A client that comes back opens a new session
    console.clients = [teleport_client(since=1_800_000_000)]
    await _poll(hass, setup_entry)
    assert len(history.sessions) == 3
    assert history.sessions[2]["until"] is None


async def test_remote_clients_not_readable(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """While the console does not tell its clients, open sessions stay open."""
    console.clients_error = NetworkError("no such endpoint")
    await _poll(hass, setup_entry)
    assert _history(setup_entry).sessions[0]["until"] is None


async def test_saved_and_loaded(
    hass: HomeAssistant,
    console: FakeConsole,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The history is saved after a change and on unload; it comes back with the entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    key = storage_key(config_entry.entry_id)
    freezer.tick(timedelta(seconds=HISTORY_SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"]["sessions"][0]["name"] == "Phone"
    # A change shortly before a reload is written by the unload
    await console.push(hass, sync(gateway(ipv4="198.51.100.8")))
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"]["events"][0]["type"] == "public_ip_changed"
    assert _history(config_entry).events[0]["type"] == "public_ip_changed"
    # A reload without changes writes nothing
    del hass_storage[key]
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
    # Removing the console removes its history
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {}}
    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_load_drops_old_and_unreadable_entries(
    hass: HomeAssistant,
    console: FakeConsole,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Entries older than the kept days go, drills and open sessions stay, junk is dropped."""
    now = dt_util.utcnow()
    old = (now - timedelta(days=40)).isoformat()
    new = (now - timedelta(days=1)).isoformat()
    key = storage_key(config_entry.entry_id)
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {
            "events": [{"at": old, "type": "failover"}, {"at": new, "type": "restored"}, "x", {}],
            "speedtests": [{"at": old}, {"at": new, "download": 1}],
            "drills": [{"at": old, "failover_seconds": 1}],
            "sessions": [
                {"at": old, "until": old, "type": "VPN", "name": "Gone"},
                {"at": old, "until": None, "type": "TELEPORT", "name": "Phone"},
                {"at": new, "type": "VPN"},
            ],
        },
    }
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_HISTORY_DAYS: 30})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    history = _history(config_entry)
    assert history.events == [{"at": new, "type": "restored"}]
    assert history.speedtests == [{"at": new, "download": 1}]
    assert history.drills == [{"at": old, "failover_seconds": 1}]
    assert [s["name"] for s in history.sessions] == ["Phone"]
    assert history.as_dict()["days"] == 30


async def test_load_of_a_broken_store(
    hass: HomeAssistant,
    console: FakeConsole,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """A part that is no list starts empty."""
    key = storage_key(config_entry.entry_id)
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"events": "x"}}
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _history(config_entry).events == []


async def test_remote_sessions_of_the_same_name(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Two clients of the same name have a session each; one leaving closes only its own."""
    history = _history(setup_entry)
    console.clients = [
        teleport_client(client_id="a"),
        teleport_client(since=1_700_000_600, client_id="b"),
    ]
    freezer.tick(timedelta(minutes=1))
    await _poll(hass, setup_entry)
    # The client without an ID before is another one than these two
    assert [s["until"] is None for s in history.sessions] == [False, True, True]
    console.clients = [teleport_client(since=1_700_000_600, client_id="b")]
    freezer.tick(timedelta(minutes=1))
    await _poll(hass, setup_entry)
    assert [s["until"] is None for s in history.sessions] == [False, False, True]


async def test_old_session_without_a_match_closes(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An open session from before keys were kept closes if two clients carry its name."""
    history = _history(setup_entry)
    for session in history.sessions:
        session.pop("key", None)
    console.clients = [teleport_client(client_id="a"), teleport_client(client_id="b")]
    freezer.tick(timedelta(minutes=1))
    await _poll(hass, setup_entry)
    assert history.sessions[0]["until"] is not None
    assert [s["until"] for s in history.sessions[1:]] == [None, None]


async def test_old_session_goes_to_its_client(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An open session from before keys were kept stays open for the one client it names."""
    history = _history(setup_entry)
    for session in history.sessions:
        session.pop("key", None)
    console.clients = [teleport_client(client_id="a")]
    freezer.tick(timedelta(minutes=1))
    await _poll(hass, setup_entry)
    assert len(history.sessions) == 1
    assert history.sessions[0]["until"] is None
    assert history.sessions[0]["key"]
