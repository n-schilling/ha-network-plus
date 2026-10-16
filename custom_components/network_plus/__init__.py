"""Network Plus: what the UniFi integration of Home Assistant does not show.

A config entry is one UniFi OS console with its gateway.
"""

from __future__ import annotations

from aiohttp import CookieJar

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import NetworkClient
from .coordinator import NetworkConfigEntry, NetworkCoordinator, RuntimeData
from .entity import gateway_device_info

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]


def create_client(hass: HomeAssistant, data: dict[str, object]) -> NetworkClient:
    """Return a client with its own session: the console's login is a cookie."""
    session = async_create_clientsession(
        hass,
        verify_ssl=bool(data.get(CONF_VERIFY_SSL, True)),
        # Accept the cookie also when the console is addressed by IP
        cookie_jar=CookieJar(unsafe=True),
    )
    return NetworkClient(
        session, str(data[CONF_HOST]), str(data[CONF_USERNAME]), str(data[CONF_PASSWORD])
    )


async def async_setup_entry(hass: HomeAssistant, entry: NetworkConfigEntry) -> bool:
    """Set up one console."""
    client = create_client(hass, dict(entry.data))
    # The config flow sets the gateway's MAC as unique ID
    coordinator = NetworkCoordinator(hass, entry, client, str(entry.unique_id))
    await coordinator.async_config_entry_first_refresh()
    # The gateway's device is created here so the LTE device can hang below it
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **gateway_device_info(coordinator.data.gateway, entry.title),
    )
    entry.runtime_data = RuntimeData(
        client=client, coordinator=coordinator, gateway_device_id=device.id
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, coordinator.async_listen(), "network_plus websocket")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NetworkConfigEntry) -> bool:
    """Unload one console; the websocket task ends with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
