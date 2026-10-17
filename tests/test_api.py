"""Tests for the client, against a fake aiohttp session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from typing import Any
from unittest.mock import MagicMock

from aiohttp import ClientError, WSMsgType, WSServerHandshakeError
import pytest

from custom_components.network_plus.api import (
    NetworkAuthError,
    NetworkClient,
    NetworkError,
    NetworkPermissionError,
    find_gateway,
    find_lte_devices,
    is_lte_device,
)

from .conftest import HOST, MAC, gateway, lte_device


class FakeResponse:
    """An aiohttp response with a status, a JSON body and headers."""

    def __init__(
        self, status: int, body: Any = None, path: str = "/", headers: dict[str, str] | None = None
    ) -> None:
        self.status = status
        self._body = body
        self.url = MagicMock(path=path)
        self.headers = headers or {}

    async def json(self, content_type: str | None = None) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeWebSocket:
    """A websocket that delivers a fixed list of messages."""

    def __init__(self, messages: list[tuple[WSMsgType, str]]) -> None:
        self._messages = messages

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        for kind, data in self._messages:
            yield MagicMock(type=kind, data=data)


class FakeCookieJar:
    """Holds cookie names and records what was cleared."""

    def __init__(self) -> None:
        self.names = {"TOKEN", "other"}
        self.cleared = 0

    def clear(self, predicate: Any) -> None:
        self.cleared += 1
        self.names = {name for name in self.names if not predicate(MagicMock(key=name))}


class FakeSession:
    """Answers requests from queues; records what was asked."""

    def __init__(self) -> None:
        self.login: list[FakeResponse | Exception] = []
        self.gets: dict[str, list[FakeResponse | Exception]] = {}
        self.puts: dict[str, list[FakeResponse | Exception]] = {}
        self.writes: list[tuple[str, str, Any, dict[str, str]]] = []
        self.ws: FakeWebSocket | Exception = FakeWebSocket([])
        self.requests: list[tuple[str, str]] = []
        self.ws_urls: list[str] = []
        self.detached = False
        self.cookie_jar = FakeCookieJar()

    def detach(self) -> None:
        self.detached = True

    @staticmethod
    @asynccontextmanager
    async def _answer(item: FakeResponse | Exception) -> AsyncIterator[FakeResponse]:
        await asyncio.sleep(0)  # a real request yields to the loop
        if isinstance(item, Exception):
            raise item
        yield item

    def post(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(("POST", url))
        assert kwargs["json"] == {"username": "user", "password": "pass"}
        return self._answer(self.login.pop(0))

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.requests.append((method, url))
        path = url.removeprefix(f"https://{HOST}")
        if method == "GET":
            return self._answer(self.gets[path].pop(0))
        self.writes.append((method, url, kwargs.get("json"), kwargs.get("headers") or {}))
        return self._answer(self.puts[path].pop(0))

    @asynccontextmanager
    async def ws_connect(self, url: str, **kwargs: Any) -> AsyncIterator[FakeWebSocket]:
        self.ws_urls.append(url)
        if isinstance(self.ws, Exception):
            raise self.ws
        yield self.ws


DEVICES = "/proxy/network/api/s/default/stat/device"
WANS = "/proxy/network/v2/api/site/default/wan/enriched-configuration"
CLIENTS = "/proxy/network/v2/api/site/default/clients/active?includeTeleport=true"


def _client(session: FakeSession) -> NetworkClient:
    return NetworkClient(session, HOST, "user", "pass")  # type: ignore[arg-type]


async def test_get_gateway_logs_in_once() -> None:
    """The first request logs in; later requests reuse the session."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    switch = {"mac": "02:00:00:00:00:02", "type": "usw"}
    body = {"data": [switch, gateway(), "junk"]}
    session.gets[DEVICES] = [FakeResponse(200, body), FakeResponse(200, body)]
    client = _client(session)
    assert (await client.get_gateway())["mac"] == MAC  # type: ignore[index]
    assert await client.get_gateway("02:00:00:00:00:99") is None
    assert [r[0] for r in session.requests] == ["POST", "GET", "GET"]
    assert session.requests[0][1] == f"https://{HOST}/api/auth/login"


async def test_session_expired_logs_in_again() -> None:
    """A 401 logs in again and repeats the request once."""
    session = FakeSession()
    session.login = [FakeResponse(200), FakeResponse(200)]
    session.gets[DEVICES] = [FakeResponse(401), FakeResponse(200, {"data": []})]
    assert await _client(session).get_devices() == []
    assert [r[0] for r in session.requests] == ["POST", "GET", "POST", "GET"]


async def test_session_refused_twice() -> None:
    """A 401 right after a new login is an authentication error."""
    session = FakeSession()
    session.login = [FakeResponse(200), FakeResponse(200)]
    session.gets[DEVICES] = [FakeResponse(401), FakeResponse(401)]
    with pytest.raises(NetworkAuthError):
        await _client(session).get_devices()


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, None),
        (403, {"code": "AUTHENTICATION_FAILED_INVALID_CREDENTIALS", "message": "Invalid"}),
        (403, {"code": "AUTHENTICATION_FAILED_ACCOUNT_LOCKED"}),
    ],
)
async def test_login_rejected(status: int, body: Any) -> None:
    """A rejected login is an authentication error."""
    session = FakeSession()
    session.login = [FakeResponse(status, body)]
    with pytest.raises(NetworkAuthError):
        await _client(session).login()


async def test_login_locked_out_is_no_auth_error() -> None:
    """Too many attempts lock the console for a while; asking for the password would not help."""
    session = FakeSession()
    session.login = [FakeResponse(403, {"code": "AUTHENTICATION_FAILED_LIMIT_REACHED"})]
    with pytest.raises(NetworkError, match="for now") as err:
        await _client(session).login()
    assert not isinstance(err.value, NetworkAuthError)


async def test_login_drops_the_old_token() -> None:
    """The session cookie from before is cleared, so the login carries no expired token."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    await _client(session).login()
    assert session.cookie_jar.cleared == 1
    assert session.cookie_jar.names == {"other"}


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, {"code": "API_ERROR"}),
        (400, None),
        (403, ValueError("no json")),
        (403, ["junk"]),
    ],
)
async def test_login_refused_for_other_reasons(status: int, body: Any) -> None:
    """A 400 or 403 without the credentials code is a network error, not wrong credentials."""
    session = FakeSession()
    session.login = [FakeResponse(status, body, path="/api/auth/login")]
    with pytest.raises(NetworkError, match="refused") as err:
        await _client(session).login()
    assert not isinstance(err.value, NetworkAuthError)


async def test_login_errors() -> None:
    """Server errors and connection errors are network errors."""
    session = FakeSession()
    session.login = [FakeResponse(500, path="/api/auth/login"), ClientError(), TimeoutError()]
    client = _client(session)
    with pytest.raises(NetworkError, match="HTTP 500"):
        await client.login()
    with pytest.raises(NetworkError, match="ClientError"):
        await client.login()
    with pytest.raises(NetworkError, match="TimeoutError"):
        await client.login()


async def test_get_errors() -> None:
    """HTTP errors, broken JSON, odd answers and lost connections are network errors."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    session.gets[DEVICES] = [
        FakeResponse(500, path=DEVICES),
        FakeResponse(200, ValueError("no json")),
        FakeResponse(200, {"data": "x"}),
        FakeResponse(200, []),
        ClientError("gone"),
    ]
    session.gets[WANS] = [FakeResponse(200, {"data": []})]
    client = _client(session)
    with pytest.raises(NetworkError, match="HTTP 500"):
        await client.get_devices()
    with pytest.raises(NetworkError, match="no JSON"):
        await client.get_devices()
    with pytest.raises(NetworkError, match="unexpected"):
        await client.get_devices()
    with pytest.raises(NetworkError, match="unexpected"):
        await client.get_devices()
    with pytest.raises(NetworkError, match="gone"):
        await client.get_devices()
    with pytest.raises(NetworkError, match="unexpected"):
        await client.get_wan_configs()


async def test_get_wan_configs() -> None:
    """The configuration part of every uplink is returned."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    session.gets[WANS] = [
        FakeResponse(200, [{"configuration": {"name": "Cable"}}, {"details": {}}, "junk"])
    ]
    assert await _client(session).get_wan_configs() == [{"name": "Cable"}]


async def test_listen_passes_messages() -> None:
    """Text messages with a JSON object reach the callback; others are skipped."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    message = {"meta": {"message": "device:sync"}, "data": []}
    session.ws = FakeWebSocket(
        [
            (WSMsgType.TEXT, json.dumps(message)),
            (WSMsgType.TEXT, "not json"),
            (WSMsgType.TEXT, "[1]"),
            (WSMsgType.BINARY, "x"),
        ]
    )
    received: list[dict[str, Any]] = []
    connected: list[bool] = []
    await _client(session).listen(received.append, lambda: connected.append(True))
    assert received == [message]
    assert connected == [True]
    assert session.ws_urls == [f"wss://{HOST}/proxy/network/wss/s/default/events?clients=v2"]


async def test_listen_refused() -> None:
    """A refused websocket is a network error; 401 makes the next call log in."""
    session = FakeSession()
    session.login = [FakeResponse(200), FakeResponse(200)]
    session.ws = WSServerHandshakeError(MagicMock(), (), status=401)
    client = _client(session)
    connected: list[bool] = []
    with pytest.raises(NetworkError, match="HTTP 401"):
        await client.listen(lambda _m: None, lambda: connected.append(True))
    assert connected == []
    session.ws = ClientError("reset")
    with pytest.raises(NetworkError, match="reset"):
        await client.listen(lambda _m: None)
    assert [r[0] for r in session.requests] == ["POST", "POST"]


def test_find_gateway() -> None:
    """Only gateways count; the MAC is compared without case."""
    devices = [{"type": "usw", "mac": MAC}, gateway()]
    assert find_gateway(devices) is devices[1]
    assert find_gateway(devices, MAC.upper()) is devices[1]
    assert find_gateway([{"type": "usw"}]) is None


async def test_get_active_clients() -> None:
    """The clients come as a list; anything else is an error."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    session.gets[CLIENTS] = [FakeResponse(200, [{"type": "WIRED"}, "junk"]), FakeResponse(200, {})]
    client = _client(session)
    assert await client.get_active_clients() == [{"type": "WIRED"}]
    with pytest.raises(NetworkError, match="unexpected"):
        await client.get_active_clients()


def test_find_lte_devices() -> None:
    """The LTE devices are the ones with LTE fields."""
    devices = [{"type": "usw"}, gateway(), lte_device(), lte_device(mac="02:a1:b2:c3:d4:e7")]
    assert find_lte_devices(devices) == devices[2:]
    assert find_lte_devices(devices[:2]) == []
    assert is_lte_device({"lte_connected": True})
    assert not is_lte_device(gateway())


async def test_concurrent_callers_log_in_once() -> None:
    """Two requests that find the session rejected share one new login."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    session.gets[DEVICES] = [FakeResponse(200, {"data": []}), FakeResponse(200, {"data": []})]
    client = _client(session)
    assert await asyncio.gather(client.get_devices(), client.get_devices()) == [[], []]
    assert [r[0] for r in session.requests] == ["POST", "GET", "GET"]


def test_detach() -> None:
    """Detaching gives the session's connector back."""
    session = FakeSession()
    _client(session).detach()
    assert session.detached is True


async def test_late_rejection_does_not_log_in_again() -> None:
    """Two requests whose sessions are both rejected share one renewal."""
    session = FakeSession()
    session.login = [FakeResponse(200), FakeResponse(200)]
    session.gets[DEVICES] = [
        FakeResponse(401),
        FakeResponse(401),
        FakeResponse(200, {"data": []}),
        FakeResponse(200, {"data": []}),
    ]
    client = _client(session)
    assert await asyncio.gather(client.get_devices(), client.get_devices()) == [[], []]
    assert [r[0] for r in session.requests].count("POST") == 2


NETCONF = "/proxy/network/api/s/default/rest/networkconf/net-wan"
SELF = "/api/users/self"
DEVMGR = "/proxy/network/api/s/default/cmd/devmgr"


async def test_can_manage_network() -> None:
    """The admin role for the Network application allows writes; anything else does not."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    session.gets[SELF] = [
        FakeResponse(200, {"permissions": {"network.management": ["admin"]}}),
        FakeResponse(200, {"permissions": {"network.management": ["readonly"]}}),
        FakeResponse(200, {"permissions": {}}),
        FakeResponse(200, {"role": "admin"}),
    ]
    client = _client(session)
    assert await client.can_manage_network() is True
    assert await client.can_manage_network() is False
    assert await client.can_manage_network() is False
    with pytest.raises(NetworkError, match="unexpected"):
        await client.can_manage_network()


async def test_set_wan_type_sends_the_whole_network_with_the_token() -> None:
    """The network is read, changed and written back with the CSRF token of the login."""
    session = FakeSession()
    session.login = [FakeResponse(200, headers={"x-csrf-token": "tok-1"})]
    session.gets[NETCONF] = [
        FakeResponse(200, {"data": [{"_id": "net-wan", "name": "Cable", "wan_type": "dhcp"}]}),
        FakeResponse(200, {"data": [{"_id": "net-wan", "name": "Cable", "wan_type": "disabled"}]}),
    ]
    # The console answers a write with meta only; the network is read again
    session.puts[NETCONF] = [FakeResponse(200, {"meta": {"rc": "ok"}, "data": []})]
    client = _client(session)
    assert await client.set_wan_type("net-wan", "disabled") == "disabled"
    method, url, body, headers = session.writes[0]
    assert (method, url.endswith(NETCONF)) == ("PUT", True)
    assert body == {"_id": "net-wan", "name": "Cable", "wan_type": "disabled"}
    assert headers["x-csrf-token"] == "tok-1"


async def test_set_wan_type_errors() -> None:
    """Odd answers and a refused write are errors; a 403 names the missing rights."""
    session = FakeSession()
    session.login = [FakeResponse(200)]
    session.gets[NETCONF] = [
        FakeResponse(200, {"data": []}),
        FakeResponse(200, {"data": [{"_id": "net-wan"}]}),
        FakeResponse(200, {"data": [{"_id": "net-wan"}]}),
        FakeResponse(200, {"data": [{"_id": "net-wan"}]}),
    ]
    session.puts[NETCONF] = [
        FakeResponse(200, {"meta": {"rc": "error", "msg": "api.err.Invalid"}}),
        FakeResponse(200, ["junk"]),
        FakeResponse(403, path=NETCONF),
    ]
    client = _client(session)
    with pytest.raises(NetworkError, match="unexpected answer"):
        await client.set_wan_type("net-wan", "disabled")
    with pytest.raises(NetworkError, match=r"write refused \(api.err.Invalid\)"):
        await client.set_wan_type("net-wan", "disabled")
    with pytest.raises(NetworkError, match=r"write refused \(unexpected answer\)"):
        await client.set_wan_type("net-wan", "disabled")
    with pytest.raises(NetworkPermissionError):
        await client.set_wan_type("net-wan", "disabled")


async def test_start_speedtest() -> None:
    """The device manager gets the command with the gateway's MAC and the CSRF token."""
    session = FakeSession()
    session.login = [FakeResponse(200, headers={"x-csrf-token": "tok-2"})]
    session.puts[DEVMGR] = [
        FakeResponse(200, {"meta": {"rc": "ok"}, "data": []}),
        FakeResponse(200, {"meta": {"rc": "error", "msg": "api.err.SpeedtestInProgress"}}),
        FakeResponse(200, "junk"),
        FakeResponse(403, path=DEVMGR),
    ]
    client = _client(session)
    await client.start_speedtest(MAC)
    method, url, body, headers = session.writes[0]
    assert (method, url.endswith(DEVMGR)) == ("POST", True)
    assert body == {"mac": MAC, "cmd": "speedtest"}
    assert headers["x-csrf-token"] == "tok-2"
    with pytest.raises(NetworkError, match=r"not started \(api.err.SpeedtestInProgress\)"):
        await client.start_speedtest(MAC)
    with pytest.raises(NetworkError, match=r"not started \(unexpected answer\)"):
        await client.start_speedtest(MAC)
    with pytest.raises(NetworkPermissionError):
        await client.start_speedtest(MAC)
