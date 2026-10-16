"""Tests for the client, against a fake aiohttp session."""

from __future__ import annotations

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
    find_gateway,
    find_lte,
    is_lte_device,
)

from .conftest import HOST, MAC, gateway, lte_device


class FakeResponse:
    """An aiohttp response with a status and a JSON body."""

    def __init__(self, status: int, body: Any = None, path: str = "/") -> None:
        self.status = status
        self._body = body
        self.url = MagicMock(path=path)

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


class FakeSession:
    """Answers requests from queues; records what was asked."""

    def __init__(self) -> None:
        self.login: list[FakeResponse | Exception] = []
        self.gets: dict[str, list[FakeResponse | Exception]] = {}
        self.ws: FakeWebSocket | Exception = FakeWebSocket([])
        self.requests: list[tuple[str, str]] = []
        self.ws_urls: list[str] = []

    @staticmethod
    @asynccontextmanager
    async def _answer(item: FakeResponse | Exception) -> AsyncIterator[FakeResponse]:
        if isinstance(item, Exception):
            raise item
        yield item

    def post(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(("POST", url))
        assert kwargs["json"] == {"username": "user", "password": "pass"}
        return self._answer(self.login.pop(0))

    def get(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(("GET", url))
        path = url.removeprefix(f"https://{HOST}")
        return self._answer(self.gets[path].pop(0))

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


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_login_rejected(status: int) -> None:
    """A rejected login is an authentication error."""
    session = FakeSession()
    session.login = [FakeResponse(status)]
    with pytest.raises(NetworkAuthError):
        await _client(session).login()


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
    await _client(session).listen(received.append)
    assert received == [message]
    assert session.ws_urls == [f"wss://{HOST}/proxy/network/wss/s/default/events?clients=v2"]


async def test_listen_refused() -> None:
    """A refused websocket is a network error; 401 makes the next call log in."""
    session = FakeSession()
    session.login = [FakeResponse(200), FakeResponse(200)]
    session.ws = WSServerHandshakeError(MagicMock(), (), status=401)
    client = _client(session)
    with pytest.raises(NetworkError, match="HTTP 401"):
        await client.listen(lambda _m: None)
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


def test_find_lte() -> None:
    """The LTE device is the one with LTE fields."""
    devices = [{"type": "usw"}, gateway(), lte_device()]
    assert find_lte(devices) is devices[2]
    assert find_lte(devices[:2]) is None
    assert is_lte_device({"lte_connected": True})
    assert not is_lte_device(gateway())
