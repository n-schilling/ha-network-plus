"""Check the page in a real browser against a throwaway Home Assistant.

Starts two made-up consoles (fake_console.py, HTTPS with their own
certificates) and Home Assistant in a temporary directory, both reachable from
this machine only (see run_hass.py). The consoles are added through the config
flow, the first with its certificate pinned and the admin opt-in; a made-up
history goes into the first one's store. Then Playwright drives the page and
the checks look at what it shows and who may do what. Home Assistant is stopped
through its own stop action at the end, also after a failure.

Usage (from the repository root, with the packages of requirements_panel.txt):

    python -I scripts/panel/check_panel.py [--screenshots DIR] [--keep]
        [--browser chrome|chromium] [--headed]

--screenshots writes the pictures of the README into DIR (docs/screenshots).
--keep leaves the temporary directory for a look at the log.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from playwright.async_api import Browser, Page, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_console import ADMIN_PASSWORD, ADMIN_USERNAME, PASSWORD, USERNAME, FakeConsole

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = ROOT / "custom_components" / "network_plus"
VERSION = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))["version"]
TIME_ZONE = ZoneInfo("Europe/Berlin")
# Accounts of the throwaway instance; it listens on 127.0.0.1 only and is deleted afterwards
ADMIN = ("admin", "panel-check-admin")
USER = ("user", "panel-check-user")
PANEL = "network-plus-panel"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _configuration(port: int) -> str:
    return f"""homeassistant:
  name: Home
  time_zone: {TIME_ZONE.key}
  unit_system: metric
  country: DE
  language: en
http:
  server_host: 127.0.0.1
  server_port: {port}
frontend:
config:
logger:
  default: warning
"""


def _history(now: datetime) -> dict[str, Any]:
    """Two months of made-up history: switches, outages, speed tests, drills, sessions."""
    rng = random.Random(11)
    wan = {"uplink": "WAN", "uplink_name": "WAN"}
    events: list[dict[str, Any]] = []
    speedtests: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []

    def iso(moment: datetime) -> str:
        return moment.astimezone(UTC).isoformat()

    def outage(start: datetime, minutes: float) -> None:
        back = start + timedelta(minutes=minutes)
        events.extend(
            [
                {"at": iso(start), "type": "uplink_down", **wan},
                {
                    "at": iso(start + timedelta(seconds=25)),
                    "type": "failover",
                    "from_uplink": "WAN",
                    "from_uplink_name": "WAN",
                    "to_uplink": "WAN_LTE_FAILOVER",
                    "to_uplink_name": "LTE Failover WAN",
                },
                {"at": iso(back), "type": "uplink_up", **wan},
                {
                    "at": iso(back + timedelta(seconds=2)),
                    "type": "restored",
                    "from_uplink": "WAN_LTE_FAILOVER",
                    "from_uplink_name": "LTE Failover WAN",
                    "to_uplink": "WAN",
                    "to_uplink_name": "WAN",
                },
            ]
        )

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    outage(today - timedelta(days=1) + timedelta(hours=7, minutes=42), 18.5)
    outage(today - timedelta(days=9) + timedelta(hours=23, minutes=5), 3.2)
    outage(today - timedelta(days=24) + timedelta(hours=14, minutes=10), 47)
    events.append(
        {
            "at": iso(today - timedelta(days=1) + timedelta(hours=3, minutes=2)),
            "type": "public_ip_changed",
            **wan,
            "family": "ipv4",
        }
    )
    events.append(
        {
            "at": iso(today - timedelta(days=1) + timedelta(hours=3, minutes=2, seconds=9)),
            "type": "public_ip_changed",
            **wan,
            "family": "ipv6",
        }
    )
    events.append(
        {
            "at": iso(today - timedelta(days=16) + timedelta(hours=11, minutes=27)),
            "type": "brief_failover",
        }
    )
    for day in range(60, 0, -2):
        at = today - timedelta(days=day) + timedelta(hours=4, minutes=rng.randint(0, 50))
        speedtests.append(
            {
                "at": iso(at),
                "uplink": "wan",
                "uplink_name": "WAN",
                "download": round(rng.uniform(880, 940), 1),
                "upload": round(rng.uniform(44, 51), 1),
                "ping": rng.randint(8, 13),
            }
        )
    for day in (52, 31, 10):
        at = today - timedelta(days=day) + timedelta(hours=10, minutes=15)
        speedtests.append(
            {
                "at": iso(at),
                "uplink": "wan_lte_failover",
                "uplink_name": "LTE Failover WAN",
                "download": round(rng.uniform(62, 118), 1),
                "upload": round(rng.uniform(18, 34), 1),
                "ping": rng.randint(31, 52),
            }
        )
    drills = [
        {
            "at": iso(today - timedelta(days=day) + timedelta(hours=10, minutes=14)),
            "failover_seconds": failover,
            "restore_seconds": restore,
            "data_used_bytes": used,
        }
        for day, failover, restore, used in (
            (52, 16.2, 14.8, 41_000_000),
            (31, 14.9, 15.3, 37_500_000),
            (10, 15.6, 14.1, 52_300_000),
        )
    ]
    for day, hour, minutes, name in (
        (3, 19, 95, "Travel tablet"),
        (12, 8, 22, "Laptop"),
        (27, 21, 140, "Travel tablet"),
    ):
        start = today - timedelta(days=day) + timedelta(hours=hour, minutes=6)
        sessions.append(
            {
                "at": iso(start),
                "until": iso(start + timedelta(minutes=minutes)),
                "type": "TELEPORT",
                "name": name,
            }
        )
    return {
        "version": 1,
        "minor_version": 1,
        "key": "",
        "data": {
            "events": events,
            "speedtests": speedtests,
            "drills": drills,
            "sessions": sessions,
        },
    }


class Api:
    """REST and WebSocket access to the throwaway instance."""

    def __init__(self, session: aiohttp.ClientSession, base: str) -> None:
        self.session = session
        self.base = base
        self.client_id = f"{base}/"

    async def login(self, username: str, password: str) -> str:
        """An access token for a user, through the login flow the page uses."""
        flow = await self._json(
            "post",
            "/auth/login_flow",
            json={
                "client_id": self.client_id,
                "handler": ["homeassistant", None],
                "redirect_uri": self.client_id,
            },
        )
        step = await self._json(
            "post",
            f"/auth/login_flow/{flow['flow_id']}",
            json={"client_id": self.client_id, "username": username, "password": password},
        )
        token = await self._json(
            "post",
            "/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": step["result"],
                "client_id": self.client_id,
            },
        )
        return str(token["access_token"])

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        async with self.session.request(method, self.base + path, **kwargs) as response:
            response.raise_for_status()
            return await response.json()

    async def post(self, token: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """A REST call as the user of the token."""
        headers = {"Authorization": f"Bearer {token}"}
        return await self._json("post", path, headers=headers, json=body or {})

    async def status(self, token: str, path: str, body: dict[str, Any]) -> int:
        """The HTTP status of a REST call as the user of the token."""
        headers = {"Authorization": f"Bearer {token}"}
        async with self.session.post(self.base + path, headers=headers, json=body) as response:
            return response.status

    async def ws(self, token: str, message: dict[str, Any]) -> dict[str, Any]:
        """One WebSocket command; the whole result message, success or not."""
        async with self.session.ws_connect(self.base + "/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            if (await ws.receive_json())["type"] != "auth_ok":
                raise RuntimeError("WebSocket login failed")
            await ws.send_json({"id": 1, **message})
            while True:
                reply = await ws.receive_json()
                if reply.get("id") == 1 and reply.get("type") == "result":
                    return dict(reply)


class Instance:
    """A throwaway Home Assistant with two made-up consoles."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.config = directory / "config"
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[bytes] | None = None
        self.token = ""
        self.entry_ids: dict[str, str] = {}
        self.consoles = [
            FakeConsole(
                "Gateway",
                "02:5e:00:00:00:01",
                "02:5e:00:00:00:02",
                "198.51.100.23",
                "2001:db8:4e2a::1",
            ),
            FakeConsole(
                "Office",
                "02:5e:00:00:01:01",
                "02:5e:00:00:01:02",
                "203.0.113.45",
                "2001:db8:77c1::1",
            ),
        ]
        self.flow_checks: list[str] = []

    def _launch(self) -> None:
        """Copy the integration and start Home Assistant in the background."""
        (self.config / "custom_components").mkdir(parents=True)
        shutil.copytree(
            INTEGRATION,
            self.config / "custom_components" / "network_plus",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (self.config / "configuration.yaml").write_text(_configuration(self.port))
        log = (self.directory / "hass.log").open("wb")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("run_hass.py")),
                "-c",
                str(self.config),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    async def start(self, session: aiohttp.ClientSession) -> Api:
        """Start the consoles and Home Assistant, onboard and add the consoles."""
        for console in self.consoles:
            await console.start(self.directory)
        await asyncio.to_thread(self._launch)
        api = Api(session, self.base)
        await self._wait_ready(session)
        self.token = await self._onboard(api)
        await self._add_consoles(api)
        await self._seed_history(api)
        await self._add_user(api)
        return api

    async def _wait_ready(self, session: aiohttp.ClientSession) -> None:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(f"Home Assistant ended, see {self.directory / 'hass.log'}")
            try:
                async with session.get(self.base + "/api/onboarding") as response:
                    if response.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(1)
        raise RuntimeError("Home Assistant did not start within 3 minutes")

    async def _onboard(self, api: Api) -> str:
        user = await api._json(
            "post",
            "/api/onboarding/users",
            json={
                "client_id": api.client_id,
                "name": "Sam",
                "username": ADMIN[0],
                "password": ADMIN[1],
                "language": "en",
            },
        )
        token = await api._json(
            "post",
            "/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": user["auth_code"],
                "client_id": api.client_id,
            },
        )
        access = str(token["access_token"])
        await api.post(access, "/api/onboarding/core_config")
        await api.post(access, "/api/onboarding/analytics")
        await api.post(
            access,
            "/api/onboarding/integration",
            {"client_id": api.client_id, "redirect_uri": api.client_id},
        )
        # The HTTP settings of configuration.yaml start as a trial that the page
        # asks to confirm and that reverts after five minutes; keep them
        promoted = await api.ws(access, {"type": "http/config/promote"})
        if not promoted["success"]:
            raise RuntimeError(f"HTTP settings not kept: {promoted}")
        return access

    async def _add_consoles(self, api: Api) -> None:
        """Add both consoles through the config flow, checking the certificate on the way.

        The first one gets its own certificate pinned and the admin opt-in;
        the second one is first refused with the check on, then added with
        the check off.
        """
        flows = "/api/config/config_entries/flow"
        gateway, office = self.consoles
        flow = await api.post(self.token, flows, {"handler": "network_plus"})
        url = f"{flows}/{flow['flow_id']}"
        step = await api.post(
            self.token,
            url,
            {
                "host": gateway.host,
                "username": USERNAME,
                "password": PASSWORD,
                "verify_ssl": True,
                "pin_certificate": True,
                "use_admin": True,
            },
        )
        if step.get("step_id") != "certificate":
            raise RuntimeError(f"no certificate step: {step}")
        shown = step["description_placeholders"]["fingerprint"]
        if shown.replace(":", "").lower() != gateway.fingerprint:
            raise RuntimeError(f"another fingerprint shown than the console's: {shown}")
        wrong = await api.post(self.token, url, {"certificate_fingerprint": "00" * 32})
        if wrong.get("errors") != {"base": "fingerprint_mismatch"}:
            raise RuntimeError(f"a wrong fingerprint was taken: {wrong}")
        step = await api.post(self.token, url, {"certificate_fingerprint": shown})
        if step.get("step_id") != "admin":
            raise RuntimeError(f"no admin step: {step}")
        step = await api.post(
            self.token,
            url,
            {"admin_username": ADMIN_USERNAME, "admin_password": ADMIN_PASSWORD},
        )
        if step.get("type") != "create_entry" or step["result"]["state"] != "loaded":
            raise RuntimeError(f"Gateway did not load: {step}")
        self.entry_ids[gateway.name] = step["result"]["entry_id"]
        self.flow_checks.append("pinning: the certificate step shows the console's fingerprint")
        self.flow_checks.append("pinning: a wrong fingerprint is refused")

        flow = await api.post(self.token, flows, {"handler": "network_plus"})
        url = f"{flows}/{flow['flow_id']}"
        office_data = {"host": office.host, "username": USERNAME, "password": PASSWORD}
        step = await api.post(self.token, url, {**office_data, "verify_ssl": True})
        if step.get("errors") != {"base": "certificate_not_trusted"}:
            raise RuntimeError(f"an untrusted certificate was taken: {step}")
        step = await api.post(self.token, url, {**office_data, "verify_ssl": False})
        if step.get("type") != "create_entry" or step["result"]["state"] != "loaded":
            raise RuntimeError(f"Office did not load: {step}")
        self.entry_ids[office.name] = step["result"]["entry_id"]
        self.flow_checks.append("without a pin an untrusted certificate is refused while checked")

    async def _seed_history(self, api: Api) -> None:
        """Put the made-up history into the first console's store while it is unloaded."""
        entry_id = self.entry_ids["Gateway"]
        for disabled_by in ("user", None):
            reply = await api.ws(
                self.token,
                {
                    "type": "config_entries/disable",
                    "entry_id": entry_id,
                    "disabled_by": disabled_by,
                },
            )
            if not reply["success"]:
                raise RuntimeError(f"entry not switched: {reply}")
            if disabled_by == "user":
                store = _history(datetime.now(TIME_ZONE))
                store["key"] = f"network_plus.history.{entry_id}"
                (self.config / ".storage" / store["key"]).write_text(json.dumps(store))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            entries = await api.ws(
                self.token, {"type": "config_entries/get", "domain": "network_plus"}
            )
            if all(e["state"] == "loaded" for e in entries["result"]):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("the console did not load again")

    async def _add_user(self, api: Api) -> None:
        """A user of the group Users: may control entities, is no administrator."""
        created = await api.ws(
            self.token,
            {"type": "config/auth/create", "name": "Visitor", "group_ids": ["system-users"]},
        )
        await api.ws(
            self.token,
            {
                "type": "config/auth_provider/homeassistant/create",
                "user_id": created["result"]["user"]["id"],
                "username": USER[0],
                "password": USER[1],
            },
        )

    async def stop(self, session: aiohttp.ClientSession) -> None:
        """Stop through Home Assistant's own stop action; force only if that fails."""
        if self.process is not None and self.process.poll() is None:
            try:
                if self.token:
                    async with session.post(
                        self.base + "/api/services/homeassistant/stop",
                        headers={"Authorization": f"Bearer {self.token}"},
                    ):
                        pass
            except aiohttp.ClientError:
                pass
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=30)
        for console in self.consoles:
            await console.stop()


@dataclass
class Checks:
    """The checks that ran and the ones that failed."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    before: Callable[[], Awaitable[None]] | None = None

    async def run(self, name: str, check: Callable[[], Awaitable[None]]) -> None:
        try:
            if self.before is not None:
                await self.before()
            await check()
        except Exception as err:
            self.failed.append(f"{name}: {err!r}")
            print(f"FAIL {name}: {err!r}")
        else:
            self.passed.append(name)
            print(f"ok   {name}")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _open(
    browser: Browser, instance: Instance, user: tuple[str, str], **options: Any
) -> Page:
    """A new page, logged in, on the Network Plus page."""
    context = await browser.new_context(
        viewport=options.pop("viewport", {"width": 1100, "height": 800}),
        device_scale_factor=2,
        locale="en-US",
        timezone_id=TIME_ZONE.key,
        color_scheme=options.pop("color_scheme", "light"),
    )
    page = await context.new_page()
    await page.goto(f"{instance.base}/network-plus")
    await page.get_by_label("Username").fill(user[0])
    await page.get_by_label("Password", exact=True).fill(user[1])
    await page.keyboard.press("Enter")
    await page.wait_for_load_state("networkidle")
    # Keep the sidebar out of the pictures
    await page.evaluate("localStorage.setItem('dockedSidebar', JSON.stringify('always_hidden'))")
    await page.reload()
    await page.wait_for_load_state("networkidle")
    return page


async def _ready(page: Page) -> None:
    """The page shows a console and Home Assistant's loading screen has faded out."""
    # The first load of the frontend takes a while on a slow CI machine
    await page.locator(f"{PANEL} ha-card").first.wait_for(timeout=90000)
    await page.locator("#ha-launch-screen").wait_for(state="detached")


async def _tab(page: Page, tab: str) -> None:
    await page.locator(f"{PANEL} a[data-tab='{tab}']").first.click()
    await page.locator(f"{PANEL} a.tab.active[data-tab='{tab}']").first.wait_for()
    await _ready(page)
    await page.wait_for_timeout(300)


async def _text(page: Page) -> str:
    return await page.locator(f"{PANEL} #sheet").inner_text()


STAMP = re.compile(r"As of \d{2}:\d{2}:\d{2}")
# mdi:refresh and mdi:cog-outline, as Home Assistant and the other pages draw them
REFRESH_ICON = "M17.65,6.35"
SETTINGS_ICON = "M12,8A4,4 0 0,1 16,12"
SETTINGS_PATH = "/config/integrations/integration/network_plus"


async def _stamp(page: Page) -> str:
    return (await page.locator(f"{PANEL} #stamp").inner_text()).strip()


async def _brand(page: Page, where: str) -> dict[str, Any]:
    """The integration's icon left of the name, loaded from its own brand folder."""
    # The token request comes after the frame: give the image its moment
    brand = page.locator(f"{PANEL} #brand")
    deadline = time.monotonic() + 15
    while not await brand.evaluate("img => img.complete && img.naturalWidth > 0"):
        expect(time.monotonic() < deadline, f"{where}: the icon does not load")
        await page.wait_for_timeout(200)
    info: dict[str, Any] = await page.locator(f"{PANEL} .toolbar").evaluate(
        """bar => {
            const img = bar.querySelector("#brand"), name = bar.querySelector(".main-title");
            const box = (el) => el && el.getBoundingClientRect();
            const b = box(img), n = box(name);
            return {src: img.currentSrc || img.src, width: img.naturalWidth, shown: b.width,
                    before: !!(img.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING),
                    name: name.textContent.trim(), nameRight: n.right, nameTop: n.top, nameHeight: n.height,
                    brandRight: b.right, barRight: box(bar).right,
                    tabs: [...bar.querySelectorAll("nav.tabs a.tab")].map((t) => {
                        const r = t.getBoundingClientRect();
                        return {top: r.top, bottom: r.bottom, left: r.left};
                    }),
                    tools: box(bar.querySelector(".tools"))};
        }"""
    )
    expect(
        "/api/brands/integration/network_plus/" in info["src"] and "token=" in info["src"],
        f"{where}: icon from {info['src']!r}",
    )
    expect(info["width"] > 0 and info["shown"] == 24, f"{where}: icon {info}")
    expect(info["before"], f"{where}: the icon is not left of the name")
    expect(info["name"] == "Network Plus", f"{where}: name {info['name']!r}")
    return info


async def _row(page: Page, where: str) -> None:
    """Wide screen: icon, name and tabs in one row, no second header row."""
    info = await _brand(page, where)
    expect(len(info["tabs"]) == 5, f"{where}: {len(info['tabs'])} tabs in the header")
    name_middle = info["nameTop"] + info["nameHeight"] / 2
    for tab in info["tabs"]:
        expect(
            tab["top"] <= name_middle <= tab["bottom"] and tab["left"] >= info["nameRight"],
            f"{where}: a tab not in the row of the name {tab} / {info}",
        )


async def _header(page: Page, where: str) -> None:
    """The end of the header: stamp, refresh, settings, in this order and nothing else."""
    parts = await page.locator(f"{PANEL} .toolbar .tools").evaluate(
        """el => [...el.children].map((c) => ({
            tag: c.localName, id: c.id, label: c.getAttribute("label"),
            path: c.getAttribute("path") || "", text: c.textContent.trim()}))"""
    )
    shape = [(part["tag"], part["id"]) for part in parts]
    expect(
        shape == [("span", "stamp"), ("ha-icon-button", "refresh"), ("ha-icon-button", "settings")],
        f"{where}: header ends with {shape}",
    )
    expect(bool(STAMP.fullmatch(parts[0]["text"])), f"{where}: stamp {parts[0]['text']!r}")
    expect(
        parts[1]["label"] == "Refresh" and parts[1]["path"].startswith(REFRESH_ICON),
        f"{where}: refresh button {parts[1]}",
    )
    expect(
        parts[2]["label"] == "Settings" and parts[2]["path"].startswith(SETTINGS_ICON),
        f"{where}: settings button {parts[2]}",
    )


async def check_page(browser: Browser, instance: Instance, api: Api, shots: Path | None) -> Checks:
    page = await _open(browser, instance, ADMIN)
    # The overview requests the page sends over the WebSocket
    overviews: list[float] = []

    def watch(ws: Any) -> None:
        ws.on(
            "framesent",
            lambda frame: (
                overviews.append(time.monotonic())
                if '"network_plus/overview"' in str(frame)
                else None
            ),
        )

    page.on("websocket", watch)

    async def from_start() -> None:
        """Every check starts on the first tab, whatever the one before left behind."""
        await page.goto(f"{instance.base}/network-plus/uplinks")
        await _ready(page)

    checks = Checks(before=from_start)
    for name in instance.flow_checks:
        checks.passed.append(name)
        print(f"ok   {name}")

    async def uplinks() -> None:
        text = await _text(page)
        for part in ("Gateway", "WAN", "LTE Failover WAN", "LTE backup", "198.51.100.23"):
            expect(part in text, f"{part!r} missing on the Uplinks tab")
        defined = await page.evaluate(
            "() => ['ha-card', 'ha-alert', 'ha-icon-button'].filter((n) => !customElements.get(n))"
        )
        expect(not defined, f"Home Assistant elements not available: {defined}")
        cards = await page.locator(f"{PANEL} ha-card").count()
        expect(cards >= 4, f"4 cards expected, got {cards}")
        expect(
            await page.locator(f"{PANEL} .card, {PANEL} button.icon-button").count() == 0,
            "own copies of Home Assistant's elements",
        )
        about = (await page.locator(f"{PANEL} #about").inner_text()).strip()
        expect(about == f"Network Plus {VERSION}", f"version line: {about!r}")
        if shots is not None:
            await page.screenshot(path=str(shots / "uplinks.png"))

    await checks.run(
        "uplinks, LTE backup and the version line from Home Assistant's cards", uplinks
    )

    async def history() -> None:
        await _tab(page, "events")
        text = await _text(page)
        expect("Switched to LTE Failover WAN" in text, "failover missing")
        expect("Back on WAN" in text and "after 18 min" in text, "return with its duration missing")
        expect("New public IPv6 address" in text, "address change missing")
        expect("198.51.100" not in text, "an address in the history")
        if shots is not None:
            await page.screenshot(path=str(shots / "history.png"))

    await checks.run("history: switches with durations, no addresses", history)

    async def speedtests() -> None:
        await _tab(page, "speedtests")
        expect(await page.locator(f"{PANEL} svg.chart polyline").count() == 2, "two lines expected")
        rows = await page.locator(f"{PANEL} table.data tr").count()
        expect(rows == 34, f"33 results and a head expected, got {rows} rows")
        if shots is not None:
            await page.screenshot(path=str(shots / "speedtests.png"))

    await checks.run("speed tests: a line per uplink and every result", speedtests)

    async def drills() -> None:
        await _tab(page, "drills")
        text = await _text(page)
        expect("Average" in text, "average of the drills missing")
        expect(await page.locator(f"{PANEL} table.data tr").count() == 5, "3 drills, head, average")
        if shots is not None:
            await page.screenshot(path=str(shots / "drills.png"))

    await checks.run("drills with their average", drills)

    async def remote() -> None:
        await _tab(page, "remote")
        text = await _text(page)
        expect("Travel tablet" in text and "Laptop" in text, "remote clients missing")
        expect("Desk" not in text, "a local client among the remote ones")

    await checks.run("remote clients now and their sessions", remote)

    async def console_choice() -> None:
        choice = page.locator(f"{PANEL} select#console")
        await choice.select_option(label="Office")
        await page.locator(f"{PANEL} ha-card .card-header", has_text="Office").first.wait_for()
        text = await _text(page)
        expect("203.0.113.45" in text, "the other console's uplink missing")
        expect("Drills need the admin opt-in" not in text, "wrong tab")
        await page.wait_for_timeout(500)
        header = await page.locator(f"{PANEL} ha-card .card-header").first.inner_text()
        expect(header.startswith("Office"), f"the choice went back: {header!r}")
        await choice.select_option(label="Gateway")
        await page.locator(f"{PANEL} ha-card .card-header", has_text="Gateway").first.wait_for()

    await checks.run("choosing another console shows it and keeps it", console_choice)

    async def header() -> None:
        for tab in ("uplinks", "events", "speedtests", "drills", "remote"):
            if tab != "uplinks":
                await _tab(page, tab)
            await _header(page, tab)
            await _row(page, tab)
            expect(
                await page.locator(f"{PANEL} .toolbar select").count() == 0,
                f"{tab}: the console choice in the header",
            )
            expect(await page.locator(f"{PANEL} #stamp").is_visible(), f"{tab}: stamp hidden")

    await checks.run(
        "header on every tab: icon, name and tabs in one row; stamp, refresh, settings", header
    )

    async def switch_state(entity_id: str, wanted: str) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            reply = await api.ws(instance.token, {"type": "get_states"})
            state = next(s for s in reply["result"] if s["entity_id"] == entity_id)
            if state["state"] == wanted:
                return
            await asyncio.sleep(0.3)
        raise AssertionError(f"{entity_id} did not become {wanted}")

    async def enforced(wanted: str) -> None:
        row = page.locator(f"{PANEL} table.kv tr", has_text="Backup uplink enforced")
        await row.locator("td", has_text=re.compile(f"^{wanted}$")).wait_for(timeout=10000)

    async def refresh() -> None:
        await enforced("no")
        states = await api.ws(instance.token, {"type": "get_states"})
        switch = next(
            s["entity_id"]
            for s in states["result"]
            if s["entity_id"].startswith("switch.gateway") and "enforce" in s["entity_id"]
        )
        try:
            for wanted, shown in (("on", "yes"), ("off", "no")):
                before = await _stamp(page)
                # The stamp counts seconds: let one pass so a new one differs
                await page.wait_for_timeout(1100)
                service = "turn_on" if wanted == "on" else "turn_off"
                await api.post(
                    instance.token, f"/api/services/switch/{service}", {"entity_id": switch}
                )
                await switch_state(switch, wanted)
                sent = len(overviews)
                clicked = time.monotonic()
                # The click and the class in one go: the button spins while the answer is on its way
                spinning = await page.locator(f"{PANEL} #refresh").evaluate(
                    "el => { el.click(); return el.classList.contains('spin'); }"
                )
                expect(spinning, "the refresh button does not spin while loading")
                await enforced(shown)
                expect(
                    any(at >= clicked for at in overviews[sent:]),
                    "refresh sent no request over the WebSocket",
                )
                await page.locator(f"{PANEL} #refresh:not(.spin)").wait_for()
                after = await _stamp(page)
                expect(
                    after != before and STAMP.fullmatch(after) is not None,
                    f"stamp {before!r} → {after!r}",
                )
        finally:
            # Whatever failed, the next checks start with the uplink on
            if instance.consoles[0].wan_type != "dhcp":
                await api.post(
                    instance.token, "/api/services/switch/turn_off", {"entity_id": switch}
                )
        expect(instance.consoles[0].wan_type == "dhcp", "the uplink did not come back")

    await checks.run(
        "refresh fetches the data again over the WebSocket, spins and moves the stamp", refresh
    )

    async def settings() -> None:
        await page.evaluate("window.__noReload = true")
        await page.locator(f"{PANEL} #settings").click()
        await page.wait_for_url(f"**{SETTINGS_PATH}")
        expect(
            await page.evaluate("window.__noReload === true"),
            "the settings button reloaded the whole page",
        )
        await page.wait_for_function(
            """() => {
                const main = document.querySelector('home-assistant')?.shadowRoot
                    ?.querySelector('home-assistant-main')?.shadowRoot;
                return !!main && !main.querySelector('network-plus-panel')
                    && !!main.querySelector('ha-panel-config');
            }""",
            timeout=30000,
        )

    await checks.run("settings opens Home Assistant's page of the integration in the app", settings)

    async def phone_and_dark() -> None:
        for scheme in ("light", "dark"):
            phone = await _open(
                browser,
                instance,
                ADMIN,
                viewport={"width": 390, "height": 844},
                color_scheme=scheme,
            )
            await _ready(phone)
            for tab in ("uplinks", "events", "speedtests", "drills", "remote"):
                await phone.goto(f"{instance.base}/network-plus/{tab}")
                await _ready(phone)
                overflow = await phone.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                expect(overflow <= 0, f"{scheme}/{tab}: page scrolls sideways by {overflow} px")
            expect(
                await phone.locator(f"{PANEL} nav.bottom-tabs a.tab").count() == 5
                and await phone.locator(f"{PANEL} .toolbar nav.tabs").count() == 0,
                "tabs at the bottom on a phone",
            )
            # Icon (the dark one falls back to the light), name and buttons fit the row
            info = await _brand(phone, f"{scheme} phone")
            expect(
                ("/dark_icon" in info["src"]) == (scheme == "dark"),
                f"{scheme} phone: icon {info['src']!r}",
            )
            expect(
                info["brandRight"] <= info["nameRight"] <= info["tools"]["left"]
                and info["tools"]["right"] <= info["barRight"],
                f"{scheme} phone: header row does not fit {info}",
            )
            expect(
                not await phone.locator(f"{PANEL} #stamp").is_visible()
                and await phone.locator(f"{PANEL} #refresh").is_visible()
                and await phone.locator(f"{PANEL} #settings").is_visible(),
                "on a phone: stamp hidden, refresh and settings shown",
            )
            background = await phone.locator(PANEL).evaluate(
                "el => getComputedStyle(el).backgroundColor"
            )
            if scheme == "dark":
                channels = [
                    int(part) for part in background[background.index("(") + 1 : -1].split(",")[:3]
                ]
                expect(sum(channels) < 200, f"dark mode background is {background}")
                if shots is not None:
                    await phone.goto(f"{instance.base}/network-plus/uplinks")
                    await _ready(phone)
                    await phone.wait_for_timeout(300)
                    await phone.screenshot(path=str(shots / "phone-dark.png"))
            await phone.context.close()

    await checks.run(
        "phone width and dark mode: no sideways scrolling, icon and buttons fit, tabs at the bottom",
        phone_and_dark,
    )

    async def admin_only() -> None:
        token = await api.login(*USER)
        reply = await api.ws(token, {"type": "network_plus/overview"})
        expect(not reply["success"] and reply["error"]["code"] == "unauthorized", str(reply))
        panels = await api.ws(token, {"type": "get_panels"})
        expect("network-plus" not in panels["result"], "the page is offered to a user")
        states = await api.ws(instance.token, {"type": "get_states"})
        switch = next(
            s["entity_id"]
            for s in states["result"]
            if s["entity_id"].startswith("switch.gateway") and "enforce" in s["entity_id"]
        )
        status = await api.status(token, "/api/services/switch/turn_on", {"entity_id": switch})
        expect(status == 401, f"a user switched the uplink off (HTTP {status})")
        expect(instance.consoles[0].wan_type == "dhcp", "the console was written by a user")
        await api.post(instance.token, "/api/services/switch/turn_on", {"entity_id": switch})
        expect(instance.consoles[0].wan_type == "disabled", "an administrator could not switch")
        await api.post(instance.token, "/api/services/switch/turn_off", {"entity_id": switch})
        expect(instance.consoles[0].wan_type == "dhcp", "the uplink did not come back")

    await checks.run(
        "users without administrator rights get neither page nor admin writes", admin_only
    )
    await page.context.close()
    return checks


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--screenshots", type=Path, help="write the README pictures into this directory"
    )
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    parser.add_argument("--browser", choices=["chrome", "chromium"], default="chromium")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if args.screenshots is not None:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    directory = Path(tempfile.mkdtemp(prefix="network-plus-panel-"))
    instance = Instance(directory)
    checks = Checks()
    async with aiohttp.ClientSession() as session:
        try:
            api = await instance.start(session)
            print(f"Home Assistant on {instance.base}, files in {directory}")
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    channel="chrome" if args.browser == "chrome" else None,
                    headless=not args.headed,
                )
                try:
                    checks = await check_page(browser, instance, api, args.screenshots)
                finally:
                    await browser.close()
        except Exception as err:
            checks.failed.append(f"setup: {err!r}")
            print(f"FAIL setup: {err!r}")
            log = directory / "hass.log"
            if log.exists():
                print("".join(log.read_text(errors="replace").splitlines(keepends=True)[-60:]))
        finally:
            await instance.stop(session)
    errors = (
        [
            line
            for line in (directory / "config" / "home-assistant.log")
            .read_text(errors="replace")
            .splitlines()
            if (" ERROR " in line or " WARNING " in line)
            and "network_plus" in line
            # Home Assistant says this of every custom integration
            and "has not been tested by Home Assistant" not in line
        ]
        if (directory / "config" / "home-assistant.log").exists()
        else []
    )
    for line in errors:
        checks.failed.append(f"log: {line}")
        print(f"FAIL log: {line}")
    print(f"\n{len(checks.passed)} passed, {len(checks.failed)} failed")
    if args.keep or checks.failed:
        print(f"Files kept in {directory}")
    else:
        shutil.rmtree(directory, ignore_errors=True)
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
