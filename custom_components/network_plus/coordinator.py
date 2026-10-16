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
    find_lte_devices,
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

# Websocket messages with device runtime objects: a sync carries the whole
# object, an update only the fields that changed
MESSAGE_SYNC = "device:sync"
MESSAGE_UPDATE = "device:update"


@dataclass(slots=True)
class RuntimeData:
    """Objects shared by the platforms of one entry."""

    client: NetworkClient
    coordinator: NetworkCoordinator
    gateway_device_id: str


type NetworkConfigEntry = ConfigEntry[RuntimeData]


class NetworkCoordinator(DataUpdateCoordinator[NetworkState]):
    """Keep the gateway's state; tell listeners what happened in between.

    The websocket pushes the runtime objects of the gateway and the LTE
    devices about once a second. A push updates the state only when something
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
        self._lte_raws: dict[str, dict[str, Any]] = {}
        self._clients: list[dict[str, Any]] | None = None
        # Accepted pushes; a poll that overlapped one keeps the pushed objects
        self._pushes = 0
        self._event_listeners: list[Callable[[UplinkEvent], None]] = []
        # Last address seen per (uplink, family), for changes across an outage
        self._known_addresses: dict[tuple[str, str], str] = {}
        # When the active uplink was last seen changing
        self._switch_seen_at: datetime | None = None

    async def _async_update_data(self) -> NetworkState:
        pushes_before = self._pushes
        # The three requests are independent; exceptions come back as values
        results: tuple[Any, Any, Any] = await asyncio.gather(
            self.client.get_devices(),
            self.client.get_wan_configs(),
            self.client.get_active_clients(),
            return_exceptions=True,
        )
        for result in results[:2]:
            if isinstance(result, BaseException):
                raise _poll_error(result)
        devices: list[dict[str, Any]] = results[0]
        configs: list[dict[str, Any]] = results[1]
        clients = results[2]
        if isinstance(clients, NetworkAuthError):
            raise _poll_error(clients)
        if isinstance(clients, NetworkError):
            # A console without this endpoint still reports its uplinks;
            # the remote clients are unknown then.
            _LOGGER.debug("Active clients not readable: %s", clients)
            self._clients = None
        elif isinstance(clients, BaseException):
            raise clients
        else:
            self._clients = clients

        gateway = find_gateway(devices, self.mac)
        if gateway is None:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="gateway_missing")
        self._configs = configs
        lte_raws = {mac: raw for raw in find_lte_devices(devices) if (mac := _mac(raw))}
        if self._pushes == pushes_before:
            self._gateway_raw = gateway
            self._lte_raws = lte_raws
        else:
            # A push arrived while the poll ran: its objects are newer than
            # the poll's snapshot, which must not turn a seen failover back
            for mac, raw in lte_raws.items():
                self._lte_raws.setdefault(mac, raw)
        state = self._build()
        previous: NetworkState | None = self.data
        if previous is not None and _renamed(previous, state):
            # Entity names carry the uplink names; a reload renews them
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        self._notify(state)
        return state

    def _build(self) -> NetworkState:
        return parse_state(
            self._gateway_raw, self._configs, list(self._lte_raws.values()), self._clients
        )

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
        """Take the runtime objects of the gateway and the LTE devices from a websocket message."""
        meta = message.get("meta")
        kind = meta.get("message") if isinstance(meta, dict) else None
        if kind not in (MESSAGE_SYNC, MESSAGE_UPDATE):
            return
        partial = kind == MESSAGE_UPDATE
        data = message.get("data")
        changed = False
        for raw in data if isinstance(data, list) else []:
            if not isinstance(raw, dict) or not (mac := _mac(raw)):
                continue
            if mac == self.mac:
                if partial:
                    raw = {**self._gateway_raw, **raw}
                if "last_wan_status" in raw:
                    self._gateway_raw = raw
                    changed = True
            elif mac in self._lte_raws or is_lte_device(raw):
                if partial:
                    raw = {**self._lte_raws.get(mac, {}), **raw}
                if is_lte_device(raw):
                    self._lte_raws[mac] = raw
                    changed = True
        if not changed or not self._gateway_raw:
            return
        self._pushes += 1
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


def _poll_error(err: BaseException) -> BaseException:
    """Return what a failed request means for the coordinator."""
    if isinstance(err, NetworkAuthError):
        return ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth")
    if isinstance(err, NetworkError):
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="update_failed",
            translation_placeholders={"error": str(err)},
        )
    return err


def _mac(raw: dict[str, Any]) -> str:
    return str(raw.get("mac", "")).lower()


def _renamed(previous: NetworkState, state: NetworkState) -> bool:
    return any(
        (before := previous.uplinks.get(key)) is not None and before.name != uplink.name
        for key, uplink in state.uplinks.items()
    )
