"""The history the page shows: switches, speed tests, drills and remote sessions.

Kept in a store of its own per console instead of the recorder: the page
needs a handful of entries a day, the recorder would need sensors for them.
Entries older than the option "Keep history" are dropped; drills are few
and stay.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import EVENT_DRILL, EVENT_SPEEDTEST, HISTORY_SAVE_DELAY, HISTORY_STORAGE_KEY
from .coordinator import NetworkCoordinator
from .model import (
    EVENT_BRIEF_FAILOVER,
    EVENT_FAILOVER,
    EVENT_PUBLIC_IP_CHANGED,
    EVENT_RESTORED,
    EVENT_UPLINK_DOWN,
    EVENT_UPLINK_UP,
    UplinkEvent,
)

STORAGE_VERSION = 1

# What an event of the "Internet" entity keeps in the history; the addresses
# of a changed public IP stay out, the page names only the uplink and the family
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    EVENT_FAILOVER: ("from_uplink", "from_uplink_name", "to_uplink", "to_uplink_name"),
    EVENT_RESTORED: ("from_uplink", "from_uplink_name", "to_uplink", "to_uplink_name"),
    EVENT_BRIEF_FAILOVER: (),
    EVENT_UPLINK_DOWN: ("uplink", "uplink_name"),
    EVENT_UPLINK_UP: ("uplink", "uplink_name"),
    EVENT_PUBLIC_IP_CHANGED: ("uplink", "uplink_name", "family"),
}
SPEEDTEST_FIELDS = ("uplink", "uplink_name", "download", "upload", "ping")
DRILL_FIELDS = ("failover_seconds", "restore_seconds", "data_used_bytes")


def storage_key(entry_id: str) -> str:
    """Return the key of the store of the console with ``entry_id``."""
    return HISTORY_STORAGE_KEY.format(entry_id=entry_id)


class History:
    """Switches, speed tests, drills and remote sessions of one console."""

    def __init__(self, hass: HomeAssistant, entry_id: str, days: int) -> None:
        """Initialize the history; ``days`` is how long entries are kept."""
        self.hass = hass
        self.entry_id = entry_id
        self.days = days
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry_id))
        self.events: list[dict[str, Any]] = []
        self.speedtests: list[dict[str, Any]] = []
        self.drills: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []
        self._dirty = False

    async def async_load(self) -> None:
        """Read the stored history; an unreadable part starts empty."""
        data = await self._store.async_load() or {}
        for name in ("events", "speedtests", "drills", "sessions"):
            value = data.get(name)
            items = [item for item in value if _valid(item)] if isinstance(value, list) else []
            setattr(self, name, items)
        self.sessions = [s for s in self.sessions if {"until", "type", "name"} <= s.keys()]
        self._prune()

    @callback
    def attach(self, coordinator: NetworkCoordinator) -> Callable[[], None]:
        """Follow the console's events, speed tests, drills and remote clients.

        Returns the function that stops it.
        """
        removers = [
            coordinator.async_add_event_listener(self.add_event),
            coordinator.async_add_listener(lambda: self.note_remote(coordinator)),
            self.hass.bus.async_listen(EVENT_SPEEDTEST, self._on_speedtest),
            self.hass.bus.async_listen(EVENT_DRILL, self._on_drill),
        ]
        self.note_remote(coordinator)

        @callback
        def _detach() -> None:
            for remove in removers:
                remove()

        return _detach

    @callback
    def add_event(self, event: UplinkEvent) -> None:
        """Keep a switch, an uplink going down or up, or a changed public IP."""
        fields = EVENT_FIELDS.get(event.type)
        if fields is None:
            return
        at = event.attributes.get("at") if event.type == EVENT_BRIEF_FAILOVER else None
        item = {"at": at or _now(), "type": event.type}
        item.update({key: event.attributes.get(key) for key in fields})
        self.events.append(item)
        self._changed()

    @callback
    def _on_speedtest(self, event: Event[dict[str, Any]]) -> None:
        data = event.data
        at = data.get("last_run")
        if data.get("entry_id") != self.entry_id or not isinstance(at, str):
            return
        if any(item["at"] == at for item in self.speedtests):
            return
        self.speedtests.append({"at": at, **{key: data.get(key) for key in SPEEDTEST_FIELDS}})
        self._changed()

    @callback
    def _on_drill(self, event: Event[dict[str, Any]]) -> None:
        data = event.data
        at = data.get("started_at")
        if data.get("entry_id") != self.entry_id or not isinstance(at, str):
            return
        if any(item["at"] == at for item in self.drills):
            return
        self.drills.append({"at": at, **{key: data.get(key) for key in DRILL_FIELDS}})
        self._changed()

    @callback
    def note_remote(self, coordinator: NetworkCoordinator) -> None:
        """Open a session for a remote client that appeared, close those that left.

        Nothing changes while the console does not tell its clients.
        """
        state = coordinator.data
        clients = state.vpn_clients if state is not None else None
        if clients is None:
            return
        now = _now()
        current = {(client.type, client.name): client for client in clients}
        changed = False
        for session in self.sessions:
            if session["until"] is None and (session["type"], session["name"]) not in current:
                session["until"] = now
                changed = True
        open_keys = {(s["type"], s["name"]) for s in self.sessions if s["until"] is None}
        for key, client in current.items():
            if key not in open_keys:
                since = client.since.isoformat() if client.since else now
                self.sessions.append({"at": since, "until": None, "type": key[0], "name": key[1]})
                changed = True
        if changed:
            self._changed()

    def as_dict(self) -> dict[str, Any]:
        """Return the history for the page, oldest first."""
        return {
            "days": self.days,
            "events": sorted(self.events, key=_at),
            "speedtests": sorted(self.speedtests, key=_at),
            "drills": sorted(self.drills, key=_at),
            "sessions": sorted(self.sessions, key=_at),
        }

    async def async_flush(self) -> None:
        """Write what is not written yet; on unload, so a reload does not lose it."""
        if self._dirty:
            await self._store.async_save(self._data())
            self._dirty = False

    async def async_remove(self) -> None:
        """Delete the stored history, with the console's entry."""
        await self._store.async_remove()

    @callback
    def _changed(self) -> None:
        self._prune()
        self._dirty = True
        self._store.async_delay_save(self._saved, HISTORY_SAVE_DELAY)

    @callback
    def _saved(self) -> dict[str, Any]:
        self._dirty = False
        return self._data()

    def _data(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "speedtests": self.speedtests,
            "drills": self.drills,
            "sessions": self.sessions,
        }

    def _prune(self) -> None:
        """Drop entries older than the kept days; open sessions and drills stay."""
        limit = dt_util.utcnow() - timedelta(days=self.days)

        def keep(item: dict[str, Any], key: str = "at") -> bool:
            moment = _parse(item.get(key))
            return moment is None or moment >= limit

        self.events = [item for item in self.events if keep(item)]
        self.speedtests = [item for item in self.speedtests if keep(item)]
        self.sessions = [
            item for item in self.sessions if item.get("until") is None or keep(item, "until")
        ]


def _valid(item: Any) -> bool:
    return isinstance(item, dict) and _parse(item.get("at")) is not None


def _parse(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def _at(item: dict[str, Any]) -> datetime:
    return _parse(item["at"]) or dt_util.utc_from_timestamp(0)


def _now() -> str:
    return dt_util.utcnow().isoformat()
