"""Gateway state from the websocket, checked by polling."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
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
from .const import (
    AUTH_FAILURES_FOR_REAUTH,
    CONF_AUTO_OFF_MINUTES,
    CONF_DRILL,
    CONF_ENFORCED_AT,
    CONF_ENFORCED_WAN_TYPE,
    CONF_LTE_LIMIT_PERCENT,
    CONF_SPEEDTEST_UPLINK,
    DEFAULT_AUTO_OFF_MINUTES,
    DEFAULT_LTE_LIMIT_PERCENT,
    DOMAIN,
    EVENT_SPEEDTEST,
    FAILOVER_TIMEOUT,
    FALLBACK_WAN_TYPE,
    ISSUE_ADMIN,
    ISSUE_LTE_LIMIT,
    ISSUE_UNREACHABLE,
    RECONNECT_MAX,
    RECONNECT_MIN,
    RESULT_TIMEOUT,
    UNREACHABLE_POLLS,
    UPDATE_INTERVAL,
    WAN_TYPE_DISABLED,
)
from .model import (
    EVENT_BRIEF_FAILOVER,
    EVENT_DRILL_COMPLETED,
    SWITCH_TOLERANCE,
    Drill,
    NetworkState,
    Speedtest,
    Uplink,
    UplinkEvent,
    brief_failover_candidate,
    detect_active_events,
    detect_address_events,
    detect_online_events,
    drill_progress,
    essentials,
    lte_usage,
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
        # The running or last backup drill, as the entry stored it
        self.drill = Drill.from_dict(entry.data.get(CONF_DRILL))
        # The uplink the last speed test ran over, by run time, as the entry stored it
        self._speedtest_uplink = _speedtest_uplink_from(entry.data.get(CONF_SPEEDTEST_UPLINK))
        # The context of the event fired for the newest result, by run time; the
        # speed test sensors write that result in it, so the logbook names the
        # test as the cause of their state change
        self.speedtest_context: tuple[datetime, Context] | None = None
        # The timer that switches the preferred uplink on again by itself
        self._unsub_auto_off: CALLBACK_TYPE | None = None
        # The running speed test over the backup uplink
        self.backup_speedtest: asyncio.Task[None] | None = None
        # Consecutive failed polls, for the repair issue "console unreachable"
        self._failed_polls = 0
        # Consecutive polls with a rejected login, before the password is asked for
        self._auth_failures = 0
        # LTE devices whose data limit is turned off (repair issue per device)
        self._lte_limit_issues: set[str] = set()

    async def _async_update_data(self) -> NetworkState:
        try:
            state = await self._async_fetch()
        except UpdateFailed:
            self._failed_polls += 1
            if self._failed_polls == UNREACHABLE_POLLS:
                self._create_issue(
                    ISSUE_UNREACHABLE,
                    {
                        "minutes": str(
                            int(UNREACHABLE_POLLS * UPDATE_INTERVAL.total_seconds() // 60)
                        )
                    },
                )
            raise
        if self._failed_polls:
            self._failed_polls = 0
            self._delete_issue(ISSUE_UNREACHABLE)
        self._auth_failures = 0
        self._check_lte_limits(state)
        return state

    async def _async_fetch(self) -> NetworkState:
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
                raise self._poll_error(result)
        devices: list[dict[str, Any]] = results[0]
        configs: list[dict[str, Any]] = results[1]
        clients = results[2]
        if isinstance(clients, NetworkAuthError):
            raise self._poll_error(clients)
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
        async with self._admin_write("console_write_failed") as client:
            uplink = self.data.uplinks.get(key)
            if uplink is None or uplink.config_id is None:
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_wan_config")
            kept = await client.set_wan_type(uplink.config_id, wan_type)
        if kept != wan_type:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="console_write_failed",
                translation_placeholders={"error": f"console kept {kept or 'nothing'}"},
            )
        # Not async_request_refresh: that one is debounced for 10 s, and the
        # switch must show the new state right after the write
        await self.async_refresh()

    async def async_start_speedtest(self) -> None:
        """Start a speed test on the gateway with the admin credentials.

        It runs over the uplink that carries the traffic; the result arrives
        with the gateway's runtime object. Refused while one is running.
        """
        async with self._admin_write("speedtest_not_started") as client:
            self._check_no_speedtest_running()
            await client.start_speedtest(self.mac)

    # The switch "Enforce backup uplink": the preferred uplink switched off in
    # the console. The type to restore and the start live in the entry's data,
    # so a restart neither forgets what to restore nor when.

    @property
    def primary_uplink(self) -> Uplink | None:
        """Return the uplink the gateway prefers; None while it is not reported."""
        data = self.data
        return data.uplinks.get(data.primary) if data.primary else None

    @property
    def backup_enforced(self) -> bool | None:
        """Return whether the preferred uplink is switched off; None if its type is unknown."""
        primary = self.primary_uplink
        if primary is None or primary.wan_type is None:
            return None
        return primary.wan_type == WAN_TYPE_DISABLED

    @property
    def auto_off(self) -> timedelta:
        """Return after how long the preferred uplink is switched on again by itself."""
        return timedelta(
            minutes=self.config_entry.options.get(CONF_AUTO_OFF_MINUTES, DEFAULT_AUTO_OFF_MINUTES)
        )

    @property
    def restore_type(self) -> str:
        """Return the WAN type to restore."""
        return str(self.config_entry.data.get(CONF_ENFORCED_WAN_TYPE) or FALLBACK_WAN_TYPE)

    @property
    def enforced_at(self) -> datetime | None:
        """Return when the enforcing started; None while it is not enforced."""
        return dt_util.parse_datetime(str(self.config_entry.data.get(CONF_ENFORCED_AT) or ""))

    async def async_enforce_backup(self) -> None:
        """Switch the preferred uplink off, unless that would be unwise; the drill begins."""
        primary = self._check_enforceable()
        previous = primary.wan_type if primary.wan_type != WAN_TYPE_DISABLED else None
        self._remember_enforced(previous or self.restore_type, dt_util.utcnow())
        await self.async_set_wan_type(primary.key, WAN_TYPE_DISABLED)
        # The drill protocol: the failover and the return are timed from here
        self.start_drill()
        self.arm_auto_off(self.auto_off)

    async def async_release_backup(self) -> None:
        """Switch the preferred uplink on again."""
        primary = self.primary_uplink
        if primary is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_wan_config")
        # The return is timed from the switch-back, so note it before the write
        self.end_drill()
        try:
            await self.async_set_wan_type(primary.key, self.restore_type)
        except HomeAssistantError:
            # The timer stays armed: it tries again later
            self.reopen_drill()
            raise
        self.cancel_auto_off()
        self._remember_enforced(None, None)
        self.async_update_listeners()

    def _check_enforceable(self) -> Uplink:
        """Return the preferred uplink if switching it off is wise now; raise otherwise."""
        data = self.data
        primary = self.primary_uplink
        if primary is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_wan_config")
        if data.backup_active:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="backup_already_active"
            )
        limit = self.config_entry.options.get(CONF_LTE_LIMIT_PERCENT, DEFAULT_LTE_LIMIT_PERCENT)
        for lte in (data.lte or {}).values():
            if lte.usage_percent is not None and lte.usage_percent >= limit:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="lte_data_plan_used",
                    translation_placeholders={
                        "percent": f"{lte.usage_percent:.0f}",
                        "limit": str(limit),
                    },
                )
        return primary

    def _check_no_speedtest_running(self) -> None:
        if self.data.speedtest is not None and self.data.speedtest.running:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="speedtest_running"
            )

    @callback
    def _remember_enforced(self, wan_type: str | None, started: datetime | None) -> None:
        """Keep the WAN type to restore and the start in the entry, across restarts."""
        data = {
            k: v
            for k, v in self.config_entry.data.items()
            if k not in (CONF_ENFORCED_WAN_TYPE, CONF_ENFORCED_AT)
        }
        if wan_type is not None and started is not None:
            data[CONF_ENFORCED_WAN_TYPE] = wan_type
            data[CONF_ENFORCED_AT] = started.isoformat()
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)

    @callback
    def arm_auto_off(self, delay: timedelta) -> None:
        """Switch the preferred uplink on again after ``delay``."""
        self.cancel_auto_off()
        self._unsub_auto_off = async_call_later(self.hass, delay, self._async_auto_off)

    @callback
    def arm_auto_off_after_restart(self) -> None:
        """Arm the automatic switch-off again after a restart, or catch up on it."""
        if self.backup_enforced and (started := self.enforced_at) is not None:
            remaining = started + self.auto_off - dt_util.utcnow()
            self.arm_auto_off(max(remaining, timedelta(0)))

    @callback
    def cancel_auto_off(self) -> None:
        """Stop the timer; the console keeps its state."""
        if self._unsub_auto_off is not None:
            self._unsub_auto_off()
            self._unsub_auto_off = None

    async def _async_auto_off(self, _now: datetime) -> None:
        """Switch back after the configured time."""
        self._unsub_auto_off = None
        if not self.backup_enforced:
            return
        try:
            await self.async_release_backup()
        except HomeAssistantError as err:
            _LOGGER.warning("Could not switch the preferred uplink on again: %s", err)
            self.arm_auto_off(timedelta(minutes=1))

    # The button "Run speed test over backup uplink": switch the preferred
    # uplink off, wait for the failover, run the test, wait for the result,
    # switch the uplink on again, whatever happened in between.

    async def async_start_backup_speedtest(self) -> None:
        """Start the measurement of the backup uplink in the background, unless unwise."""
        if self.admin_client is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_admin")
        if self.backup_speedtest is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="backup_speedtest_running"
            )
        self._check_no_speedtest_running()
        self._check_enforceable()
        self.backup_speedtest = self.config_entry.async_create_background_task(
            self.hass, self._async_backup_speedtest(), "network_plus backup speed test"
        )
        self.async_update_listeners()

    async def _async_backup_speedtest(self) -> None:
        try:
            await self.async_enforce_backup()
            try:
                if not await self._async_wait(lambda s: s.backup_active is True, FAILOVER_TIMEOUT):
                    raise HomeAssistantError("the gateway did not switch to the backup in time")
                before = self.data.speedtest.last_run if self.data.speedtest else None
                await self.async_start_speedtest()
                if not await self._async_wait(
                    lambda s: (
                        s.speedtest is not None
                        and not s.speedtest.running
                        and s.speedtest.last_run != before
                    ),
                    RESULT_TIMEOUT,
                ):
                    raise HomeAssistantError("the gateway reported no result in time")
            finally:
                await self.async_release_backup()
        except HomeAssistantError as err:
            _LOGGER.warning("Speed test over the backup uplink: %s", err)
        finally:
            self.backup_speedtest = None
            self.async_update_listeners()

    async def _async_wait(self, predicate: Callable[[NetworkState], bool], seconds: float) -> bool:
        """Return whether ``predicate`` holds for a state within ``seconds``."""
        if predicate(self.data):
            return True
        done = self.hass.loop.create_future()

        @callback
        def _check() -> None:
            if not done.done() and predicate(self.data):
                done.set_result(True)

        unsub = self.async_add_listener(_check)
        try:
            async with asyncio.timeout(seconds):
                await done
        except TimeoutError:
            return False
        finally:
            unsub()
        return True

    @asynccontextmanager
    async def _admin_write(self, failed_key: str) -> AsyncIterator[NetworkClient]:
        """Give the admin client to a write and turn its errors into readable ones.

        ``failed_key`` names the message for an error of the console that is
        neither a rejected login nor missing rights.
        """
        if self.admin_client is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_admin")
        try:
            yield self.admin_client
        except NetworkAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="admin_login_rejected"
            ) from err
        except NetworkPermissionError as err:
            self.note_admin_permission(False, "the console refused the write")
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="admin_not_allowed"
            ) from err
        except NetworkError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=failed_key,
                translation_placeholders={"error": str(err)},
            ) from err

    def _build(self) -> NetworkState:
        state = parse_state(
            self._gateway_raw, self._configs, list(self._lte_raws.values()), self._clients
        )
        return self._place_speedtest(state)

    @callback
    def _place_speedtest(self, state: NetworkState) -> NetworkState:
        """Fill in the uplink the last speed test ran over, and remember it.

        The gateway names the test's interface only for a while after the
        test (seen live: right after it finished, empty a minute later). So
        the uplink is remembered when a run time is first seen: from the
        interface if the gateway names it, else the uplink that carries the
        traffic at that moment. Kept in the entry, so a restart does not
        reassign a result to whatever uplink is active then.
        """
        speedtest = state.speedtest
        if speedtest is None or (last_run := speedtest.last_run) is None:
            return state
        uplink = speedtest.uplink
        remembered = self._speedtest_uplink
        if uplink is None:
            if remembered is not None and remembered[0] == last_run:
                uplink = remembered[1]
            else:
                uplink = state.active
        if uplink is None:
            return state
        speedtest = replace(speedtest, uplink=uplink)
        if (last_run, uplink) != remembered:
            if remembered is not None and remembered[0] != last_run:
                # A result that arrived while the integration watched, not the
                # one found at the first setup: tell the logbook
                self._announce_speedtest(speedtest, last_run, uplink, state)
            self._speedtest_uplink = (last_run, uplink)
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_SPEEDTEST_UPLINK: {"last_run": last_run.isoformat(), "uplink": uplink},
                },
            )
        return replace(state, speedtest=speedtest)

    @callback
    def _announce_speedtest(
        self, speedtest: Speedtest, last_run: datetime, uplink: str, state: NetworkState
    ) -> None:
        """Fire the event for a finished speed test and keep its context for the sensors."""
        named = state.uplinks.get(uplink)
        context = Context()
        self.hass.bus.async_fire(
            EVENT_SPEEDTEST,
            {
                "entry_id": self.config_entry.entry_id,
                "gateway": self.config_entry.title,
                "download": speedtest.download,
                "upload": speedtest.upload,
                "ping": speedtest.ping,
                "last_run": last_run.isoformat(),
                "uplink": uplink.lower(),
                "uplink_name": named.name if named else uplink,
            },
            context=context,
        )
        self.speedtest_context = (last_run, context)

    @callback
    def note_admin_permission(self, allowed: bool, reason: str = "") -> None:
        """Keep or drop the repair issue that says the admin user cannot write."""
        if allowed:
            self._delete_issue(ISSUE_ADMIN)
        else:
            self._create_issue(ISSUE_ADMIN, {"reason": reason})

    @callback
    def start_drill(self) -> None:
        """Note that the switch turned the preferred uplink off; the drill begins now."""
        self._save_drill(Drill(started_at=dt_util.utcnow(), usage_start=lte_usage(self.data)))

    @callback
    def end_drill(self) -> None:
        """Note that the switch turns the preferred uplink on again."""
        if self.drill is not None and not self.drill.completed and self.drill.ended_at is None:
            self._save_drill(replace(self.drill, ended_at=dt_util.utcnow()))

    @callback
    def reopen_drill(self) -> None:
        """Forget the end of the drill: the console did not take the switch-back."""
        if self.drill is not None and not self.drill.completed and self.drill.ended_at is not None:
            self._save_drill(replace(self.drill, ended_at=None))

    @callback
    def _save_drill(self, drill: Drill) -> None:
        """Take ``drill`` as the current one and keep it in the entry, across restarts."""
        self.drill = drill
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_DRILL: drill.as_dict()}
        )
        self.async_update_listeners()

    @callback
    def _check_lte_limits(self, state: NetworkState) -> None:
        """Raise a repair issue for every LTE device whose data limit is turned off."""
        for mac, lte in (state.lte or {}).items():
            if lte.limit_enabled:
                if mac in self._lte_limit_issues:
                    self._lte_limit_issues.discard(mac)
                    self._delete_issue(f"{ISSUE_LTE_LIMIT}_{mac}")
            elif mac not in self._lte_limit_issues:
                self._lte_limit_issues.add(mac)
                self._create_issue(f"{ISSUE_LTE_LIMIT}_{mac}", {"name": lte.name}, ISSUE_LTE_LIMIT)

    @callback
    def _create_issue(
        self, issue_id: str, placeholders: dict[str, str], translation_key: str | None = None
    ) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{issue_id}_{self.config_entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key or issue_id,
            translation_placeholders={"title": self.config_entry.title, **placeholders},
        )

    @callback
    def _delete_issue(self, issue_id: str) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, f"{issue_id}_{self.config_entry.entry_id}")

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
        if switches and self.drill is not None and not self.drill.completed:
            drill = drill_progress(self.drill, switches, now, lte_usage(state))
            if drill != self.drill:
                self._save_drill(drill)
                if drill.completed:
                    events.append(UplinkEvent(EVENT_DRILL_COMPLETED, drill.event_attributes()))
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

    def _poll_error(self, err: BaseException) -> BaseException:
        """Return what a failed request means for the coordinator.

        A rejected login asks for the password only when it repeats: the
        console rejects a login once in a while (maintenance, a session
        edge case), and the reauthentication flow needs a person.
        """
        if isinstance(err, NetworkAuthError):
            self._auth_failures += 1
            if self._auth_failures >= AUTH_FAILURES_FOR_REAUTH:
                return ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="invalid_auth",
                    translation_placeholders={"error": str(err)},
                )
            _LOGGER.warning(
                "The console rejected the login (%s); the password is asked for after %d"
                " rejections in a row",
                err,
                AUTH_FAILURES_FOR_REAUTH,
            )
            return UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="login_rejected",
                translation_placeholders={"error": str(err)},
            )
        if isinstance(err, NetworkError):
            return UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            )
        return err


def _speedtest_uplink_from(data: Any) -> tuple[datetime, str] | None:
    """Return the stored (run time, uplink key) of the last speed test; None if unreadable."""
    if not isinstance(data, dict) or not isinstance(data.get("uplink"), str):
        return None
    try:
        last_run = datetime.fromisoformat(str(data.get("last_run")))
    except ValueError:
        return None
    return (last_run, data["uplink"])


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
