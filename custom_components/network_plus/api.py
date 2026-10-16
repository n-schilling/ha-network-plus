"""Local client for the UniFi Network application on a UniFi OS console.

The endpoints are the ones the console's own web interface uses; they are not
a published API. Every request goes through ``_request`` so a changed
interface fails in one place with a clear error.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import Any

from aiohttp import (
    ClientError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    WSMsgType,
    WSServerHandshakeError,
)

from .const import SITE
from .model import normalize_mac

TIMEOUT = ClientTimeout(total=15)
# Seconds between websocket pings; a silent connection is noticed after twice that.
HEARTBEAT = 30
# The console protects writes with a token it hands out at login and with answers
CSRF_HEADER = "x-csrf-token"
# The permission a user needs to change the network configuration
NETWORK_ADMIN = ("network.management", "admin")

# Device types of gateways in the UniFi Network application
GATEWAY_TYPES = frozenset({"udm", "ugw", "uxg"})


class NetworkError(Exception):
    """The console could not be reached or answered with an error."""


class NetworkAuthError(NetworkError):
    """The console rejected the user name or password."""


class NetworkPermissionError(NetworkError):
    """The console refused a write: the user may not change the configuration."""


class NetworkClient:
    """Read the gateway and its uplinks; one login session per client."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        username: str,
        password: str,
        site: str = SITE,
    ) -> None:
        """Initialize the client; ``session`` needs a cookie jar that accepts IP hosts."""
        self._session = session
        self._host = host
        self._username = username
        self._password = password
        self._site = site
        self._logged_in = False
        self._login_lock = asyncio.Lock()
        # Counts successful logins, so concurrent callers log in only once
        self._login_generation = 0
        self._csrf = ""

    @property
    def _base(self) -> str:
        return f"https://{self._host}"

    async def login(self, seen_generation: int | None = None) -> None:
        """Log in; the console keeps the session in a cookie.

        A caller that found the session rejected passes the login generation
        it saw; if another caller logged in meanwhile, nothing is done.
        """
        async with self._login_lock:
            if seen_generation is not None and seen_generation != self._login_generation:
                return
            try:
                async with self._session.post(
                    f"{self._base}/api/auth/login",
                    json={"username": self._username, "password": self._password},
                    timeout=TIMEOUT,
                ) as resp:
                    if resp.status in (400, 401, 403):
                        # The console answers wrong credentials with 403 and an
                        # error code; 400 and 403 without it mean something else,
                        # for example a console that is still starting
                        code = await _error_code(resp)
                        if resp.status == 401 or code.startswith("AUTHENTICATION_FAILED"):
                            raise NetworkAuthError(f"login rejected ({code or resp.status})")
                        detail = f" {code}" if code else ""
                        raise NetworkError(f"login refused (HTTP {resp.status}{detail})")
                    _raise_for_status(resp)
                    self._remember_csrf(resp)
            except (ClientError, TimeoutError) as err:
                raise NetworkError(_describe(err)) from err
            self._logged_in = True
            self._login_generation += 1

    def _session_rejected(self, generation: int) -> None:
        """Note an expired session, unless another caller renewed it meanwhile."""
        if self._login_generation == generation:
            self._logged_in = False

    def _remember_csrf(self, resp: ClientResponse) -> None:
        if token := resp.headers.get(CSRF_HEADER):
            self._csrf = token

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        """Send a request for a JSON document; log in first and once more when the session expired."""
        for attempt in range(2):
            if not self._logged_in:
                await self.login(self._login_generation)
            generation = self._login_generation
            headers = {CSRF_HEADER: self._csrf} if method != "GET" else {}
            try:
                async with self._session.request(
                    method, f"{self._base}{path}", json=body, headers=headers, timeout=TIMEOUT
                ) as resp:
                    if resp.status == 401:
                        self._session_rejected(generation)
                        if attempt == 0:
                            continue
                        raise NetworkAuthError("session not accepted after a new login")
                    if resp.status == 403 and method != "GET":
                        raise NetworkPermissionError(f"{path}: the user may not write (HTTP 403)")
                    _raise_for_status(resp)
                    self._remember_csrf(resp)
                    try:
                        return await resp.json(content_type=None)
                    except ValueError as err:
                        raise NetworkError(f"{path}: no JSON in the answer") from err
            except (ClientError, TimeoutError) as err:
                raise NetworkError(_describe(err)) from err
        raise NetworkError("unreachable")  # pragma: no cover

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return the runtime objects of all devices of the site."""
        body = await self._get(f"/proxy/network/api/s/{self._site}/stat/device")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise NetworkError("stat/device: unexpected answer")
        return [d for d in data if isinstance(d, dict)]

    async def get_gateway(self, mac: str | None = None) -> dict[str, Any] | None:
        """Return the gateway's runtime object, the one with ``mac`` if given."""
        return find_gateway(await self.get_devices(), mac)

    async def get_wan_configs(self) -> list[dict[str, Any]]:
        """Return the configured uplinks (name, network group, failover priority, WAN type)."""
        body = await self._get(
            f"/proxy/network/v2/api/site/{self._site}/wan/enriched-configuration"
        )
        if not isinstance(body, list):
            raise NetworkError("wan/enriched-configuration: unexpected answer")
        configs = (w.get("configuration") for w in body if isinstance(w, dict))
        return [c for c in configs if isinstance(c, dict)]

    async def get_active_clients(self) -> list[dict[str, Any]]:
        """Return the active clients, Teleport and VPN clients included."""
        body = await self._get(
            f"/proxy/network/v2/api/site/{self._site}/clients/active?includeTeleport=true"
        )
        if not isinstance(body, list):
            raise NetworkError("clients/active: unexpected answer")
        return [c for c in body if isinstance(c, dict)]

    async def can_manage_network(self) -> bool:
        """Return whether the user may change the network configuration."""
        body = await self._get("/api/users/self")
        permissions = body.get("permissions") if isinstance(body, dict) else None
        if not isinstance(permissions, dict):
            raise NetworkError("users/self: unexpected answer")
        area, level = NETWORK_ADMIN
        granted = permissions.get(area)
        return isinstance(granted, list) and level in granted

    async def set_wan_type(self, network_id: str, wan_type: str) -> str:
        """Set the WAN type of an uplink network and return the type the console kept.

        The console wants the whole network object back, so it is read first;
        its answer to the write carries only ``meta.rc``, so the network is
        read once more to see what it kept.
        """
        path = f"/proxy/network/api/s/{self._site}/rest/networkconf/{network_id}"
        network = await self._get_network(path, network_id)
        answer = await self._request("PUT", path, {**network, "wan_type": wan_type})
        meta = answer.get("meta") if isinstance(answer, dict) else None
        if not isinstance(meta, dict) or meta.get("rc") != "ok":
            detail = meta.get("msg") if isinstance(meta, dict) else "unexpected answer"
            raise NetworkError(f"networkconf {network_id}: write refused ({detail})")
        return str((await self._get_network(path, network_id)).get("wan_type") or "")

    async def _get_network(self, path: str, network_id: str) -> dict[str, Any]:
        body = await self._get(path)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise NetworkError(f"networkconf {network_id}: unexpected answer")
        return data[0]

    def detach(self) -> None:
        """Give the session's connector back; for a client that is no longer needed."""
        self._session.detach()

    async def listen(
        self,
        on_message: Callable[[dict[str, Any]], None],
        on_connect: Callable[[], None] | None = None,
    ) -> None:
        """Pass every websocket message to ``on_message`` until the connection ends.

        ``on_connect`` is called once the connection stands. Returns normally
        when an established connection closes; raises ``NetworkError`` when no
        connection could be made.
        """
        if not self._logged_in:
            await self.login(self._login_generation)
        generation = self._login_generation
        url = f"wss://{self._host}/proxy/network/wss/s/{self._site}/events?clients=v2"
        try:
            async with self._session.ws_connect(url, heartbeat=HEARTBEAT) as ws:
                if on_connect is not None:
                    on_connect()
                async for msg in ws:
                    if msg.type is not WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        continue
                    if isinstance(data, dict):
                        on_message(data)
        except WSServerHandshakeError as err:
            if err.status == 401:
                self._session_rejected(generation)
            raise NetworkError(f"websocket refused (HTTP {err.status})") from err
        except (ClientError, TimeoutError) as err:
            raise NetworkError(_describe(err)) from err


def find_gateway(devices: list[dict[str, Any]], mac: str | None = None) -> dict[str, Any] | None:
    """Return the gateway among the devices, the one with ``mac`` if given."""
    for device in devices:
        if device.get("type") not in GATEWAY_TYPES:
            continue
        if mac is None or normalize_mac(device.get("mac")) == normalize_mac(mac):
            return device
    return None


def is_lte_device(device: dict[str, Any]) -> bool:
    """Return whether the runtime object is an LTE backup device."""
    return "lte_state" in device or "lte_connected" in device


def find_lte_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the LTE backup devices among the devices."""
    return [d for d in devices if is_lte_device(d)]


async def _error_code(resp: ClientResponse) -> str:
    """Return the error code of the console's answer, "" if there is none."""
    try:
        body = await resp.json(content_type=None)
    except ValueError:
        return ""
    return str(body.get("code") or "") if isinstance(body, dict) else ""


def _raise_for_status(resp: ClientResponse) -> None:
    if resp.status >= 400:
        raise NetworkError(f"{resp.url.path}: HTTP {resp.status}")


def _describe(err: BaseException) -> str:
    return str(err) or type(err).__name__
