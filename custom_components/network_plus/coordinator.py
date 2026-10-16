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
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    NetworkAuthError,
    NetworkClient,
    NetworkError,
    NetworkPermissionError,
    find_gateway,
    find_lte_devices,
    is_lte_device,
)
from .const import DOMAIN, RECONNECT_MAX, RECONNECT_MIN, UPDATE_INTERVAL
from .model import (
    EVENT_BRIEF_FAILOVER,
    SWITCH_TOLERANCE,
    NetworkState,
    UplinkEvent,
    brief_failover_candidate,
    detect_active_events,
    detect_address_events,
    detect_online_events,
    essentials,
    normalize_mac,
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
    admin_client: NetworkClient | None = None  # only with the admin opt-in


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
        admin_client: NetworkClient | None = None,
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
        self.admin_client = admin_client
        self.mac = mac
        self.websocket_connected = False
        self._configs: list[dict[str, Any]] = []
        self._gateway_raw: dict[str, Any] = {}
        self._lte_raws: dict[str, dict[str, Any]] = {}
        self._clients: list[dict[str, Any]] | None = None
        # Devices pushed since the poll started; the poll keeps their objects,
        # which are newer than its own snapshot
        self._pushed_macs: set[str] = set()
        self._event_listeners: list[Callable[[UplinkEvent], None]] = []
        # Last known status and address per uplink and the last known active
        # uplink, for changes across a gap or an unknown state in between
        self._known_online: dict[str, bool] = {}
        self._known_addresses: dict[tuple[str, str], str] = {}
        self._known_active: dict[str, str] = {}
        # When the active uplink was last seen changing, and a transition
        # timestamp that may turn out to be a brief failover
        self._switch_seen_at: datetime | None = None
        self._pending_brief: datetime | None = None
        # Whether the current websocket connection delivered a message
        self._received = False

    async def _async_update_data(self) -> NetworkState:
        self._pushed_macs.clear()
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
        # A device pushed while the poll ran keeps its pushed object: it is
        # newer than the poll's snapshot, which must not turn a seen failover
        # back. The poll decides which LTE devices exist.
        pushed = self._pushed_macs
        if self.mac not in pushed:
            self._gateway_raw = gateway
        self._lte_raws = {
            mac: self._lte_raws[mac] if mac in pushed and mac in self._lte_raws else raw
            for raw in find_lte_devices(devices)
            if (mac := normalize_mac(raw.get("mac")))
        }
        state = self._build()
        previous: NetworkState | None = self.data
        if previous is not None and _renamed(previous, state, self._configs):
            # Entity names carry the uplink names; a reload renews them
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        self._notify(state)
        return state

    async def async_set_wan_type(self, key: str, wan_type: str) -> None:
        """Set the WAN type of the uplink ``key`` with the admin credentials, then refresh."""
        if self.admin_client is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_admin")
        uplink = self.data.uplinks.get(key)
        if uplink is None or uplink.config_id is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_wan_config")
        try:
            kept = await self.admin_client.set_wan_type(uplink.config_id, wan_type)
        except NetworkAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="admin_login_rejected"
            ) from err
        except NetworkPermissionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="admin_not_allowed"
            ) from err
        except NetworkError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="console_write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if kept != wan_type:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="console_write_failed",
                translation_placeholders={"error": f"console kept {kept or 'nothing'}"},
            )
        # Not async_request_refresh: that one is debounced for 10 s, and the
        # switch must show the new state right after the write
        await self.async_refresh()

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
        """Pass the events between the last known state and ``state`` on.

        The first state only seeds what is known; it produces no events.
        """
        now = dt_util.utcnow()
        events = detect_online_events(self._known_online, state)
        switches = detect_active_events(self._known_active, state)
        if switches:
            self._switch_seen_at = now
            # A timestamp that preceded this switch was its herald, no brief failover
            self._pending_brief = None
        events += switches
        # None before the first refresh, although the type says otherwise
        previous: NetworkState | None = self.data
        if previous is not None:
            candidate = brief_failover_candidate(previous, state, self._switch_seen_at)
            if candidate is not None:
                self._pending_brief = candidate
            if (
                self._pending_brief is not None
                and not switches
                and state.active == state.primary
                and now - self._pending_brief >= SWITCH_TOLERANCE
            ):
                events.append(
                    UplinkEvent(EVENT_BRIEF_FAILOVER, {"at": self._pending_brief.isoformat()})
                )
                self._pending_brief = None
        events += detect_address_events(self._known_addresses, state)
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
            if not isinstance(raw, dict) or not (mac := normalize_mac(raw.get("mac"))):
                continue
            if mac == self.mac:
                if partial:
                    raw = {**self._gateway_raw, **raw}
                if "last_wan_status" in raw:
                    self._gateway_raw = raw
                    self._pushed_macs.add(mac)
                    changed = True
            elif mac in self._lte_raws or is_lte_device(raw):
                if partial:
                    raw = {**self._lte_raws.get(mac, {}), **raw}
                if is_lte_device(raw):
                    self._lte_raws[mac] = raw
                    self._pushed_macs.add(mac)
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
            # Not async_set_updated_data: that would reschedule the poll, and a
            # flapping uplink could postpone it for good
            self.data = state
            self.async_update_listeners()

    async def async_listen(self) -> None:
        """Keep the websocket connected for as long as the entry is loaded."""
        delay = RECONNECT_MIN
        while True:
            self._received = False
            try:
                await self.client.listen(self._on_message, self._on_connect)
            except NetworkAuthError as err:
                # Wrong credentials: the poll starts the reauth flow and the
                # entry is reloaded after it. A console that is starting up
                # answers the same way, so try again, but rarely.
                _LOGGER.debug("Websocket login rejected: %s", err)
                self.websocket_connected = False
                await asyncio.sleep(RECONNECT_MAX)
                continue
            except NetworkError as err:
                _LOGGER.debug("Websocket ended: %s", err)
            except Exception:
                # A push the integration cannot handle must not end the listener
                _LOGGER.exception("Unexpected error while handling a websocket message")
            else:
                _LOGGER.debug("Websocket closed")
            # A connection that delivered messages is retried soon, however it
            # ended; attempts that failed or were closed at once wait longer
            # each time
            worked = self._received
            self.websocket_connected = False
            if worked:
                delay = RECONNECT_MIN
            await asyncio.sleep(delay)
            if not worked:
                delay = min(delay * 2, RECONNECT_MAX)

    @callback
    def _on_connect(self) -> None:
        self.websocket_connected = True

    @callback
    def _on_message(self, message: dict[str, Any]) -> None:
        self._received = True
        self.handle_message(message)


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


def _renamed(previous: NetworkState, state: NetworkState, configs: list[dict[str, Any]]) -> bool:
    """Return whether an uplink carries another name than before.

    Only names from the uplink configuration count: an uplink missing from an
    incomplete answer falls back to its key, and that is no rename.
    """
    configured = {c.get("wan_networkgroup") for c in configs}
    return any(
        key in configured
        and (before := previous.uplinks.get(key)) is not None
        and before.name != before.key
        and before.name != uplink.name
        for key, uplink in state.uplinks.items()
    )
