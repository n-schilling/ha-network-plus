"""The certificate check against a real TLS server on this machine.

The console makes its own certificate; the client takes it only pinned by
fingerprint, or a certificate the system trusts. Nothing is sent before the
certificate is accepted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import hashlib
import ipaddress
import ssl
from typing import Any

from aiohttp import ClientSession, DummyCookieJar, TCPConnector, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from custom_components.network_plus.api import (
    NetworkCertificateError,
    NetworkClient,
    NetworkError,
    format_fingerprint,
    normalize_fingerprint,
)

from .conftest import gateway


def _certificate(tmp_path: Any, name: str) -> tuple[ssl.SSLContext, str]:
    """A self-signed certificate as a console makes one; the server context and its fingerprint."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
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
    cert_file = tmp_path / f"{name}.pem"
    key_file = tmp_path / f"{name}.key"
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
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return context, digest


class Console:
    """A console that answers the login and the device list over TLS."""

    def __init__(self) -> None:
        self.logins = 0

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/auth/login", self._login)
        app.router.add_get("/proxy/network/api/s/default/stat/device", self._devices)
        app.router.add_get("/", self._root)
        return app

    async def _login(self, request: web.Request) -> web.Response:
        self.logins += 1
        response = web.json_response({})
        response.set_cookie("TOKEN", "token")
        return response

    async def _devices(self, request: web.Request) -> web.Response:
        return web.json_response({"data": [gateway()]})

    async def _root(self, request: web.Request) -> web.Response:
        return web.Response(text="UniFi OS")


@pytest.fixture
async def server(
    tmp_path: Any, socket_enabled: None
) -> AsyncIterator[tuple[str, str, Console, Any]]:
    """The console on 127.0.0.1 with its own certificate: host, fingerprint, console, tmp."""
    context, digest = _certificate(tmp_path, "console")
    console = Console()
    runner = web.AppRunner(console.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    port = runner.addresses[0][1]
    yield f"127.0.0.1:{port}", digest, console, tmp_path
    await runner.cleanup()


@pytest.fixture
async def session() -> AsyncIterator[ClientSession]:
    """A session as the integration creates it, without cookies."""
    async with ClientSession(cookie_jar=DummyCookieJar()) as session:
        yield session


async def test_own_certificate_is_not_trusted(
    server: tuple[str, str, Console, Any], session: ClientSession
) -> None:
    """Without a pin the console's own certificate fails, before any login."""
    host, digest, console, _ = server
    client = NetworkClient(session, host, "user", "pass")
    assert await client.untrusted_fingerprint() == digest
    with pytest.raises(NetworkCertificateError) as err:
        await client.get_devices()
    assert err.value.fingerprint is None
    assert console.logins == 0


async def test_trusted_certificate_needs_no_pin(server: tuple[str, str, Console, Any]) -> None:
    """A certificate with a chain the system trusts is taken without a pin."""
    host, _, _, tmp_path = server
    trusting = ssl.create_default_context(cafile=str(tmp_path / "console.pem"))
    async with ClientSession(
        cookie_jar=DummyCookieJar(), connector=TCPConnector(ssl=trusting)
    ) as session:
        client = NetworkClient(session, host, "user", "pass")
        assert await client.untrusted_fingerprint() is None
        assert (await client.get_devices())[0]["mac"] == gateway()["mac"]


async def test_pinned_certificate_is_taken(
    server: tuple[str, str, Console, Any], session: ClientSession
) -> None:
    """With the pin of its certificate the console is read as usual."""
    host, digest, console, _ = server
    client = NetworkClient(session, host, "user", "pass", fingerprint=digest)
    devices = await client.get_devices()
    assert devices[0]["mac"] == gateway()["mac"]
    assert console.logins == 1
    assert await client.get_fingerprint() == digest


async def test_other_certificate_is_refused(
    server: tuple[str, str, Console, Any], session: ClientSession
) -> None:
    """Another certificate than the pinned one is refused before the credentials go out."""
    host, digest, console, _ = server
    client = NetworkClient(session, host, "user", "pass", fingerprint="00" * 32)
    with pytest.raises(NetworkCertificateError) as err:
        await client.get_devices()
    assert err.value.fingerprint == digest
    assert console.logins == 0


async def test_unreachable(session: ClientSession, socket_enabled: None) -> None:
    """A console that does not answer is no certificate problem."""
    client = NetworkClient(session, "127.0.0.1:9", "user", "pass")
    with pytest.raises(NetworkError) as err:
        await client.untrusted_fingerprint()
    assert not isinstance(err.value, NetworkCertificateError)
    with pytest.raises(NetworkError):
        await client.get_fingerprint()


def test_fingerprint_formats() -> None:
    """Fingerprints are entered as browsers show them and kept as plain hex."""
    digest = "ab" * 32
    shown = format_fingerprint(digest)
    assert shown == ":".join(["AB"] * 32)
    assert normalize_fingerprint(shown) == digest
    assert normalize_fingerprint(f" {digest.upper()} ") == digest
    assert normalize_fingerprint("ab cd") == ""
    assert normalize_fingerprint("zz" * 32) == ""
