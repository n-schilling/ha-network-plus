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

from custom_components.network_plus.api import NetworkError, find_gateway
from custom_components.network_plus.const import (
    CONF_ADMIN_PASSWORD,
    CONF_ADMIN_USERNAME,
    CONF_USE_ADMIN,
    DOMAIN,
)

MAC = "02:a1:b2:c3:d4:e5"
LTE_MAC = "02:a1:b2:c3:d4:e6"
HOST = "192.0.2.1"
IPV4 = "198.51.100.7"
IPV6 = "2001:db8::7"
SPEEDTEST_RUN = 1_700_000_000  # s
USERNAME = "homeassistant"
PASSWORD = "secret"
ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_USERNAME: USERNAME,
    CONF_PASSWORD: PASSWORD,
    CONF_VERIFY_SSL: True,
}
TRANSITION = 1_700_000_000_000  # ms
ADMIN_DATA = {
    CONF_USE_ADMIN: True,
    CONF_ADMIN_USERNAME: "netadmin",
    CONF_ADMIN_PASSWORD: "admin-secret",
}

CLOSE = object()


def wan_configs() -> list[dict[str, Any]]:
    """The uplink configuration of a console with cable, an unused WAN2 and LTE."""
    return [
        {
            "_id": "net-wan",
            "name": "Cable",
            "wan_networkgroup": "WAN",
            "wan_failover_priority": 1,
            "wan_type": "dhcp",
        },
        {
            "_id": "net-wan2",
            "name": "Internet 2",
            "wan_networkgroup": "WAN2",
            "wan_failover_priority": 2,
            "wan_type": "dhcp",
        },
        {
            "_id": "net-lte",
            "name": "LTE",
            "wan_networkgroup": "WAN_LTE_FAILOVER",
            "wan_failover_priority": -1,
            "wan_type": "static",
        },
    ]


def gateway(
    *,
    wan: str = "online",
    lte: str = "online",
    active: str = "WAN",
    transition: int = TRANSITION,
    mac: str = MAC,
    latency: int = 14,
    ipv4: str = IPV4,
    ipv6: str = IPV6,
    download: float = 949.0,
    speed: int = 2500,
    duplex: bool = True,
    up: bool = True,
    interface: str | None = "eth8",
    speedtest_summary: int = 2,
    speedtest_run: int = SPEEDTEST_RUN,
) -> dict[str, Any]:
    """Return the gateway's runtime object as stat/device and the websocket send it.

    ``speedtest-status`` is the status block of the speed test: ``interface``
    the one it ran over (the gateway names it only for a while after the
    test), ``speedtest_summary`` its status, 2 for finished, 1 for running
    (then without a result and an interface, as seen live).
    """
    if speedtest_summary == 1:
        progress: dict[str, Any] = {
            "interface_name": None,
            "source_interface": "",
            "rundate": 0,
            "runtime": 0,
            "status_download": 1,
            "status_ping": 2,
            "status_summary": 1,
            "status_upload": 0,
            "xput_download": 0.0,
            "xput_upload": 0.0,
            "latency": 18,
        }
    else:
        progress = {
            "interface_name": interface,
            "source_interface": f"if!{interface}" if interface else "",
            "rundate": speedtest_run,
            "runtime": 26,
            "status_download": speedtest_summary,
            "status_ping": speedtest_summary,
            "status_summary": speedtest_summary,
            "status_upload": speedtest_summary,
            "xput_download": download,
            "xput_upload": 54.0,
            "latency": 18,
        }
    return {
        "mac": mac,
        "type": "udm",
        "model": "UDMPRO",
        "name": "Gateway",
        "version": "4.0.0",
        "last_wan_status": {"WAN": wan, "WAN_LTE_FAILOVER": lte},
        "last_wan_interfaces": {"WAN": {"ip": ipv4, "alive": wan == "online"}},
        "wan1": {
            "ifname": "eth8",
            "ipv6": ["fe80::1", ipv6],
            "ip": ipv4,
            "speed": speed,
            "full_duplex": duplex,
            "up": up,
            "max_speed": 10000,
        },
        "uplink": {
            "comment": active,
            "name": "eth8",
            "latency": 15,
            "speedtest_lastrun": speedtest_run,
            "speedtest_ping": 18,
            "speedtest_status": "Success",
            "xput_down": download,
            "xput_up": 54.0,
        },
        "uptime_stats": {
            "WAN": {"availability": 100.0, "latency_average": latency},
            "WAN_LTE_FAILOVER": {"availability": 99.5, "latency_average": 158},
        },
        "speedtest-status": progress,
        "last_lte_failover_transition_timestamp": transition,
    }


def lte_device(
    *,
    mac: str = LTE_MAC,
    name: str = "LTE backup",
    rx: int = 1_000_000,
    tx: int = 500_000,
    connected: Any = "yes",
    in_use: Any = False,
    hard: int | None = 8_000_000_000,
    soft: int | None = 6_000_000_000,
    limit: bool = True,
    rssi: str = "-77",
    pin_required: Any = True,
    pin_verified: Any = True,
    pin_tries: Any = 3,
) -> dict[str, Any]:
    """Return the LTE device's runtime object, secrets included as the console sends them."""
    return {
        "mac": mac,
        "type": "uap",
        "model": "ULTEPRO",
        "name": name,
        "version": "6.0.0",
        "lte_state": "ready",
        "lte_connected": connected,
        "lte_failover": in_use,
        "rx_bytes": rx,
        "tx_bytes": tx,
        "lte_hard_limit": hard,
        "lte_soft_limit": soft,
        "lte_data_limit_enabled": limit,
        "lte_rssi": rssi,
        "lte_rsrp": "-112",
        "lte_sim_pin": "9876",
        "lte_imei": "123456789012345",
        "lte_networkoperator": "Carrier",
        "lte_is_sim_pin_required": pin_required,
        "lte_is_sim_pin_verified": pin_verified,
        "lte_sim_pin_tries_left": pin_tries,
    }


def teleport_client(name: str = "Phone", since: int = 1_700_000_000) -> dict[str, Any]:
    """Return a connected Teleport client as the console lists it."""
    return {"type": "TELEPORT", "display_name": name, "assoc_time": since, "status": "online"}


def sync(raw: dict[str, Any], message: str = "device:sync") -> dict[str, Any]:
    """Wrap a runtime object in a websocket message."""
    return {"meta": {"message": message}, "data": [raw]}


class FakeConsole:
    """Behaves like NetworkClient, as far as the integration uses it."""

    def __init__(self) -> None:
        self.devices: list[dict[str, Any]] = [
            gateway(),
            {"mac": "02:00:00:00:00:02", "type": "usw"},
            lte_device(),
        ]
        self.configs = wan_configs()
        self.clients: list[dict[str, Any]] = [
            {"type": "WIRED", "display_name": "Desk"},
            {"type": "WIRELESS", "display_name": "Laptop"},
            teleport_client(),
        ]
        self.error: Exception | None = None
        self.clients_error: Exception | None = None
        # Admin features: what the console answers about the user's rights and writes
        self.permissions: dict[str, list[str]] = {"network.management": ["admin"]}
        self.admin_error: Exception | None = None
        self.write_error: Exception | None = None
        self.writes: list[tuple[str, str]] = []
        self.speedtests: list[str] = []
        self.close_with_error = False
        self.detached = 0
        # Set to hold a poll's device request until the test releases it
        self.devices_gate: asyncio.Event | None = None
        self.listen_error: Exception | None = None
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        self.listening = asyncio.Event()
        self.listen_calls = 0
        self.created_with: list[tuple[str, str, str]] = []

    def set_gateway(self, raw: dict[str, Any]) -> None:
        """Replace the gateway's runtime object."""
        self.devices[0] = raw

    async def get_devices(self) -> list[dict[str, Any]]:
        if self.devices_gate is not None:
            await self.devices_gate.wait()
        if self.error:
            raise self.error
        return copy.deepcopy(self.devices)

    async def get_gateway(self, mac: str | None = None) -> dict[str, Any] | None:
        found = find_gateway(await self.get_devices(), mac)
        return copy.deepcopy(found) if found else None

    async def get_active_clients(self) -> list[dict[str, Any]]:
        if self.clients_error:
            raise self.clients_error
        return copy.deepcopy(self.clients)

    async def get_wan_configs(self) -> list[dict[str, Any]]:
        if self.error:
            raise self.error
        return copy.deepcopy(self.configs)

    def detach(self) -> None:
        self.detached += 1

    async def can_manage_network(self) -> bool:
        if self.admin_error:
            raise self.admin_error
        if self.error:
            raise self.error
        return "admin" in self.permissions.get("network.management", [])

    async def set_wan_type(self, network_id: str, wan_type: str) -> str:
        if self.write_error:
            raise self.write_error
        self.writes.append((network_id, wan_type))
        for config in self.configs:
            if config.get("_id") == network_id:
                config["wan_type"] = wan_type
                return wan_type
        return ""

    async def start_speedtest(self, mac: str) -> None:
        if self.write_error:
            raise self.write_error
        self.speedtests.append(mac)

    async def listen(
        self,
        on_message: Callable[[dict[str, Any]], None],
        on_connect: Callable[[], None] | None = None,
    ) -> None:
        self.listen_calls += 1
        if self.listen_error:
            raise self.listen_error
        self.listening.set()
        try:
            if on_connect is not None:
                on_connect()
            while (message := await self.messages.get()) is not CLOSE:
                on_message(message)
        finally:
            self.listening.clear()
        if self.close_with_error:
            raise NetworkError("connection dropped")

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
def admin_entry() -> MockConfigEntry:
    """A configured console with the admin opt-in."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Gateway",
        unique_id=MAC,
        data={**ENTRY_DATA, **ADMIN_DATA},
    )


@pytest.fixture
async def setup_admin_entry(
    hass: HomeAssistant, console: FakeConsole, admin_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration with admin credentials."""
    admin_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(admin_entry.entry_id)
    await hass.async_block_till_done()
    return admin_entry


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration with the simulated console."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
