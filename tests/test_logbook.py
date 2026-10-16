"""Tests for the speed test event and its logbook entry."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from homeassistant.core import Event, HomeAssistant

from custom_components.network_plus.const import DOMAIN, EVENT_SPEEDTEST
from custom_components.network_plus.logbook import async_describe_events

from .conftest import SPEEDTEST_RUN, FakeConsole, gateway, sync

SENSORS = (
    "sensor.gateway_speed_test_download",
    "sensor.gateway_speed_test_upload",
    "sensor.gateway_speed_test_ping",
    "sensor.gateway_last_speed_test",
)


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_new_result_fires_the_event_and_the_sensors_share_its_context(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A finished test is an event; the sensors' new states carry its context."""
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
    for entity_id in SENSORS:
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.context.id == events[0].context.id, entity_id
    # An interface named later for the same run is no new result, and the
    # sensors' next writes carry no stale context
    console.set_gateway(gateway(interface="eth8", download=40.0, speedtest_run=SPEEDTEST_RUN + 60))
    await _poll(hass, setup_entry)
    assert len(events) == 1
    download = hass.states.get("sensor.gateway_speed_test_download")
    assert download.attributes["uplink"] == "wan"  # type: ignore[union-attr]
    # A result seen by the poll is announced as well, with the active uplink
    console.set_gateway(gateway(interface=None, download=30.0, speedtest_run=SPEEDTEST_RUN + 120))
    await _poll(hass, setup_entry)
    assert len(events) == 2
    assert (events[1].data["download"], events[1].data["uplink_name"]) == (30.0, "Cable")
    last = hass.states.get("sensor.gateway_last_speed_test")
    assert last.context.id == events[1].context.id  # type: ignore[union-attr]


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
