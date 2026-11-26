"""Check the page in a real browser against a throwaway Home Assistant.

Starts two made-up consoles (fake_console.py, HTTPS with their own
certificates) and Home Assistant in a temporary directory, both reachable from
this machine only (see run_hass.py). The consoles are added through the config
flow, the first with its certificate pinned and the admin opt-in; a made-up
history goes into the first one's store. Then Playwright drives the page and
the checks look at what it shows and who may do what, as STANDARD.md §7 "Page
check" lists it: on every page Home Assistant's frame with the three areas (one
active) and the menu button, the page's own tab, stamp, refresh and the
integration button; the areas at the bottom on a phone, no sideways scrolling;
old addresses and a change of the query alone; every (i); an error as one line
with the message behind its (i); numbers in the profile's format (a decimal
comma, German); refresh; the integration button; the dashboard card for a user
without administrator rights, in its sizes, in the states of a failover made up
in the page, and through a failover and a drill of the made-up console. The page has no settings, no
subpages and no drawing, so there are no settings rows, no save button and no
arrow keys to check. Home Assistant is stopped through its own stop action at
the end, also after a failure.

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
CARD = "network-plus-card"
DASHBOARD = "internet-cards"
# An iPhone's safe area (47 px above, 34 px below), as Home Assistant's debug setting sets it
SAFE_AREA = """() => {
  const style = document.createElement('style');
  style.textContent =
    ':root{--app-safe-area-inset-top:47px !important;--app-safe-area-inset-bottom:34px !important;}';
  document.head.append(style);
}"""
# Top and bottom of Home Assistant's frame for pages with tabs, the height of its header and
# top and bottom of its bar at the bottom, in the viewport
SAFE_AREA_MEASURE = """() => {
  const find = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.localName === 'hass-tabs-subpage') return el;
      const inner = el.shadowRoot && find(el.shadowRoot);
      if (inner) return inner;
    }
    return null;
  };
  const frame = find(document);
  if (!frame) return 'no hass-tabs-subpage';
  const box = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return [Math.round(b.top), Math.round(b.bottom)];
  };
  const toolbar = frame.shadowRoot.querySelector('.toolbar');
  return {
    viewport: innerHeight,
    frame: box(frame),
    toolbar: toolbar ? Math.round(toolbar.getBoundingClientRect().height) : null,
    tabbar: box(frame.shadowRoot.querySelector('#tabbar.bottom-bar')),
  };
}"""


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
    for day, hour, minutes, name, down, up in (
        (3, 19, 95, "Travel tablet", 2_310_000_000, 84_000_000),
        (12, 8, 22, "Laptop", 152_000_000, 9_400_000),
        (27, 21, 140, "Travel tablet", 3_050_000_000, 121_000_000),
    ):
        start = today - timedelta(days=day) + timedelta(hours=hour, minutes=6)
        sessions.append(
            {
                "at": iso(start),
                "until": iso(start + timedelta(minutes=minutes)),
                "type": "TELEPORT",
                "name": name,
                "down_bytes": down,
                "up_bytes": up,
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
        await self._add_dashboard(api)
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

    async def _add_dashboard(self, api: Api) -> None:
        """A dashboard with the card: as it comes, for the second console, and in three sizes."""
        devices = await api.ws(self.token, {"type": "config/device_registry/list"})
        office = next(
            device["id"]
            for device in devices["result"]
            if device["name"] == "Office" and self.entry_ids["Office"] in device["config_entries"]
        )
        card = {"type": f"custom:{CARD}"}
        created = await api.ws(
            self.token,
            {
                "type": "lovelace/dashboards/create",
                "url_path": DASHBOARD,
                "title": "Internet",
                "mode": "storage",
            },
        )
        if not created["success"]:
            raise RuntimeError(f"Dashboard not created: {created}")
        saved = await api.ws(
            self.token,
            {
                "type": "lovelace/config/save",
                "url_path": DASHBOARD,
                "config": {
                    "views": [
                        {
                            "path": "large",
                            "title": "Large",
                            "cards": [
                                card,
                                {
                                    **card,
                                    "title": "Office",
                                    "device": office,
                                    "show_networks": False,
                                    "show_names": True,
                                    "show_latency": True,
                                    "show_last_switch": True,
                                    "show_problems": True,
                                },
                            ],
                        },
                        {
                            "path": "sizes",
                            "title": "Sizes",
                            "type": "sections",
                            "sections": [
                                {
                                    "type": "grid",
                                    "cards": [
                                        {**card, "grid_options": {"columns": 12, "rows": rows}}
                                        for rows in (1, 3, 6)
                                    ],
                                },
                                {
                                    "type": "grid",
                                    "cards": [
                                        # As it comes, and half as wide in one row
                                        card,
                                        {**card, "grid_options": {"columns": 6, "rows": 1}},
                                    ],
                                },
                            ],
                        },
                    ]
                },
            },
        )
        if not saved["success"]:
            raise RuntimeError(f"Dashboard not saved: {saved}")

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


async def _until(check: Callable[[], Awaitable[bool]], what: str, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


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
    await page.locator(f"{PANEL} #sheet ha-card").first.wait_for(timeout=90000)
    await page.locator("#ha-launch-screen").wait_for(state="detached")


async def _goto(page: Page, instance: Instance, path: str) -> None:
    """A page of the panel by its address, loaded anew."""
    await page.goto(f"{instance.base}/network-plus/{path}")
    await _ready(page)
    await page.wait_for_timeout(300)


async def _text(page: Page) -> str:
    return await page.locator(f"{PANEL} #sheet").inner_text()


STAMP = re.compile(r"As of \d{2}:\d{2}:\d{2}")
# mdi:refresh and mdi:cog-outline, as Home Assistant and the other pages draw them
REFRESH_ICON = "M17.65,6.35"
INTEGRATION_ICON = "M12,8A4,4 0 0,1 16,12"
INTEGRATION_PATH = "/config/integrations/integration/network_plus"
# The areas, their pages and the address of each
AREAS = {
    "uplinks": ("status", "history"),
    "tests": ("speedtests", "drills"),
    "remote": ("now", "sessions", "devices"),
}
PAGES = [f"{area}/{page}" for area, pages in AREAS.items() for page in pages]
# Home Assistant's elements the page uses; all of them on the list of STANDARD.md §7
ELEMENTS = [
    "hass-tabs-subpage",
    "ha-tab-group",
    "ha-tab-group-tab",
    "ha-card",
    "ha-alert",
    "ha-icon-button",
    "ha-svg-icon",
    "ha-tooltip",
    "ha-select",
    "ha-badge",
]


async def _stamp(page: Page) -> str:
    return (await page.locator(f"{PANEL} #stamp").inner_text()).strip()


async def _frame(page: Page, where: str, *, page_id: str, phone: bool = False) -> None:
    """STANDARD.md §7: Home Assistant's frame with the three areas as tabs (one
    active, in the header on a wide screen, at the bottom on a narrow one) and
    the menu button; the page's own tab active below it; at the end of the header
    stamp, refresh and the integration button, in this order and nothing else."""
    h: dict[str, Any] = await page.locator(PANEL).evaluate(
        """el => {
            const root = el.shadowRoot, frame = root.getElementById("frame");
            const bar = frame.shadowRoot.getElementById("tabbar");
            const tabs = bar ? [...bar.querySelectorAll("ha-tab")] : [];
            const active = root.querySelector("#subtabs ha-tab-group-tab[active]");
            return {
                tabs: tabs.length, active: tabs.filter((t) => t.active).length,
                activeName: (tabs.find((t) => t.active)?.textContent || "").trim(),
                bottom: !!bar && bar.classList.contains("bottom-bar"),
                menu: !!frame.shadowRoot.querySelector("ha-menu-button"),
                back: !!frame.shadowRoot.querySelector("ha-icon-button-arrow-prev"),
                page: active ? active.getAttribute("panel") : null,
                parts: [...root.querySelector("[slot=toolbar-icon]").children].map((c) => ({
                    tag: c.localName, id: c.id, label: c.getAttribute("label"),
                    path: c.getAttribute("path") || "", text: c.textContent.trim(),
                    visible: getComputedStyle(c).display !== "none"})),
            };
        }"""
    )
    expect(h["menu"] and not h["back"], f"{where}: no menu button in the frame ({h})")
    expect(h["tabs"] == 3 and h["active"] == 1, f"{where}: areas {h}")
    expect(h["bottom"] == phone, f"{where}: areas {'not ' if phone else ''}at the bottom ({h})")
    expect(h["page"] == page_id, f"{where}: tab of the page {h['page']!r}, not {page_id!r}")
    shape = [(p["tag"], p["id"]) for p in h["parts"]]
    expect(
        shape
        == [("span", "stamp"), ("ha-icon-button", "refresh"), ("ha-icon-button", "integration")],
        f"{where}: header ends with {shape}",
    )
    stamp, refresh, integration = h["parts"]
    expect(bool(STAMP.fullmatch(stamp["text"])), f"{where}: stamp {stamp['text']!r}")
    expect(stamp["visible"] != phone, f"{where}: stamp {'shown' if phone else 'hidden'}")
    expect(
        refresh["label"] == "Refresh" and refresh["path"].startswith(REFRESH_ICON),
        f"{where}: refresh {refresh}",
    )
    expect(
        integration["label"] == "Integration in Home Assistant"
        and integration["path"].startswith(INTEGRATION_ICON),
        f"{where}: integration button {integration}",
    )


async def _helps(page: Page, where: str) -> int:
    """Every (i) is Home Assistant's icon button with its tooltip, shown on hover and on a tap."""
    helps: list[dict[str, Any]] = await page.locator(PANEL).evaluate(
        """el => [...el.shadowRoot.querySelectorAll("ha-icon-button.help")].map((b) => {
            const tip = el.shadowRoot.querySelector(`ha-tooltip[for="${b.id}"]`);
            return {label: b.getAttribute("label"), trigger: tip?.getAttribute("trigger"),
                    text: (tip?.textContent || "").trim()};
        })"""
    )
    for help_ in helps:
        expect(
            help_["label"] == "Explanation"
            and help_["trigger"] == "hover click"
            and bool(help_["text"]),
            f"{where}: (i) {help_}",
        )
    return len(helps)


async def check_page(browser: Browser, instance: Instance, api: Api, shots: Path | None) -> Checks:
    page = await _open(browser, instance, ADMIN)
    gateway_id, office_id = instance.entry_ids["Gateway"], instance.entry_ids["Office"]
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
        """Every check starts on the first page, whatever the one before left behind."""
        await _goto(page, instance, "uplinks/status")

    checks = Checks(before=from_start)
    for name in instance.flow_checks:
        checks.passed.append(name)
        print(f"ok   {name}")

    async def status() -> None:
        text = await _text(page)
        for part in ("Gateway", "WAN", "LTE Failover WAN", "LTE backup", "198.51.100.23"):
            expect(part in text, f"{part!r} missing on Uplinks: Status")
        missing = await page.evaluate(
            f"() => {json.dumps(ELEMENTS)}.filter((n) => !customElements.get(n))"
        )
        expect(not missing, f"Home Assistant elements not available: {missing}")
        cards = await page.locator(f"{PANEL} ha-card").count()
        expect(cards >= 4, f"4 cards expected, got {cards}")
        # Only Home Assistant's elements: no home-made chips, tabs, selects or icons
        own = await page.locator(PANEL).evaluate(
            """el => [...el.shadowRoot.querySelectorAll(
                "select, .chip, .tab, nav, ha-icon, ha-menu-button, ha-top-app-bar-fixed, svg.mdi")]
                .map((e) => e.localName + "." + e.className)"""
        )
        expect(not own, f"own copies of Home Assistant's elements: {own}")
        # Every state has a word, not only a colour
        badges = await page.locator(f"{PANEL} ha-badge").all_inner_texts()
        expect("Online" in badges and "active" in badges, f"badges {badges}")
        bar = await page.locator(f"{PANEL} .bar").get_attribute("aria-label")
        expect(bool(bar) and "% of the data plan used" in (bar or ""), f"LTE bar label {bar!r}")
        # The networks: whether each reaches the internet, per uplink in words
        expect("Networks" in text and "Guest" in text, "the networks card missing")
        marks = await page.locator(f"{PANEL} table.data ha-svg-icon").evaluate_all(
            "els => els.map((e) => e.getAttribute('aria-label'))"
        )
        expect(
            "LTE Failover WAN: does not carry it" in marks and "WAN: carries it" in marks,
            f"network marks {marks}",
        )
        # Failover: an exclamation mark for the network only one uplink carries, a check otherwise
        expect("Failover" in text, "the failover column missing")
        flagged = await page.locator(f"{PANEL} table.data tr").evaluate_all(
            """rows => rows.filter((r) => r.querySelector('[aria-label="no backup"]'))
                .map((r) => r.cells[0].innerText.trim())"""
        )
        expect(flagged == ["Guest"], f"networks without backup {flagged}")
        expect("with backup" in marks, f"a network with backup unmarked {marks}")
        about = (await page.locator(f"{PANEL} #about").inner_text()).strip()
        expect(about == f"Network Plus {VERSION}", f"version line: {about!r}")
        if shots is not None:
            await page.screenshot(path=str(shots / "uplinks.png"))

    await checks.run(
        "Uplinks, Status: uplinks, networks, LTE backup, states in words and the version line",
        status,
    )

    async def every_page() -> None:
        for path in PAGES:
            await _goto(page, instance, path)
            await _frame(page, path, page_id=path.split("/")[1])
            await _helps(page, path)
            about = (await page.locator(f"{PANEL} #about").inner_text()).strip()
            expect(about == f"Network Plus {VERSION}", f"{path}: version line {about!r}")

    await checks.run(
        "every page: the three areas, its own tab, stamp, refresh, integration, (i), version",
        every_page,
    )

    async def history() -> None:
        await _goto(page, instance, "uplinks/history")
        text = await _text(page)
        expect("Switched to LTE Failover WAN" in text, "failover missing")
        expect("Back on WAN" in text and "after 18 min" in text, "return with its duration missing")
        expect("New public IPv6 address" in text, "address change missing")
        expect("198.51.100" not in text, "an address in the history")
        kinds = set(
            await page.locator(PANEL).evaluate(
                """el => [...el.shadowRoot.querySelectorAll(".item .kind")]
                    .map((k) => k.getAttribute("aria-label"))"""
            )
        )
        expect(kinds == {"Switch", "Back", "Outage", "Change"}, f"kinds in words {kinds}")
        expect(await _helps(page, "history") == 1, "the kept days behind the card's (i)")
        if shots is not None:
            await page.screenshot(path=str(shots / "history.png"))

    await checks.run("Uplinks, History: switches with durations, kinds in words", history)

    async def speedtests() -> None:
        await _goto(page, instance, "tests/speedtests")
        expect(await page.locator(f"{PANEL} svg.chart polyline").count() == 2, "two lines expected")
        label = await page.locator(f"{PANEL} svg.chart").get_attribute("aria-label") or ""
        expect("WAN" in label and "LTE Failover WAN" in label, f"chart label {label!r}")
        rows = await page.locator(f"{PANEL} table.data tr").count()
        expect(rows == 34, f"33 results and a head expected, got {rows} rows")
        if shots is not None:
            await page.screenshot(path=str(shots / "speedtests.png"))

    await checks.run("Tests, Speed tests: a line per uplink, its label, every result", speedtests)

    async def drills() -> None:
        await _goto(page, instance, "tests/drills")
        text = await _text(page)
        expect("Average" in text, "average of the drills missing")
        expect(await page.locator(f"{PANEL} table.data tr").count() == 5, "3 drills, head, average")
        if shots is not None:
            await page.screenshot(path=str(shots / "drills.png"))

    await checks.run("Tests, Drills with their average", drills)

    async def remote() -> None:
        await _goto(page, instance, "remote/now")
        text = await _text(page)
        expect("Desk" not in text, "a local client among the remote ones")
        expect("1.84 GB" in text and "96 MB" in text, f"volume of the connected client: {text}")
        expect("Default" in text, "the network of the connected client")
        await _goto(page, instance, "remote/sessions")
        text = await _text(page)
        expect("Travel tablet" in text and "Laptop" in text, "remote sessions missing")
        expect("2.31 GB" in text and "152 MB" in text, "volume of the sessions missing")
        await _goto(page, instance, "remote/devices")
        text = await _text(page)
        expect("Old phone" in text and "Travel tablet" in text, "known devices missing")
        expect("Desk" not in text, "a local client among the known devices")
        badges = await page.locator(f"{PANEL} ha-badge").all_inner_texts()
        expect([b.strip() for b in badges] == ["connected"], f"connected badge {badges}")

    await checks.run(
        "Remote: the clients now with their volume, the sessions, the known devices", remote
    )

    async def in_app_tabs() -> None:
        # The page's tab and Home Assistant's area tab navigate without a reload
        await page.evaluate("window.__noReload = true")
        await page.locator(f"{PANEL} ha-tab-group-tab[panel='history']").click()
        await page.wait_for_url("**/network-plus/uplinks/history")
        await page.locator(f"{PANEL} .item").first.wait_for()
        await page.locator(PANEL).evaluate(
            """el => [...el.shadowRoot.getElementById("frame").shadowRoot
                .querySelectorAll("#tabbar ha-tab")][1].click()"""
        )
        await page.wait_for_url("**/network-plus/tests/speedtests")
        await page.locator(f"{PANEL} svg.chart").wait_for()
        await _frame(page, "after the area tab", page_id="speedtests")
        expect(await page.evaluate("window.__noReload === true"), "a tab reloaded the page")

    await checks.run("the tabs of the pages and of the areas navigate in the app", in_app_tabs)

    async def old_addresses() -> None:
        for old, new in (
            ("", "/network-plus/uplinks/status"),
            ("uplinks", "/network-plus/uplinks/status"),
            ("events", "/network-plus/uplinks/history"),
            ("speedtests", "/network-plus/tests/speedtests"),
            ("drills", "/network-plus/tests/drills"),
            ("remote", "/network-plus/remote/now"),
        ):
            await page.goto(f"{instance.base}/network-plus/{old}")
            await page.wait_for_url(f"**{new}")
            await _ready(page)
        # The query goes along
        await page.goto(f"{instance.base}/network-plus/events?console={office_id}")
        await page.wait_for_url(f"**/network-plus/uplinks/history?console={office_id}")

    await checks.run("old addresses lead to the pages that took their place", old_addresses)

    async def console_choice() -> None:
        header = page.locator(f"{PANEL} ha-card .card-header").first
        select = page.locator(f"{PANEL} ha-select#console")
        expect(
            await select.evaluate("s => s.label") == "Console"
            and await select.evaluate("s => s.value") == gateway_id,
            "the choice does not show the first console",
        )
        await select.evaluate(
            "(s, v) => s.dispatchEvent(new CustomEvent('selected', {detail: {value: v}, bubbles: true, composed: true}))",
            office_id,
        )
        await page.wait_for_url(f"**/network-plus/uplinks/status?console={office_id}")
        await header.filter(has_text="Office").wait_for()
        text = await _text(page)
        expect("203.0.113.45" in text, "the other console's uplink missing")
        # Another area keeps the console
        await page.locator(PANEL).evaluate(
            """el => [...el.shadowRoot.getElementById("frame").shadowRoot
                .querySelectorAll("#tabbar ha-tab")][2].click()"""
        )
        await page.wait_for_url(f"**/network-plus/remote/now?console={office_id}")
        # A change of the query alone, like a link of Home Assistant: the other console
        await page.evaluate(
            """id => {
                history.pushState(null, "", `/network-plus/uplinks/status?console=${id}`);
                window.dispatchEvent(new CustomEvent("location-changed", {detail: {replace: false}}));
            }""",
            gateway_id,
        )
        await header.filter(has_text="Gateway").wait_for()
        expect(await select.evaluate("s => s.value") == gateway_id, "the choice did not follow")
        # Back: Office again, from the address alone
        await page.go_back()
        await page.wait_for_url(f"**/network-plus/remote/now?console={office_id}")
        await page.locator(f"{PANEL} ha-tab-group-tab[panel='now'][active]").wait_for()

        async def office() -> bool:
            return bool(await select.evaluate("s => s.value") == office_id)

        await _until(office, "the choice back on Office")
        # A reload keeps it
        await _goto(page, instance, f"uplinks/status?console={office_id}")
        await header.filter(has_text="Office").wait_for()

    await checks.run(
        "the console in the address: chosen, kept across areas, changed by the query alone",
        console_choice,
    )

    async def unknown_console() -> None:
        await page.goto(f"{instance.base}/network-plus/uplinks/status?console=nope")
        alert = page.locator(
            f"{PANEL} ha-alert[alert-type='error']",
            has_text="This console is not set up (any more).",
        )
        await alert.wait_for(timeout=60000)
        said = await alert.locator("ha-tooltip").evaluate("el => el.textContent")
        expect(said.strip().startswith("Message:"), f"message behind the (i): {said!r}")
        expect(await page.locator(f"{PANEL} #sheet ha-card").count() == 0, "a console shown")
        expect(
            await page.locator(f"{PANEL} ha-alert[alert-type='error']").count() == 1,
            "more than one error line",
        )
        # The choice offers the consoles, none chosen; the version line stays
        await page.locator(f"{PANEL} ha-select#console").wait_for()
        expect(
            await page.locator(f"{PANEL} ha-select#console").evaluate("s => s.value") is None,
            "a console chosen in place of the missing one",
        )
        about = (await page.locator(f"{PANEL} #about").inner_text()).strip()
        expect(about == f"Network Plus {VERSION}", f"version line on an error: {about!r}")

    await checks.run(
        "an unknown console reads one error line, the message behind its (i)", unknown_console
    )

    async def not_loaded() -> None:
        reply = await api.ws(
            instance.token,
            {"type": "config_entries/disable", "entry_id": office_id, "disabled_by": "user"},
        )
        expect(reply["success"], str(reply))
        try:
            await page.goto(f"{instance.base}/network-plus/uplinks/status?console={office_id}")
            alert = page.locator(
                f"{PANEL} ha-alert[alert-type='warning']",
                has_text="This console is not loaded at the moment.",
            )
            await alert.wait_for(timeout=60000)
            expect(await alert.locator("ha-icon-button.help").count() == 1, "no (i) on the line")
        finally:
            await api.ws(
                instance.token,
                {"type": "config_entries/disable", "entry_id": office_id, "disabled_by": None},
            )

            async def loaded() -> bool:
                entries = await api.ws(
                    instance.token, {"type": "config_entries/get", "domain": "network_plus"}
                )
                return all(e["state"] == "loaded" for e in entries["result"])

            await _until(loaded, "Office loaded again", seconds=30)

    await checks.run("a console not loaded: one line, the why behind its (i)", not_loaded)

    async def tooltip_on_tap() -> None:
        await _goto(page, instance, "tests/drills")
        button = page.locator(f"{PANEL} ha-card ha-icon-button.help").first
        await button.click()
        tip = page.locator(f"{PANEL} ha-tooltip").first

        async def shown() -> bool:
            return bool(await tip.evaluate("t => t.open === true || t.hasAttribute('open')"))

        await _until(shown, "the tooltip of the (i) after a tap", seconds=5)

    await checks.run("a tap on the (i) shows its explanation", tooltip_on_tap)

    async def locale(language: str, number_format: str) -> None:
        """The user's profile: language and number format, as Home Assistant's profile page sets them."""
        await page.evaluate(
            """([language, format]) => {
                const ha = document.querySelector("home-assistant");
                ha.dispatchEvent(new CustomEvent("hass-language-select", {detail: language}));
                ha.dispatchEvent(new CustomEvent("hass-number-format-select", {detail: format}));
            }""",
            [language, number_format],
        )

    async def numbers() -> None:
        try:
            # English with a decimal comma: the profile's number format, not the language's
            await locale("en", "decimal_comma")

            async def comma() -> bool:
                return "99,8 %" in await _text(page)

            await _until(comma, "99,8 % with a decimal_comma profile")
            expect("Availability" in await _text(page), "the texts are not English")
            # German: German texts, the language's comma
            await locale("de", "language")

            async def german() -> bool:
                text = await _text(page)
                return "Verfügbarkeit" in text and "99,8 %" in text

            await _until(german, "German texts with 99,8 %")
            tabs = await page.locator(f"{PANEL} ha-tab-group-tab").all_inner_texts()
            expect([t.strip() for t in tabs] == ["Status", "Verlauf"], f"German tabs {tabs}")
            label = await page.locator(f"{PANEL} #integration").get_attribute("label")
            expect(label == "Integration in Home Assistant", f"German label {label!r}")
            await _goto(page, instance, "tests/speedtests")
            ticks = await page.locator(f"{PANEL} svg.chart text").all_text_contents()
            expect(any("." in t for t in ticks[:6]), f"axis without German grouping {ticks}")
        finally:
            await locale("en", "language")

            async def english() -> bool:
                return "Results" in await _text(page) or "Availability" in await _text(page)

            await _until(english, "the page in English again")
        await _goto(page, instance, "uplinks/status")
        expect("99.8 %" in await _text(page), "English numbers again")

    await checks.run(
        "numbers in the profile's format (decimal comma, German), texts in its language", numbers
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
        await page.evaluate("window.__noReload = true")
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
            expect(await page.evaluate("window.__noReload === true"), "refresh reloaded the page")
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

    async def integration() -> None:
        await page.evaluate("window.__noReload = true")
        await page.locator(f"{PANEL} #integration").click()
        await page.wait_for_url(f"**{INTEGRATION_PATH}")
        expect(
            await page.evaluate("window.__noReload === true"),
            "the integration button reloaded the whole page",
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

    await checks.run(
        "the integration button opens Home Assistant's page of the integration in the app",
        integration,
    )

    async def phone_and_dark() -> None:
        for scheme in ("light", "dark"):
            phone = await _open(
                browser,
                instance,
                ADMIN,
                viewport={"width": 390, "height": 844},
                color_scheme=scheme,
            )
            for path in PAGES:
                await _goto(phone, instance, path)
                overflow = await phone.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                expect(overflow <= 0, f"{scheme}/{path}: page scrolls sideways by {overflow} px")
            # The areas in Home Assistant's bar at the bottom; stamp hidden, buttons shown
            await _frame(phone, f"{scheme} phone", page_id=PAGES[-1].split("/")[-1], phone=True)
            expect(
                await phone.locator(f"{PANEL} #refresh").is_visible()
                and await phone.locator(f"{PANEL} #integration").is_visible(),
                f"{scheme} phone: refresh and integration button hidden",
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
                    await _goto(phone, instance, "uplinks/status")
                    await phone.screenshot(path=str(shots / "phone-dark.png"))
            await phone.context.close()

    await checks.run(
        "phone width and dark mode: areas at the bottom, no sideways scrolling, buttons fit",
        phone_and_dark,
    )

    async def iphone_safe_area() -> None:
        # An iPhone's safe area, set the way Home Assistant's own debug setting sets it; the frame
        # pads itself, so it fills the screen like Home Assistant's own pages
        phone = await _open(
            browser,
            instance,
            ADMIN,
            viewport={"width": 390, "height": 844},
            color_scheme="dark",
        )
        measured = {}
        for name, path in (
            ("Network Plus", "/network-plus/uplinks/status"),
            ("Automations", "/config/automation/dashboard"),
        ):
            await phone.goto(f"{instance.base}{path}")
            await phone.wait_for_load_state("networkidle")
            await phone.locator("#ha-launch-screen").wait_for(state="detached")
            await phone.evaluate(SAFE_AREA)
            await phone.wait_for_timeout(800)
            measured[name] = box = await phone.evaluate(SAFE_AREA_MEASURE)
            expect(isinstance(box, dict), f"{name}: {box}")
            height = box["viewport"]
            expect(box["frame"] == [0, height], f"{name}: frame {box['frame']} in {height}")
            expect(box["toolbar"] == 56 + 47, f"{name}: header {box['toolbar']} high")
            expect(
                box["tabbar"] is not None and box["tabbar"][1] == height,
                f"{name}: bar at the bottom {box['tabbar']} in {height}",
            )
            expect(box["tabbar"][0] >= 0, f"{name}: bar at the bottom {box['tabbar']}")
        print(f"     measured: {measured}")
        expect(
            measured["Network Plus"] == measured["Automations"],
            f"unlike Home Assistant's page: {measured}",
        )
        await phone.context.close()

    await checks.run(
        "iPhone safe area: header and bottom bar as on Home Assistant's own pages", iphone_safe_area
    )

    async def card_at(card_page: Page, view: str) -> Any:
        await card_page.goto(f"{instance.base}/{DASHBOARD}/{view}")
        cards = card_page.locator(CARD)
        await cards.first.locator("ha-card").wait_for(timeout=90000)
        await card_page.locator("#ha-launch-screen").wait_for(state="detached")
        return cards

    # A card made in the page from a changed copy of the user's hass: the failover
    # to the cellular backup, which the guest network is not assigned to
    changed_card = f"""(change) => {{
      const real = document.querySelector('home-assistant').hass;
      const own = Object.values(real.entities).filter((e) => e.platform === 'network_plus');
      const gateway = own.filter((e) => e.translation_key === 'active_uplink')
        .sort((a, b) => a.entity_id.localeCompare(b.entity_id))[0];
      const states = {{ ...real.states }};
      const set = (id, state, attributes = {{}}) => {{
        states[id] = {{ ...states[id], state, attributes: {{ ...states[id].attributes, ...attributes }} }};
      }};
      const of = (key) => own.find((e) => e.translation_key === key && e.device_id === gateway.device_id);
      const lists = real.states[gateway.entity_id].attributes;
      const [wan, lte] = lists.uplinks;
      const guest = lists.networks.find((n) => n.name === 'Guest');
      set(wan.entity_id, 'off');
      if (change.offline) set(lte.entity_id, 'off');
      set(gateway.entity_id, change.offline ? 'unknown' : lte.key);
      set(of('backup_active').entity_id, change.offline ? 'unknown' : 'on');
      set(of('active_uplink_since').entity_id, '2026-01-05T09:30:00+00:00');
      set(guest.entity_id, 'off');
      if (change.drill) set(of('enforce_backup').entity_id, 'on');
      const card = document.createElement('{CARD}');
      card.setConfig({{ size: change.size }});
      document.body.appendChild(card);
      card.hass = {{ ...real, states }};
      const root = card.shadowRoot;
      const text = (selector) => [...root.querySelectorAll(selector)].map((el) => el.textContent.trim());
      const result = {{
        lines: JSON.parse(root.querySelector('.small .sub')?.dataset.lines || '[]'),
        pill: text('.pill')[0],
        tone: root.querySelector('.head, .small').className,
        warnings: text('.row .value.warning').length,
        line: text('.line.warning'),
        data: text('.details .row .name'),
      }};
      card.remove();
      return result;
    }}"""

    async def card_for_every_user() -> None:
        # A user without administrator rights: the card reads entities only
        card_page = await _open(browser, instance, USER)
        try:
            cards = await card_at(card_page, "large")
            first = cards.nth(0)
            await first.locator(".details").wait_for()
            expect((await first.locator(".title").inner_text()).strip() == "Internet", "the title")
            expect(
                (await first.locator(".pill").inner_text()).strip() == "Internet is working",
                "the state",
            )
            expect("warning" not in await first.locator(".head").get_attribute("class"), "a colour")
            names = await first.locator(".row .name").all_inner_texts()
            expect(
                names == ["Main line", "Mobile backup", "Default", "Guest", "Mobile data"],
                f"the large card's rows: {names}",
            )
            expect(await first.locator(".row .tag").all_inner_texts() == ["in use"], "one in use")
            # Home Assistant writes a no-break space before a unit
            values = [
                value.replace("\xa0", " ")
                for value in await first.locator(".row .value").all_inner_texts()
            ]
            expect(
                values[:4] == ["Connected"] * 4, f"the states in Home Assistant's words: {values}"
            )
            expect(values[4] == "18.4% of 10.0 GB used", f"the backup's data: {values[4]}")
            expect(await first.locator(".value.warning, .line").count() == 0, "a warning at rest")
            expect(
                await first.locator("button, ha-button, input, select").count() == 0,
                "the card has something to change",
            )
            # A row opens Home Assistant's dialog of its entity, nothing else
            await first.locator(".row").first.click()
            await card_page.locator("ha-more-info-dialog").wait_for(state="attached")
            await card_page.keyboard.press("Escape")
            # The second console by its device, with a title and without the networks
            second = cards.nth(1)
            expect((await second.locator(".title").inner_text()).strip() == "Office", "the title")
            expect(
                "office" in str(await second.locator(".row").first.get_attribute("data-entity")),
                "the card of the second console shows the first",
            )
            expect(
                await second.locator(".label").all_inner_texts() == ["Connections"],
                "the networks, switched off",
            )
            names = await second.locator(".row .name").all_inner_texts()
            expect(
                names == ["WAN", "LTE Failover WAN", "Mobile data", "Response time", "Last change"],
                f"the names from UniFi Network and the parts switched on: {names}",
            )
            latency = (await second.locator(".row .value").nth(3).inner_text()).replace("\xa0", " ")
            expect(latency == "12 ms", f"the response time: {latency}")
            # The card picker knows the card, and its editor has a field for every option
            offered = await card_page.evaluate(
                f"""() => {{
                  const form = customElements.get('{CARD}').getConfigForm();
                  const names = (schema) => schema.flatMap((f) => f.schema ? names(f.schema) : [f.name]);
                  return {{
                    picker: (window.customCards || []).filter((c) => c.type === '{CARD}').length,
                    fields: names(form.schema),
                    labels: names(form.schema).map((name) => form.computeLabel({{ name }})),
                  }};
                }}"""
            )
            expect(offered["picker"] == 1, "the card is not in the card picker once")
            expect(
                set(offered["fields"])
                >= {
                    "title",
                    "size",
                    "device",
                    "show_status",
                    "show_names",
                    "show_data",
                    "show_problems",
                },
                f"the editor's fields: {offered['fields']}",
            )
            expect(
                not set(offered["fields"]) & set(offered["labels"]),
                f"an editor field without its text: {offered['labels']}",
            )
            # What does not fit is left out: one row, three rows, six rows
            cards = await card_at(card_page, "sizes")
            small, medium, large = cards.nth(0), cards.nth(1), cards.nth(2)
            await large.locator(".details").wait_for()
            expect(await small.locator(".small").count() == 1, "one row is not the small card")
            expect(
                (await small.locator(".small .sub").inner_text()).strip() == "Internet is working",
                "the small card's second line",
            )
            expect(
                await medium.locator(".label").all_inner_texts() == ["Connections"],
                "three rows are not the medium card",
            )
            expect(await medium.locator(".details").count() == 0, "three rows with the details")
            expect(await large.locator(".label").count() == 2, "six rows without the networks")
            for card in (small, medium, large):
                fits = await card.locator("ha-card").evaluate(
                    "el => el.querySelector('.content').getBoundingClientRect().bottom"
                    " <= el.getBoundingClientRect().bottom + 1"
                )
                expect(fits, "a card that cuts off what it shows")
            # Given no rows, a card is as high as what it shows
            plain = cards.nth(3)
            await plain.locator(".details").wait_for()
            spare = await plain.evaluate(
                "el => { const card = el.shadowRoot.querySelector('ha-card');"
                " const style = getComputedStyle(card);"
                " return card.clientHeight - parseFloat(style.paddingTop)"
                " - parseFloat(style.paddingBottom)"
                " - card.querySelector('.content').scrollHeight; }"
            )
            expect(abs(spare) <= 2, f"a card without rows is {spare} px off its content")
            # Half as wide in one row, the second line is not cut off
            narrow = cards.nth(4).locator(".small .sub")
            cut = await narrow.evaluate("el => el.scrollWidth > el.clientWidth")
            expect(not cut, "a second line that is cut off")
            if shots is not None:
                await card_page.mouse.move(0, 0)
                await card_page.wait_for_timeout(500)
                await card_page.locator("hui-grid-section").first.screenshot(
                    path=str(shots / "card.png")
                )
            # On the cellular backup: a warning, the guest network cut off, the time it began
            backup = await card_page.evaluate(changed_card, {"size": "small"})
            expect(
                backup["lines"][0].startswith(
                    "Running on mobile data · Guest without internet · since "
                )
                and backup["lines"][-1] == "Running on mobile data",
                f"the one row on the backup: {backup['lines']}",
            )
            expect("warning" in backup["tone"], "the backup without its colour")
            backup = await card_page.evaluate(changed_card, {"size": "large"})
            expect(
                backup["pill"].startswith("Running on mobile data · since "),
                f"the state: {backup['pill']}",
            )
            expect(backup["warnings"] == 2, "the uplink that is down and the network cut off")
            backup = await card_page.evaluate(changed_card, {"size": "medium"})
            expect(backup["line"] == ["Guest without internet"], f"the medium card: {backup}")
            expect(backup["data"] == ["Mobile data"], "the backup's data while it is used up")
            # A backup switched on by hand is a drill, and no warning
            drill = await card_page.evaluate(changed_card, {"size": "large", "drill": True})
            expect(
                drill["pill"].startswith("Test: running on mobile data"),
                f"the drill: {drill['pill']}",
            )
            expect("warning" not in drill["tone"], "a drill as a warning")
            # Every uplink down
            offline = await card_page.evaluate(changed_card, {"size": "large", "offline": True})
            expect(offline["pill"] == "No internet", f"every uplink down: {offline['pill']}")
            expect("error" in offline["tone"], "no internet without its colour")
        finally:
            await card_page.context.close()

    await checks.run(
        "the card shows every user the internet, in the size its space allows, nothing to change",
        card_for_every_user,
    )

    async def card_through_a_failover() -> None:
        # The real thing, as far as a made-up console goes: the cable pulled, the traffic
        # on the cellular backup, which the guest network is not assigned to, and back;
        # then the same switched on by hand, a drill
        console = instance.consoles[0]
        states = await api.ws(instance.token, {"type": "get_states"})
        switch = next(
            s["entity_id"]
            for s in states["result"]
            if s["entity_id"].startswith("switch.gateway") and "enforce" in s["entity_id"]
        )
        card_page = await _open(browser, instance, USER)

        async def poll() -> None:
            await api.post(
                instance.token,
                "/api/services/homeassistant/update_entity",
                {"entity_id": "sensor.gateway_active_uplink"},
            )

        async def shows(card: Any, start: str) -> None:
            """The card's state begins with ``start``; the next poll is asked for meanwhile."""

            async def there() -> bool:
                if (await card.locator(".pill").inner_text()).strip().startswith(start):
                    return True
                await poll()
                return False

            await _until(there, f"the card to say {start!r}", 90)

        async def rows(card: Any) -> dict[str, tuple[str, bool]]:
            found = {}
            for row in await card.locator(".row").all():
                value = row.locator(".value")
                found[(await row.locator(".name").inner_text()).strip()] = (
                    (await value.inner_text()).strip(),
                    "warning" in str(await value.get_attribute("class")),
                )
            return found

        try:
            cards = await card_at(card_page, "large")
            card = cards.nth(0)
            await shows(card, "Internet is working")
            console.cable = False
            console.switch()
            await shows(card, "Running on mobile data · since ")
            expect(
                "warning" in str(await card.locator(".head").get_attribute("class")), "no colour"
            )
            found = await rows(card)
            expect(found["Main line"] == ("Disconnected", True), f"the cable: {found['Main line']}")
            expect(
                found["Mobile backup"] == ("Connected", False),
                f"the backup: {found['Mobile backup']}",
            )
            expect(found["Guest"] == ("Disconnected", True), f"the guests: {found['Guest']}")
            expect(found["Default"] == ("Connected", False), f"the home: {found['Default']}")
            tagged = await card.locator(".row:has(.tag) .name").all_inner_texts()
            expect(tagged == ["Mobile backup"], f"the active uplink: {tagged}")
            if shots is not None:
                await card_page.mouse.move(0, 0)
                await card_page.wait_for_timeout(500)
                await card.locator("ha-card").screenshot(path=str(shots / "card-backup.png"))
            # The same in three rows and in one
            sized = await card_at(card_page, "sizes")
            small, medium = sized.nth(0), sized.nth(1)
            line = (await small.locator(".small .sub").inner_text()).strip()
            expect(
                line.startswith("Running on mobile data · Guest without internet"),
                f"the one row: {line}",
            )
            expect(
                (await medium.locator(".line.warning").inner_text()).strip()
                == "Guest without internet",
                "the medium card without the network that is cut off",
            )
            # Three rows have no room for the backup's data as well; a card without rows has
            expect(await medium.locator(".row").count() == 2, "the medium card's uplinks")
            expect(await medium.locator(".details").count() == 0, "three rows with the data")
            for sized_card in (small, medium):
                fits = await sized_card.locator("ha-card").evaluate(
                    "el => el.querySelector('.content').getBoundingClientRect().bottom"
                    " <= el.getBoundingClientRect().bottom + 1"
                )
                expect(fits, "a card on the backup that cuts off what it shows")
            # The cable back in
            cards = await card_at(card_page, "large")
            card = cards.nth(0)
            console.cable = True
            console.switch()
            await shows(card, "Internet is working")
            expect(await card.locator(".value.warning").count() == 0, "a warning left behind")
            expect("warning" not in str(await card.locator(".head").get_attribute("class")), "")
            # A drill: the preferred uplink switched off by an administrator
            console.fails_over = True
            await api.post(instance.token, "/api/services/switch/turn_on", {"entity_id": switch})
            await shows(card, "Test: running on mobile data · since ")
            expect(
                "warning" not in str(await card.locator(".head").get_attribute("class")),
                "a drill as a warning",
            )
            found = await rows(card)
            expect(
                not found["Main line"][1],
                f"the uplink switched off for the drill: {found['Main line']}",
            )
            expect(found["Guest"] == ("Disconnected", True), f"the guests: {found['Guest']}")
            # Switched on again, the uplink takes its time: still the test, and no warning
            console.cable = False
            await api.post(instance.token, "/api/services/switch/turn_off", {"entity_id": switch})
            await poll()
            await card_page.wait_for_timeout(12000)
            await poll()
            await card_page.wait_for_timeout(2000)
            pill = (await card.locator(".pill").inner_text()).strip()
            expect(
                pill.startswith("Test: running on mobile data"),
                f"the uplink on its way back after a drill: {pill}",
            )
            expect(
                "warning" not in str(await card.locator(".head").get_attribute("class")),
                "the way back from a drill as a warning",
            )
            console.cable = True
            console.switch()
            await shows(card, "Internet is working")
        finally:
            # Whatever failed, the next checks start on the cable
            console.cable = True
            if console.wan_type != "dhcp":
                await api.post(
                    instance.token, "/api/services/switch/turn_off", {"entity_id": switch}
                )
            console.fails_over = False

            async def on_cable() -> bool:
                await poll()
                now = await api.ws(instance.token, {"type": "get_states"})
                return any(
                    s["entity_id"] == "sensor.gateway_active_uplink" and s["state"] == "wan"
                    for s in now["result"]
                )

            await _until(on_cable, "the traffic back on the cable", 90)
            await card_page.context.close()

    await checks.run(
        "the card through a failover to the cellular backup and back, and through a drill",
        card_through_a_failover,
    )

    async def admin_only() -> None:
        token = await api.login(*USER)
        for command in ("network_plus/overview", "network_plus/about"):
            reply = await api.ws(token, {"type": command})
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
