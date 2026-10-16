"""Fixtures: a UniFi OS console simulated in memory."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
import copy
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

from custom_components.network_plus.api import find_gateway
from custom_components.network_plus.const import DOMAIN

MAC = "02:a1:b2:c3:d4:e5"
HOST = "192.0.2.1"
USERNAME = "homeassistant"
PASSWORD = "secret"
ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_USERNAME: USERNAME,
    CONF_PASSWORD: PASSWORD,
    CONF_VERIFY_SSL: True,
}
TRANSITION = 1_700_000_000_000  # ms

CLOSE = object()


def wan_configs() -> list[dict[str, Any]]:
    """The uplink configuration of a console with cable, an unused WAN2 and LTE."""
    return [
        {"name": "Cable", "wan_networkgroup": "WAN", "wan_failover_priority": 1},
        {"name": "Internet 2", "wan_networkgroup": "WAN2", "wan_failover_priority": 2},
        {"name": "LTE", "wan_networkgroup": "WAN_LTE_FAILOVER", "wan_failover_priority": -1},
    ]


def gateway(
    *,
    wan: str = "online",
    lte: str = "online",
    active: str = "WAN",
    transition: int = TRANSITION,
    mac: str = MAC,
) -> dict[str, Any]:
    """Return the gateway's runtime object as stat/device and the websocket send it."""
    return {
        "mac": mac,
        "type": "udm",
        "model": "UDMPROSE",
        "name": "Gateway",
        "version": "5.1.33",
        "last_wan_status": {"WAN": wan, "WAN_LTE_FAILOVER": lte},
        "uplink": {"comment": active, "name": "eth8"},
        "uptime_stats": {
            "WAN": {"availability": 100.0, "latency_average": 14},
            "WAN_LTE_FAILOVER": {"availability": 99.5, "latency_average": 158},
        },
        "last_lte_failover_transition_timestamp": transition,
    }


def sync(raw: dict[str, Any], message: str = "device:sync") -> dict[str, Any]:
    """Wrap a runtime object in a websocket message."""
    return {"meta": {"message": message}, "data": [raw]}


class FakeConsole:
    """Behaves like NetworkClient, as far as the integration uses it."""

    def __init__(self) -> None:
        self.devices: list[dict[str, Any]] = [
            gateway(),
            {"mac": "02:00:00:00:00:02", "type": "usw"},
        ]
        self.configs = wan_configs()
        self.error: Exception | None = None
        self.listen_error: Exception | None = None
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        self.listening = asyncio.Event()
        self.listen_calls = 0
        self.created_with: list[tuple[str, str, str]] = []

    def set_gateway(self, raw: dict[str, Any]) -> None:
        """Replace the gateway's runtime object."""
        self.devices[0] = raw

    async def get_gateway(self, mac: str | None = None) -> dict[str, Any] | None:
        if self.error:
            raise self.error
        found = find_gateway(self.devices, mac)
        return copy.deepcopy(found) if found else None

    async def get_wan_configs(self) -> list[dict[str, Any]]:
        if self.error:
            raise self.error
        return copy.deepcopy(self.configs)

    async def listen(self, on_message: Callable[[dict[str, Any]], None]) -> None:
        self.listen_calls += 1
        if self.listen_error:
            raise self.listen_error
        self.listening.set()
        while (message := await self.messages.get()) is not CLOSE:
            on_message(message)
        self.listening.clear()

    async def push(self, hass: HomeAssistant, message: Any) -> None:
        """Send a websocket message and let Home Assistant handle it."""
        await self.listening.wait()
        self.messages.put_nowait(message)
        await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def console() -> Generator[FakeConsole]:
    """Replace the client by the simulated console."""
    fake = FakeConsole()

    def factory(_session: Any, host: str, username: str, password: str) -> FakeConsole:
        fake.created_with.append((host, username, password))
        return fake

    with patch("custom_components.network_plus.NetworkClient", side_effect=factory):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured console."""
    return MockConfigEntry(domain=DOMAIN, title="Gateway", unique_id=MAC, data=dict(ENTRY_DATA))


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration with the simulated console."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
