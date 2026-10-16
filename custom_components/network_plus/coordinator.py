"""Gateway state from the websocket, checked by polling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    NetworkAuthError,
    NetworkClient,
    NetworkError,
    find_gateway,
    find_lte,
    is_lte_device,
)
from .const import DOMAIN, RECONNECT_MAX, RECONNECT_MIN, UPDATE_INTERVAL
from .model import (
    EVENT_FAILOVER,
    EVENT_RESTORED,
    NetworkState,
    UplinkEvent,
    detect_address_events,
    detect_events,
    essentials,
    parse_state,
)

_LOGGER = logging.getLogger(__name__)

# Websocket messages that carry device runtime objects
DEVICE_MESSAGES = frozenset({"device:sync", "device:update"})


@dataclass(slots=True)
class RuntimeData:
    """Objects shared by the platforms of one entry."""

    client: NetworkClient
    coordinator: NetworkCoordinator
    gateway_device_id: str


type NetworkConfigEntry = ConfigEntry[RuntimeData]


class NetworkCoordinator(DataUpdateCoordinator[NetworkState]):
    """Keep the gateway's state; tell listeners what happened in between.

    The websocket pushes the runtime objects of the gateway and the LTE device
    about once a second. A push updates the state only when something
    essential changed (an uplink, the active uplink, an address, the LTE
    state), so a failover shows at once. Everything else — latency, speed
    test, data usage, remote clients, uplink names — is sampled by polling
    every minute.
    """

    config_entry: NetworkConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NetworkConfigEntry,
        client: NetworkClient,
        mac: str,
    ) -> None:
        """Initialize the coordinator for the gateway with ``mac``."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.mac = mac
        self.websocket_connected = False
        self._configs: list[dict[str, Any]] = []
        self._gateway_raw: dict[str, Any] = {}
        self._lte_raw: dict[str, Any] | None = None
        self._clients: list[dict[str, Any]] | None = None
        self._event_listeners: list[Callable[[UplinkEvent], None]] = []
        # Last address seen per (uplink, family), for changes across an outage
        self._known_addresses: dict[tuple[str, str], str] = {}
        # When the active uplink was last seen changing
        self._switch_seen_at: datetime | None = None

    async def _async_update_data(self) -> NetworkState:
        try:
            devices = await self.client.get_devices()
            self._configs = await self.client.get_wan_configs()
        except NetworkAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except NetworkError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        gateway = find_gateway(devices, self.mac)
        if gateway is None:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="gateway_missing")
        self._gateway_raw = gateway
        self._lte_raw = find_lte(devices)
        try:
            self._clients = await self.client.get_active_clients()
        except NetworkAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except NetworkError as err:
            # A console without this endpoint still reports its uplinks;
            # the remote clients are unknown then.
            _LOGGER.debug("Active clients not readable: %s", err)
            self._clients = None
        state = self._build()
        self._notify(state)
        return state

    def _build(self) -> NetworkState:
        return parse_state(self._gateway_raw, self._configs, self._lte_raw, self._clients)

    @callback
    def async_add_event_listener(self, listener: Callable[[UplinkEvent], None]) -> CALLBACK_TYPE:
        """Call ``listener`` for every event; return the function that stops it."""
        self._event_listeners.append(listener)

        @callback
        def _remove() -> None:
            self._event_listeners.remove(listener)

        return _remove

    @callback
    def _notify(self, state: NetworkState) -> None:
        """Pass the events between the last known state and ``state`` on."""
        # The first state only seeds the known addresses
        events = detect_address_events(self._known_addresses, state)
        # None before the first refresh, although the type says otherwise
        previous: NetworkState | None = self.data
        if previous is not None:
            events = detect_events(previous, state, self._switch_seen_at) + events
            if any(e.type in (EVENT_FAILOVER, EVENT_RESTORED) for e in events):
                self._switch_seen_at = dt_util.utcnow()
        for event in events:
            for listener in list(self._event_listeners):
                listener(event)

    @callback
    def handle_message(self, message: dict[str, Any]) -> None:
        """Take the runtime objects of the gateway and the LTE device from a websocket message."""
        meta = message.get("meta")
        if not isinstance(meta, dict) or meta.get("message") not in DEVICE_MESSAGES:
            return
        data = message.get("data")
        changed = False
        for raw in data if isinstance(data, list) else []:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("mac", "")).lower() == self.mac and "last_wan_status" in raw:
                self._gateway_raw = raw
                changed = True
            elif is_lte_device(raw):
                self._lte_raw = raw
                changed = True
        if not changed or not self._gateway_raw:
            return
        # The poll decides whether the console is healthy; a push only refines
        # a healthy state, so a failing poll is not hidden behind push data
        if not self.last_update_success:
            return
        state = self._build()
        previous: NetworkState | None = self.data
        if previous is None or essentials(state) != essentials(previous):
            self._notify(state)
            self.async_set_updated_data(state)

    async def async_listen(self) -> None:
        """Keep the websocket connected for as long as the entry is loaded."""
        delay = RECONNECT_MIN
        while True:
            try:
                await self.client.listen(self.handle_message, self._on_connect)
            except NetworkError as err:
                _LOGGER.debug("Websocket ended: %s", err)
            else:
                _LOGGER.debug("Websocket closed")
            # A connection that stood is retried soon, however it ended;
            # only failed attempts wait longer each time
            worked = self.websocket_connected
            self.websocket_connected = False
            if worked:
                delay = RECONNECT_MIN
            await asyncio.sleep(delay)
            if not worked:
                delay = min(delay * 2, RECONNECT_MAX)

    @callback
    def _on_connect(self) -> None:
        self.websocket_connected = True
