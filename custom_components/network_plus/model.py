"""What the integration knows about the gateway and its uplinks.

Read from the gateway's runtime object (``stat/device`` or the websocket's
``device:sync``, both carry the same fields) and the uplink configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Values of ``last_wan_status``. Only "online" was seen on a live console; an
# unknown value means "unknown", never "offline" (an uplink is reported as down
# only when the gateway says so).
ONLINE = frozenset({"online"})
OFFLINE = frozenset({"offline", "down", "disconnected", "failed"})

# Event types of the "Internet" event entity
EVENT_FAILOVER = "failover"
EVENT_RESTORED = "restored"
EVENT_BRIEF_FAILOVER = "brief_failover"
EVENT_UPLINK_DOWN = "uplink_down"
EVENT_UPLINK_UP = "uplink_up"
EVENT_TYPES = [
    EVENT_FAILOVER,
    EVENT_RESTORED,
    EVENT_BRIEF_FAILOVER,
    EVENT_UPLINK_DOWN,
    EVENT_UPLINK_UP,
]


@dataclass(frozen=True, slots=True)
class Gateway:
    """The gateway as a device."""

    mac: str
    name: str
    model: str
    version: str | None


@dataclass(frozen=True, slots=True)
class Uplink:
    """One internet uplink the gateway monitors."""

    key: str  # network group, for example WAN, WAN2, WAN_LTE_FAILOVER
    name: str  # name set in UniFi Network
    priority: int  # failover priority; 1 first, 0 or less (LTE backup) last
    online: bool | None
    latency: int | None = None  # ms, average of the gateway's monitors
    availability: float | None = None  # %, over the gateway's monitoring period


@dataclass(frozen=True, slots=True)
class NetworkState:
    """The gateway and its uplinks at one moment."""

    gateway: Gateway
    uplinks: dict[str, Uplink]  # by key, in failover order
    active: str | None  # key of the uplink that carries the traffic
    last_transition: datetime | None  # last failover to or from the LTE backup

    @property
    def primary(self) -> str | None:
        """Return the key of the uplink the gateway prefers."""
        return next(iter(self.uplinks), None)

    @property
    def backup_active(self) -> bool | None:
        """Return whether the traffic runs over another uplink than the preferred one."""
        if self.active is None or self.primary is None:
            return None
        return self.active != self.primary


@dataclass(frozen=True, slots=True)
class UplinkEvent:
    """Something that happened between two states."""

    type: str
    attributes: dict[str, Any]


def parse_state(raw: dict[str, Any], configs: list[dict[str, Any]]) -> NetworkState:
    """Build the state from the gateway's runtime object and the uplink configuration."""
    names: dict[str, str] = {}
    priorities: dict[str, int] = {}
    for config in configs:
        key = config.get("wan_networkgroup")
        if not isinstance(key, str):
            continue
        names[key] = str(config.get("name") or key)
        priorities[key] = _int(config.get("wan_failover_priority")) or 0

    status = raw.get("last_wan_status")
    status = status if isinstance(status, dict) else {}
    stats = raw.get("uptime_stats")
    stats = stats if isinstance(stats, dict) else {}

    uplinks = [
        Uplink(
            key=key,
            name=names.get(key, key),
            priority=priorities.get(key, 0),
            online=_online(key, value),
            latency=_int((stats.get(key) or {}).get("latency_average")),
            availability=_float((stats.get(key) or {}).get("availability")),
        )
        for key, value in status.items()
        if isinstance(key, str)
    ]
    uplinks.sort(key=_failover_order)
    by_key = {u.key: u for u in uplinks}

    return NetworkState(
        gateway=Gateway(
            mac=str(raw.get("mac", "")).lower(),
            name=str(raw.get("name") or raw.get("model") or "Gateway"),
            model=str(raw.get("model") or "") or "Gateway",
            version=str(raw["version"]) if raw.get("version") else None,
        ),
        uplinks=by_key,
        active=_active(raw, by_key),
        last_transition=_timestamp(raw.get("last_lte_failover_transition_timestamp")),
    )


def detect_events(old: NetworkState, new: NetworkState) -> list[UplinkEvent]:
    """Return what happened between two states, oldest first."""
    events: list[UplinkEvent] = []
    for key, uplink in new.uplinks.items():
        before = old.uplinks.get(key)
        if before is None or before.online is None or uplink.online is None:
            continue
        if before.online != uplink.online:
            events.append(
                UplinkEvent(
                    EVENT_UPLINK_UP if uplink.online else EVENT_UPLINK_DOWN,
                    {"uplink": key, "uplink_name": uplink.name},
                )
            )

    if old.active is not None and new.active is not None and old.active != new.active:
        events.append(
            UplinkEvent(
                EVENT_RESTORED if new.active == new.primary else EVENT_FAILOVER,
                {
                    "from_uplink": old.active,
                    "from_uplink_name": _name(new, old.active),
                    "to_uplink": new.active,
                    "to_uplink_name": _name(new, new.active),
                },
            )
        )
    elif (
        old.last_transition is not None
        and new.last_transition is not None
        and new.last_transition > old.last_transition
        and old.active == new.active == new.primary
    ):
        # The gateway switched to the backup and back between two states
        # this integration saw, for example while it was not connected.
        events.append(UplinkEvent(EVENT_BRIEF_FAILOVER, {"at": new.last_transition.isoformat()}))
    return events


def _active(raw: dict[str, Any], uplinks: dict[str, Uplink]) -> str | None:
    """Return the uplink that carries the traffic.

    The gateway names it in ``uplink.comment``. If that is missing or unknown,
    the traffic runs over the first uplink in failover order that is online.
    """
    uplink = raw.get("uplink")
    comment = uplink.get("comment") if isinstance(uplink, dict) else None
    if isinstance(comment, str) and comment in uplinks:
        return comment
    return next((u.key for u in uplinks.values() if u.online), None)


def _failover_order(uplink: Uplink) -> tuple[bool, int, str]:
    # Priorities 1, 2, … first; 0 and less (the LTE backup has -1) last
    return (uplink.priority <= 0, max(uplink.priority, 0), uplink.key)


def _online(key: str, value: Any) -> bool | None:
    text = str(value).lower()
    if text in ONLINE:
        return True
    if text in OFFLINE:
        return False
    _LOGGER.debug("Unknown status %r of uplink %s", value, key)
    return None


def _name(state: NetworkState, key: str) -> str:
    uplink = state.uplinks.get(key)
    return uplink.name if uplink else key


def _timestamp(value: Any) -> datetime | None:
    millis = _int(value)
    if not millis or millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, UTC)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None
