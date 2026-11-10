"""Network Plus: what the UniFi integration of Home Assistant does not show.

A config entry is one UniFi OS console with its gateway.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging

from aiohttp import DummyCookieJar

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import NetworkAuthError, NetworkClient, NetworkError
from .const import (
    CONF_ADMIN_PASSWORD,
    CONF_ADMIN_USERNAME,
    CONF_CERT_FINGERPRINT,
    CONF_HISTORY_DAYS,
    CONF_USE_ADMIN,
    DEFAULT_HISTORY_DAYS,
    DOMAIN,
)
from .coordinator import NetworkConfigEntry, NetworkCoordinator, RuntimeData
from .entity import gateway_device_info
from .frontend import async_register_frontend, async_unregister_panel
from .history import History
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the WebSocket command of the page."""
    await async_register_websocket(hass)
    return True


def create_client(hass: HomeAssistant, data: Mapping[str, object]) -> NetworkClient:
    """Return a client with its own session; the client keeps the login token itself.

    With a pinned fingerprint only that certificate is taken; without one the
    certificate check decides (on by default: a chain the system trusts).
    """
    session = async_create_clientsession(
        hass,
        verify_ssl=bool(data.get(CONF_VERIFY_SSL, True)),
        # The console deletes the token cookie with every 401, also with a late
        # one after a new login; the client sends the token itself
        cookie_jar=DummyCookieJar(),
    )
    return NetworkClient(
        session,
        str(data[CONF_HOST]),
        str(data[CONF_USERNAME]),
        str(data[CONF_PASSWORD]),
        fingerprint=str(data.get(CONF_CERT_FINGERPRINT) or "") or None,
    )


async def async_setup_entry(hass: HomeAssistant, entry: NetworkConfigEntry) -> bool:
    """Set up one console."""
    client = create_client(hass, entry.data)
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
    history = History(
        hass, entry.entry_id, int(entry.options.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS))
    )
    await history.async_load()
    # Unload runs these last first: the history stops listening, then writes
    entry.async_on_unload(history.async_flush)
    entry.async_on_unload(history.attach(coordinator))
    entry.runtime_data = RuntimeData(
        client=client,
        coordinator=coordinator,
        gateway_device_id=device.id,
        history=history,
        admin_client=admin_client,
    )
    if admin_client is not None:
        await _async_check_admin(coordinator, admin_client)
        # An enforced backup from before the restart ends on time, or at once
        coordinator.arm_auto_off_after_restart()
        entry.async_on_unload(coordinator.cancel_auto_off)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_frontend(hass)
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
    """Unload one console; the websocket task ends with the entry.

    A running speed test over the backup uplink ends first, while the admin
    session still stands, so it switches the preferred uplink on again.
    """
    await entry.runtime_data.coordinator.async_stop_backup_speedtest()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NetworkConfigEntry) -> None:
    """Drop the history and the repair issues of a console that was removed.

    The page goes with the last console.
    """
    await History(hass, entry.entry_id, DEFAULT_HISTORY_DAYS).async_remove()
    if not hass.config_entries.async_entries(DOMAIN):
        async_unregister_panel(hass)
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and issue_id.endswith(entry.entry_id):
            registry.async_delete(DOMAIN, issue_id)
