"""Serves the page and the card: the page for administrators, the card for every dashboard.

Each script URL carries a hash of its content, so a browser never keeps an
old page or card after an update.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from homeassistant.components import panel_custom
from homeassistant.components.frontend import (
    DATA_EXTRA_MODULE_URL,
    add_extra_js_url,
    async_panel_exists,
    async_remove_panel,
)
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.hass_dict import HassKey

from .const import (
    CARD_FILENAME,
    DOMAIN,
    FRONTEND_URL_BASE,
    PANEL_ELEMENT,
    PANEL_FILENAME,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

DATA_STATIC: HassKey[bool] = HassKey(f"{DOMAIN}_static")
DATA_CARD: HassKey[str] = HassKey(f"{DOMAIN}_card")
PANEL_PATH = f"{FRONTEND_URL_BASE}/{PANEL_FILENAME}"
CARD_PATH = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}"


async def _async_serve(hass: HomeAssistant) -> None:
    """Serve the scripts, once per run of Home Assistant."""
    if DATA_STATIC not in hass.data:
        hass.data[DATA_STATIC] = True
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL_BASE, str(Path(__file__).parent / "frontend"), False)]
        )


async def async_register_card(hass: HomeAssistant) -> None:
    """Load the card on every page and register it as a dashboard resource.

    Loading happens once per run of Home Assistant: a card cannot be taken
    back. The resource is checked on every setup, so a console added again
    after a removal gets it back.
    """
    await _async_serve(hass)
    if (url := hass.data.get(DATA_CARD)) is None:
        script = Path(__file__).parent / "frontend" / CARD_FILENAME
        digest = await hass.async_add_executor_job(_digest, script)
        # The content hash makes browsers fetch every changed card, also within a version
        url = hass.data[DATA_CARD] = f"{CARD_PATH}?v={digest}"
        # The frontend is an after-dependency: present in every normal installation
        if DATA_EXTRA_MODULE_URL in hass.data:
            add_extra_js_url(hass, url)
    if (found := await _async_card_resources(hass)) is None:
        return
    resources, items = found
    for item in items:
        if item["url"] != url:
            await resources.async_update_item(item["id"], {"url": url})
        return
    await resources.async_create_item({"res_type": "module", "url": url})


async def async_remove_card_resource(hass: HomeAssistant) -> None:
    """Remove the card from the dashboard resources, with the last console."""
    if (found := await _async_card_resources(hass)) is None:
        return
    resources, items = found
    for item in items:
        await resources.async_delete_item(item["id"])


async def _async_card_resources(
    hass: HomeAssistant,
) -> tuple[ResourceStorageCollection, list[dict[str, Any]]] | None:
    """Return the dashboard resources and the card's entries among them.

    None where dashboards keep their resources in YAML: those are the user's.
    """
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or lovelace.resource_mode != MODE_STORAGE:
        return None
    resources = lovelace.resources
    if not isinstance(resources, ResourceStorageCollection):
        return None
    # Loads the collection when nothing did so far
    await resources.async_get_info()
    return resources, [
        item
        for item in resources.async_items()
        if str(item.get("url", "")).split("?", 1)[0] == CARD_PATH
    ]


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the page once per run of Home Assistant and add it to the sidebar.

    The page stays through a reload of an entry, so it is not thrown out
    while an option changes; removing the last console removes it.
    """
    await _async_serve(hass)
    if async_panel_exists(hass, PANEL_URL_PATH):
        return
    digest = await hass.async_add_executor_job(
        _digest, Path(__file__).parent / "frontend" / PANEL_FILENAME
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        # The content hash makes browsers fetch every changed page, also within a version
        module_url=f"{PANEL_PATH}?v={digest}",
        require_admin=True,
        # Home Assistant's frame hass-tabs-subpage pads itself by the safe area (iPhone)
        handle_safe_area=True,
    )


def _digest(path: Path) -> str:
    """Return a short hash of the page's script."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    """Take the page off the sidebar."""
    async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
