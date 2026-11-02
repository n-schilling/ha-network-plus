"""Serves the page and puts it into the sidebar of administrators."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.frontend import async_panel_exists, async_remove_panel
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey

from .const import (
    DOMAIN,
    FRONTEND_URL_BASE,
    PANEL_ELEMENT,
    PANEL_FILENAME,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

DATA_STATIC: HassKey[bool] = HassKey(f"{DOMAIN}_static")
PANEL_PATH = f"{FRONTEND_URL_BASE}/{PANEL_FILENAME}"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the page once per run of Home Assistant and add it to the sidebar.

    The page stays through a reload of an entry, so it is not thrown out
    while an option changes; removing the last console removes it.
    """
    if DATA_STATIC not in hass.data:
        hass.data[DATA_STATIC] = True
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL_BASE, str(Path(__file__).parent / "frontend"), False)]
        )
    if async_panel_exists(hass, PANEL_URL_PATH):
        return
    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{PANEL_PATH}?v={integration.version}",
        require_admin=True,
    )


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    """Take the page off the sidebar."""
    async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
