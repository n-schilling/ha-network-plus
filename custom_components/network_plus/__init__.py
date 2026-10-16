"""Network Plus: what the UniFi integration of Home Assistant does not show.

A config entry is one UniFi OS console with its gateway.
"""

from __future__ import annotations

import logging

from aiohttp import CookieJar

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import NetworkAuthError, NetworkClient, NetworkError
from .const import CONF_ADMIN_PASSWORD, CONF_ADMIN_USERNAME, CONF_USE_ADMIN, DOMAIN
from .coordinator import NetworkConfigEntry, NetworkCoordinator, RuntimeData
from .entity import gateway_device_info

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]


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
    # Home Assistant detaches the session with the entry as well, also after
    # a failed setup; this makes it visible and independent of that
    entry.async_on_unload(client.detach)
    admin_client = None
    if entry.data.get(CONF_USE_ADMIN) and entry.data.get(CONF_ADMIN_USERNAME):
        # The admin opt-in: its own user, its own session, used only for writes
        admin_client = create_client(
            hass,
            {
                **entry.data,
                CONF_USERNAME: entry.data[CONF_ADMIN_USERNAME],
                CONF_PASSWORD: entry.data[CONF_ADMIN_PASSWORD],
            },
        )
        entry.async_on_unload(admin_client.detach)
    # The config flow sets the gateway's MAC as unique ID
    coordinator = NetworkCoordinator(hass, entry, client, str(entry.unique_id), admin_client)
    await coordinator.async_config_entry_first_refresh()
    # The gateway's device is created here so the LTE device can hang below it
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **gateway_device_info(coordinator.data.gateway, entry.title),
    )
    entry.runtime_data = RuntimeData(
        client=client,
        coordinator=coordinator,
        gateway_device_id=device.id,
        admin_client=admin_client,
    )
    if admin_client is not None:
        await _async_check_admin(coordinator, admin_client)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, coordinator.async_listen(), "network_plus websocket")
    return True


async def _async_check_admin(coordinator: NetworkCoordinator, admin_client: NetworkClient) -> None:
    """Raise or clear the repair issue about an admin user that cannot write."""
    try:
        allowed = await admin_client.can_manage_network()
    except NetworkAuthError:
        coordinator.note_admin_permission(False, "the console rejected the admin credentials")
    except NetworkError as err:
        # Nothing known either way; the next setup checks again
        _LOGGER.debug("Admin rights not checked: %s", err)
    else:
        coordinator.note_admin_permission(allowed, "the user lacks the admin role")


async def async_unload_entry(hass: HomeAssistant, entry: NetworkConfigEntry) -> bool:
    """Unload one console; the websocket task ends with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NetworkConfigEntry) -> None:
    """Drop the repair issues of a console that was removed."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.endswith(entry.entry_id):
            registry.async_delete(DOMAIN, issue_id)
