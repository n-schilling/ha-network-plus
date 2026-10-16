"""Gateway state from the websocket, checked by polling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NetworkAuthError, NetworkClient, NetworkError
from .const import DOMAIN, RECONNECT_MAX, RECONNECT_MIN, UPDATE_INTERVAL
from .model import NetworkState, UplinkEvent, detect_events, parse_state

_LOGGER = logging.getLogger(__name__)

# Websocket messages that carry device runtime objects
DEVICE_MESSAGES = frozenset({"device:sync", "device:update"})


@dataclass(slots=True)
class RuntimeData:
    """Objects shared by the platforms of one entry."""

    client: NetworkClient
    coordinator: NetworkCoordinator


type NetworkConfigEntry = ConfigEntry[RuntimeData]


class NetworkCoordinator(DataUpdateCoordinator[NetworkState]):
    """Keep the gateway's state; tell listeners what happened in between.

    The websocket pushes the gateway's runtime object about once a second.
    Polling runs only when nothing changed for a minute: it fills gaps while
    the websocket reconnects and refreshes the uplink names.
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
        self._event_listeners: list[Callable[[UplinkEvent], None]] = []

    async def _async_update_data(self) -> NetworkState:
        try:
            gateway = await self.client.get_gateway(self.mac)
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
        if gateway is None:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="gateway_missing")
        state = parse_state(gateway, self._configs)
        self._notify(state)
        return state

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
        # None before the first refresh, although the type says otherwise
        previous: NetworkState | None = self.data
        if previous is None:
            return
        for event in detect_events(previous, state):
            for listener in list(self._event_listeners):
                listener(event)

    @callback
    def handle_message(self, message: dict[str, Any]) -> None:
        """Take the gateway's runtime object from a websocket message."""
        meta = message.get("meta")
        if not isinstance(meta, dict) or meta.get("message") not in DEVICE_MESSAGES:
            return
        data = message.get("data")
        for raw in data if isinstance(data, list) else []:
            if not isinstance(raw, dict) or str(raw.get("mac", "")).lower() != self.mac:
                continue
            if "last_wan_status" not in raw:
                continue
            state = parse_state(raw, self._configs)
            if state != self.data or not self.last_update_success:
                self._notify(state)
                self.async_set_updated_data(state)

    async def async_listen(self) -> None:
        """Keep the websocket connected for as long as the entry is loaded."""
        delay = RECONNECT_MIN
        while True:
            try:
                await self.client.listen(self._on_message)
            except NetworkError as err:
                _LOGGER.debug("Websocket not connected: %s", err)
            else:
                _LOGGER.debug("Websocket closed")
                delay = RECONNECT_MIN
            self.websocket_connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    @callback
    def _on_message(self, message: dict[str, Any]) -> None:
        self.websocket_connected = True
        self.handle_message(message)
