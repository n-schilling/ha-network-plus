"""A made-up UniFi OS console for the page checks: HTTPS on 127.0.0.1, its own certificate.

It answers the requests Network Plus makes (login, devices, uplink
configuration, clients, rights, writes, websocket) with made-up data in the
shape the console sends. Nothing in it comes from a real console.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import ipaddress
from pathlib import Path
import ssl
import time
from typing import Any

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

USERNAME = "homeassistant"
PASSWORD = "panel-check-console"
ADMIN_USERNAME = "netadmin"
ADMIN_PASSWORD = "panel-check-console-admin"
SITE = "/proxy/network/api/s/default"
SITE_V2 = "/proxy/network/v2/api/site/default"


def certificate(directory: Path, name: str) -> tuple[ssl.SSLContext, str]:
    """A self-signed certificate for 127.0.0.1, as a console makes one; context and fingerprint."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UniFi OS")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file = directory / f"{name}.pem"
    key_file = directory / f"{name}.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert_file, key_file)
    return context, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


class FakeConsole:
    """One console with a gateway, a cable uplink and an LTE backup, all made up."""

    def __init__(self, name: str, mac: str, lte_mac: str, ipv4: str, ipv6: str) -> None:
        self.name = name
        self.mac = mac
        self.lte_mac = lte_mac
        self.ipv4 = ipv4
        self.ipv6 = ipv6
        self.wan_type = "dhcp"
        self.port = 0
        self.fingerprint = ""
        self._runner: web.AppRunner | None = None

    # The runtime objects, in the shape stat/device and the websocket send

    def gateway(self) -> dict[str, Any]:
        now = int(time.time())
        return {
            "mac": self.mac,
            "type": "udm",
            "model": "UDMPRO",
            "name": self.name,
            "version": "4.0.0",
            "last_wan_status": {
                "WAN": "online" if self.wan_type != "disabled" else "disabled",
                "WAN_LTE_FAILOVER": "online",
            },
            "last_wan_interfaces": {"WAN": {"ip": self.ipv4, "alive": True}},
            "wan1": {
                "ifname": "eth8",
                "ipv6": ["fe80::1", self.ipv6],
                "ip": self.ipv4,
                "speed": 2500,
                "full_duplex": True,
                "up": True,
                "max_speed": 10000,
            },
            "uplink": {
                "comment": "WAN",
                "name": "eth8",
                "latency": 12,
                "speedtest_lastrun": now - 3 * 3600,
                "speedtest_ping": 9,
                "speedtest_status": "Success",
                "xput_down": 912.4,
                "xput_up": 48.7,
            },
            "uptime_stats": {
                "WAN": {"availability": 99.97, "latency_average": 12},
                "WAN_LTE_FAILOVER": {"availability": 99.8, "latency_average": 48},
            },
            "speedtest-status": {
                "interface_name": "eth8",
                "source_interface": "if!eth8",
                "rundate": now - 3 * 3600,
                "runtime": 26,
                "status_download": 2,
                "status_ping": 2,
                "status_summary": 2,
                "status_upload": 2,
                "xput_download": 912.4,
                "xput_upload": 48.7,
                "latency": 9,
            },
            "last_lte_failover_transition_timestamp": (now - 6 * 86400) * 1000,
        }

    def lte(self) -> dict[str, Any]:
        return {
            "mac": self.lte_mac,
            "type": "uap",
            "model": "ULTEPRO",
            "name": "LTE backup",
            "version": "6.0.0",
            "lte_state": "ready",
            "lte_connected": "yes",
            "lte_failover": False,
            "rx_bytes": 1_610_000_000,
            "tx_bytes": 230_000_000,
            "lte_hard_limit": 10_000_000_000,
            "lte_soft_limit": 8_000_000_000,
            "lte_data_limit_enabled": True,
            "lte_rssi": "-71",
            "lte_rsrp": "-98",
            "lte_is_sim_pin_required": False,
            "lte_is_sim_pin_verified": True,
            "lte_sim_pin_tries_left": 3,
        }

    def configs(self) -> list[dict[str, Any]]:
        return [
            {
                "_id": "net-wan",
                "name": "WAN",
                "wan_networkgroup": "WAN",
                "wan_failover_priority": 1,
                "wan_type": self.wan_type,
            },
            {
                "_id": "net-lte",
                "name": "LTE Failover WAN",
                "wan_networkgroup": "WAN_LTE_FAILOVER",
                "wan_failover_priority": -1,
                "wan_type": "static",
            },
        ]

    @staticmethod
    def clients() -> list[dict[str, Any]]:
        return [
            {"type": "WIRED", "display_name": "Desk", "id": "c-desk"},
            {
                "type": "TELEPORT",
                "id": "c-tablet",
                "display_name": "Travel tablet",
                "assoc_time": int(time.time()) - 42 * 60,
                "status": "online",
            },
        ]

    # The server

    async def start(self, directory: Path) -> None:
        context, self.fingerprint = certificate(directory, self.name.lower())
        app = web.Application()
        app.router.add_get("/", self._root)
        app.router.add_post("/api/auth/login", self._login)
        app.router.add_get("/api/users/self", self._self)
        app.router.add_get(f"{SITE}/stat/device", self._devices)
        app.router.add_get(f"{SITE}/stat/sysinfo", self._sysinfo)
        app.router.add_get(f"{SITE}/rest/networkconf/{{id}}", self._network)
        app.router.add_put(f"{SITE}/rest/networkconf/{{id}}", self._write_network)
        app.router.add_post(f"{SITE}/cmd/devmgr", self._command)
        app.router.add_get(f"{SITE_V2}/wan/enriched-configuration", self._wans)
        app.router.add_get(f"{SITE_V2}/clients/active", self._clients)
        app.router.add_get("/proxy/network/wss/s/default/events", self._websocket)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=context)
        await site.start()
        self.port = int(self._runner.addresses[0][1])

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    @staticmethod
    def _authorized(request: web.Request) -> bool:
        return request.cookies.get("TOKEN", "").startswith("token-")

    async def _root(self, request: web.Request) -> web.Response:
        return web.Response(text="UniFi OS")

    async def _login(self, request: web.Request) -> web.Response:
        body = await request.json()
        user = (body.get("username"), body.get("password"))
        if user not in ((USERNAME, PASSWORD), (ADMIN_USERNAME, ADMIN_PASSWORD)):
            return web.json_response(
                {"code": "AUTHENTICATION_FAILED_INVALID_CREDENTIALS"}, status=403
            )
        response = web.json_response({}, headers={"x-csrf-token": "csrf"})
        response.set_cookie("TOKEN", f"token-{user[0]}")
        return response

    async def _self(self, request: web.Request) -> web.Response:
        admin = request.cookies.get("TOKEN") == f"token-{ADMIN_USERNAME}"
        level = "admin" if admin else "readonly"
        return web.json_response({"permissions": {"network.management": [level]}})

    async def _devices(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401)
        return web.json_response({"data": [self.gateway(), self.lte()]})

    async def _sysinfo(self, request: web.Request) -> web.Response:
        return web.json_response({"data": [{"timezone": "Europe/Berlin"}]})

    async def _network(self, request: web.Request) -> web.Response:
        config = next(c for c in self.configs() if c["_id"] == request.match_info["id"])
        return web.json_response({"data": [config]})

    async def _write_network(self, request: web.Request) -> web.Response:
        if request.cookies.get("TOKEN") != f"token-{ADMIN_USERNAME}":
            return web.Response(status=403)
        if request.match_info["id"] == "net-wan":
            self.wan_type = str((await request.json()).get("wan_type"))
        return web.json_response({"meta": {"rc": "ok"}, "data": []})

    async def _command(self, request: web.Request) -> web.Response:
        if request.cookies.get("TOKEN") != f"token-{ADMIN_USERNAME}":
            return web.Response(status=403)
        return web.json_response({"meta": {"rc": "ok"}, "data": []})

    async def _wans(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401)
        return web.json_response([{"configuration": c} for c in self.configs()])

    async def _clients(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.Response(status=401)
        return web.json_response(self.clients())

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        async for _ in ws:
            pass
        return ws
