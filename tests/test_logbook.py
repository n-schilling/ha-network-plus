"""Tests for the speed test and drill events and their logbook entries."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant.common import MockConfigEntry, async_capture_events

from homeassistant.core import Event, HomeAssistant

from custom_components.network_plus.const import DOMAIN, EVENT_DRILL, EVENT_SPEEDTEST
from custom_components.network_plus.logbook import async_describe_events

from .conftest import SPEEDTEST_RUN, FakeConsole, gateway, sync


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_new_result_fires_the_event(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A finished test is an event, once per run."""
    events = async_capture_events(hass, EVENT_SPEEDTEST)
    # The result found at setup is no news
    await _poll(hass, setup_entry)
    assert events == []
    finished = gateway(interface="gre1", download=40.0, speedtest_run=SPEEDTEST_RUN + 60)
    await console.push(hass, sync(finished))
    assert len(events) == 1
    assert events[0].data == {
        "entry_id": setup_entry.entry_id,
        "gateway": "Gateway",
        "download": 40.0,
        "upload": 54.0,
        "ping": 18,
        "last_run": "2023-11-14T22:14:20+00:00",
        "uplink": "wan_lte_failover",
        "uplink_name": "LTE",
    }
    # An interface named later for the same run is no new result
    console.set_gateway(gateway(interface="eth8", download=40.0, speedtest_run=SPEEDTEST_RUN + 60))
    await _poll(hass, setup_entry)
    assert len(events) == 1
    # A result seen by the poll is announced as well, with the active uplink
    console.set_gateway(gateway(interface=None, download=30.0, speedtest_run=SPEEDTEST_RUN + 120))
    await _poll(hass, setup_entry)
    assert len(events) == 2
    assert (events[1].data["download"], events[1].data["uplink_name"]) == (30.0, "Cable")


async def test_result_seen_at_setup_is_not_announced(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """Neither the first setup nor a restart with the stored run fires the event."""
    events = async_capture_events(hass, EVENT_SPEEDTEST)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert events == []
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert events == []


def test_describe_speedtest_event() -> None:
    """The entry's name carries the gateway and the result; odd values are left out."""
    described: dict[str, Any] = {}

    def register(domain: str, event_type: str, describe: Any) -> None:
        described[(domain, event_type)] = describe

    async_describe_events(None, register)  # type: ignore[arg-type]
    describe = described[(DOMAIN, EVENT_SPEEDTEST)]
    full = describe(
        Event(
            EVENT_SPEEDTEST,
            {"gateway": "Gateway", "download": 40.4, "upload": 5, "ping": 61, "uplink_name": "LTE"},
        )
    )
    assert full == {
        "name": "Gateway \u00b7 \u2193 40 Mbit/s \u00b7 \u2191 5 Mbit/s \u00b7 61 ms \u00b7 LTE",
        "message": "",
    }
    sparse = describe(Event(EVENT_SPEEDTEST, {"download": "fast", "upload": None, "ping": True}))
    assert sparse == {"name": "Network Plus", "message": ""}


def test_describe_drill_event() -> None:
    """The entry's name carries the gateway and the drill's results; missing ones are left out."""
    described: dict[str, Any] = {}

    def register(domain: str, event_type: str, describe: Any) -> None:
        described[(domain, event_type)] = describe

    async_describe_events(None, register)  # type: ignore[arg-type]
    describe = described[(DOMAIN, EVENT_DRILL)]
    full = describe(
        Event(
            EVENT_DRILL,
            {
                "gateway": "Gateway",
                "failover_seconds": 47.3,
                "restore_seconds": 9.6,
                "data_used_bytes": 31_000_000,
            },
        )
    )
    assert full == {
        "name": "Gateway \u00b7 \u21c4 \u00b7 \u2192 47 s \u00b7 \u2190 10 s \u00b7 31 MB",
        "message": "",
    }
    sparse = describe(Event(EVENT_DRILL, {"failover_seconds": 15, "restore_seconds": None}))
    assert sparse == {"name": "Network Plus \u00b7 \u21c4 \u00b7 \u2192 15 s", "message": ""}
