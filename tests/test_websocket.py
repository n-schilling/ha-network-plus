"""Tests for the page: its WebSocket command, the panel and the shipped script."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import WebSocketGenerator

from homeassistant.components.frontend import DATA_PANELS
from homeassistant.core import HomeAssistant

from custom_components.network_plus.api import NetworkError
from custom_components.network_plus.const import DOMAIN, PANEL_URL_PATH

from .conftest import ADMIN_DATA, ENTRY_DATA, MAC, FakeConsole, gateway, lte_device

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "network_plus"
PANEL = PACKAGE / "frontend" / "network-plus-panel.js"


async def test_overview(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """The page gets the consoles, the uplinks now, the LTE backup and the history."""
    hass.config_entries.async_update_entry(setup_admin_entry, options={"expected_speed_wan": 2500})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "network_plus/overview"})
    result = (await client.receive_json())["result"]
    assert result["consoles"] == [
        {"entry_id": setup_admin_entry.entry_id, "title": "Gateway", "loaded": True}
    ]
    console = result["console"]
    assert (console["title"], console["active"], console["primary"]) == ("Gateway", "WAN", "WAN")
    assert console["admin"] is True
    assert console["backup_enforced"] is False
    assert console["gateway"] == {"model": "UDMPRO", "version": "4.0.0"}
    cable, lte = console["uplinks"]
    assert cable["key"] == "WAN"
    assert (cable["link_speed"], cable["full_duplex"], cable["expected_speed"]) == (
        2500,
        True,
        2500,
    )
    assert (cable["latency"], cable["ipv4"]) == (14, "198.51.100.7")
    assert lte["key"] == "WAN_LTE_FAILOVER" and lte["expected_speed"] is None
    assert console["lte"][0]["name"] == "LTE backup"
    assert console["lte"][0]["limit_bytes"] == 8_000_000_000
    assert console["speedtest_running"] is False
    assert console["remote"] == [
        {"name": "Phone", "type": "TELEPORT", "since": "2023-11-14T22:13:20+00:00"}
    ]
    assert set(console["history"]) == {"days", "events", "speedtests", "drills", "sessions"}
    # Asked for by its ID, the same console
    await client.send_json_auto_id(
        {"type": "network_plus/overview", "entry_id": setup_admin_entry.entry_id}
    )
    assert (await client.receive_json())["result"]["console"]["title"] == "Gateway"


async def test_overview_unknown_unloaded_and_none(
    hass: HomeAssistant,
    console: FakeConsole,
    setup_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """An unknown console is an error, an unloaded one has no state, without any nothing."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "network_plus/overview", "entry_id": "nope"})
    response = await client.receive_json()
    assert response["error"]["code"] == "not_found"
    # Unknown LTE limit and remote clients pass as None
    console.clients_error = Exception("no")
    await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    await client.send_json_auto_id({"type": "network_plus/overview"})
    result = (await client.receive_json())["result"]
    assert result["consoles"][0]["loaded"] is False
    assert result["console"] is None
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    await client.send_json_auto_id({"type": "network_plus/overview"})
    assert (await client.receive_json())["result"] == {
        "consoles": [],
        "console": None,
        "version": _version(),
    }


async def test_overview_without_clients_and_lte_limit(
    hass: HomeAssistant,
    console: FakeConsole,
    setup_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Remote clients the console does not tell are None; so is a limit that is off."""
    console.clients_error = NetworkError("no")
    console.devices[2] = lte_device(limit=False)
    await setup_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "network_plus/overview"})
    console_state = (await client.receive_json())["result"]["console"]
    assert console_state["remote"] is None
    assert console_state["lte"][0]["limit_bytes"] is None
    assert console_state["lte"][0]["period_start"] is None


async def test_overview_is_for_admins(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """Other users get nothing: the page names addresses and remote clients."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "network_plus/overview"})
    assert (await client.receive_json())["error"]["code"] == "unauthorized"


async def test_panel_stays_through_a_reload_and_goes_with_the_last_console(
    hass: HomeAssistant, console: FakeConsole
) -> None:
    """The page is in the sidebar for administrators while a console is set up."""
    console.devices.append({**gateway(), "mac": "02:00:00:00:00:09"})
    first = MockConfigEntry(domain=DOMAIN, title="Gateway", unique_id=MAC, data=ENTRY_DATA)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Other",
        unique_id="02:00:00:00:00:09",
        data={**ENTRY_DATA, **ADMIN_DATA},
    )
    for entry in (first, second):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    panel = hass.data[DATA_PANELS][PANEL_URL_PATH]
    assert panel.require_admin is True
    assert panel.config["_panel_custom"]["name"] == "network-plus-panel"
    digest = hashlib.sha256(PANEL.read_bytes()).hexdigest()[:12]
    assert panel.config["_panel_custom"]["module_url"].endswith(f"?v={digest}")
    await hass.config_entries.async_reload(first.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DATA_PANELS][PANEL_URL_PATH] is panel
    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    assert PANEL_URL_PATH in hass.data[DATA_PANELS]
    await hass.config_entries.async_remove(second.entry_id)
    await hass.async_block_till_done()
    assert PANEL_URL_PATH not in hass.data[DATA_PANELS]


def _version() -> str:
    return str(json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))["version"])


def test_panel_script() -> None:
    """The script defines the element and asks the command; the version comes from there."""
    code = PANEL.read_text(encoding="utf-8")
    assert 'customElements.define("network-plus-panel"' in code
    assert '"network_plus/overview"' in code
    # manifest.json is the one place of the version
    assert re.search(r"\d+\.\d+\.\d+", code.replace("@mdi/js", "")) is None
    # Everything comes from Home Assistant: no requests of its own
    assert "fetch(" not in code and "http" not in code.replace("https://github.com", "")
