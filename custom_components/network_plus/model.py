"""What the integration knows about the gateway, its uplinks and the LTE backup.

Read from the devices' runtime objects (``stat/device`` or the websocket's
``device:sync``, both carry the same fields), the uplink configuration and
the console's list of active clients. Only named fields are taken over: the
raw objects hold secrets such as the SIM PIN and the APN credentials of an
LTE device, and they never leave this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import ipaddress
import logging
import math
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Values of ``last_wan_status``. Only "online" was seen on a live console; an
# unknown value means "unknown", never "offline" (an uplink is reported as down
# only when the gateway says so).
ONLINE = frozenset({"online"})
OFFLINE = frozenset({"offline", "down", "disconnected", "failed"})

# Client types of the console's active-client list that are on the LAN; every
# other type (TELEPORT, VPN) is a remote client.
LOCAL_CLIENT_TYPES = frozenset({"WIRED", "WIRELESS"})

# Network group of an uplink -> port object of the gateway (WAN -> wan1)
_WAN_GROUP = re.compile(r"^WAN(\d*)$")

# A transition timestamp this close to a seen switch of the active uplink
# belongs to that switch, and a fresh timestamp waits this long for a switch
# to follow before it counts as a brief failover: the console's clock and
# Home Assistant's may differ by a few seconds, and the timestamp may arrive
# a push before or after the switch itself.
SWITCH_TOLERANCE = timedelta(seconds=30)

# Event types of the "Internet" event entity
EVENT_FAILOVER = "failover"
EVENT_RESTORED = "restored"
EVENT_BRIEF_FAILOVER = "brief_failover"
EVENT_UPLINK_DOWN = "uplink_down"
EVENT_UPLINK_UP = "uplink_up"
EVENT_PUBLIC_IP_CHANGED = "public_ip_changed"
EVENT_DRILL_COMPLETED = "drill_completed"
EVENT_TYPES = [
    EVENT_FAILOVER,
    EVENT_RESTORED,
    EVENT_BRIEF_FAILOVER,
    EVENT_UPLINK_DOWN,
    EVENT_UPLINK_UP,
    EVENT_PUBLIC_IP_CHANGED,
    EVENT_DRILL_COMPLETED,
]


def normalize_mac(value: Any) -> str:
    """Return the MAC as the console sends it, lower case; "" if there is none."""
    return str(value or "").lower()


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
    ipv4: str | None = None  # public address of the uplink's port
    ipv6: str | None = None
    config_id: str | None = None  # the uplink network in the console's configuration
    wan_type: str | None = None  # dhcp, static, pppoe or disabled
    port: str | None = None  # the gateway's port object (wan1, wan2); the LTE backup has none
    link_speed: int | None = None  # Mbit/s the port negotiated; None while the link is down
    full_duplex: bool | None = None


@dataclass(frozen=True, slots=True)
class Speedtest:
    """The gateway's last speed test."""

    download: float | None  # Mbit/s
    upload: float | None  # Mbit/s
    ping: int | None  # ms
    last_run: datetime | None
    status: str | None


@dataclass(frozen=True, slots=True)
class LteBackup:
    """The LTE backup device and its data plan."""

    mac: str
    name: str
    model: str
    version: str | None
    connected: bool | None  # registered in the mobile network
    in_use: bool | None  # the gateway currently routes over LTE
    usage_bytes: int | None  # this calendar month, as the console counts it
    soft_limit: int | None  # bytes; the console warns here
    hard_limit: int | None  # bytes; the data plan set in UniFi Network
    limit_enabled: bool
    rssi: int | None  # dBm
    rsrp: int | None  # dBm
    state: str | None = None  # lte_state as the console reports it, for example "ready"
    sim_pin_required: bool | None = None
    sim_pin_verified: bool | None = None
    sim_pin_tries_left: int | None = None

    @property
    def usage_percent(self) -> float | None:
        """Return how much of the data plan is used, in percent."""
        if self.usage_bytes is None or not self.hard_limit or not self.limit_enabled:
            return None
        return self.usage_bytes / self.hard_limit * 100

    @property
    def sim_problem(self) -> bool | None:
        """Return whether the SIM keeps the device from connecting.

        That is a PIN the device could not verify, or no PIN tries left (the
        SIM wants its PUK). None while the console does not report the flags.
        """
        if self.sim_pin_tries_left == 0:
            return True
        if self.sim_pin_required is None:
            return None
        if not self.sim_pin_required:
            return False
        if self.sim_pin_verified is None:
            return None
        return not self.sim_pin_verified


@dataclass(frozen=True, slots=True)
class Drill:
    """A backup drill: the preferred uplink switched off on purpose, and what followed.

    Times are Home Assistant's clock, so the durations compare across drills.
    """

    started_at: datetime  # the switch turned on
    usage_start: int | None = None  # LTE bytes used at the start
    failover_at: datetime | None = None  # the gateway routed over the backup
    ended_at: datetime | None = None  # the switch turned off
    restored_at: datetime | None = None  # the gateway routed over the preferred uplink again
    usage_end: int | None = None

    @property
    def completed(self) -> bool:
        """Return whether the gateway is back on the preferred uplink."""
        return self.restored_at is not None

    @property
    def failover_seconds(self) -> float | None:
        """Return how long the gateway took to switch to the backup."""
        if self.failover_at is None:
            return None
        return max((self.failover_at - self.started_at).total_seconds(), 0.0)

    @property
    def restore_seconds(self) -> float | None:
        """Return how long the gateway took to come back; None if it came back on its own."""
        if self.restored_at is None or self.ended_at is None or self.restored_at < self.ended_at:
            return None
        return (self.restored_at - self.ended_at).total_seconds()

    @property
    def data_used(self) -> int | None:
        """Return the LTE bytes the drill cost; None across a reset of the monthly counter."""
        if self.usage_start is None or self.usage_end is None or self.usage_end < self.usage_start:
            return None
        return self.usage_end - self.usage_start

    def as_dict(self) -> dict[str, Any]:
        """Return the drill as the entry stores it."""
        return {
            "started_at": self.started_at.isoformat(),
            "usage_start": self.usage_start,
            "failover_at": self.failover_at.isoformat() if self.failover_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "restored_at": self.restored_at.isoformat() if self.restored_at else None,
            "usage_end": self.usage_end,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Drill | None:
        """Return the drill the entry stored; None if there is none or it is unreadable."""
        if not isinstance(data, dict):
            return None
        started = _iso(data.get("started_at"))
        if started is None:
            return None
        return cls(
            started_at=started,
            usage_start=_int(data.get("usage_start")),
            failover_at=_iso(data.get("failover_at")),
            ended_at=_iso(data.get("ended_at")),
            restored_at=_iso(data.get("restored_at")),
            usage_end=_int(data.get("usage_end")),
        )

    def event_attributes(self) -> dict[str, Any]:
        """Return what the event "drill_completed" carries."""
        return {
            "started_at": self.started_at.isoformat(),
            "failover_seconds": self.failover_seconds,
            "restore_seconds": self.restore_seconds,
            "data_used_bytes": self.data_used,
        }


def drill_progress(
    drill: Drill, switches: list[UplinkEvent], now: datetime, usage: int | None
) -> Drill:
    """Return the drill advanced by the switches of the active uplink seen at ``now``.

    The first failover marks when the gateway switched to the backup; a
    return marks the end, with the LTE usage of that moment.
    """
    for event in switches:
        if event.type == EVENT_FAILOVER and drill.failover_at is None:
            drill = replace(drill, failover_at=now)
        elif event.type == EVENT_RESTORED and drill.restored_at is None:
            drill = replace(drill, restored_at=now, usage_end=usage)
    return drill


def lte_usage(state: NetworkState) -> int | None:
    """Return the LTE bytes used this month over all LTE devices; None if none reports."""
    values = [lte.usage_bytes for lte in (state.lte or {}).values() if lte.usage_bytes is not None]
    return sum(values) if values else None


@dataclass(frozen=True, slots=True)
class VpnClient:
    """A remote client: Teleport or VPN."""

    name: str
    type: str
    since: datetime | None


@dataclass(frozen=True, slots=True)
class NetworkState:
    """The gateway and its uplinks at one moment."""

    gateway: Gateway
    uplinks: dict[str, Uplink]  # by key, in failover order
    active: str | None  # key of the uplink that carries the traffic
    last_transition: datetime | None  # last failover to or from the LTE backup
    latency: int | None = None  # ms, on the active uplink
    speedtest: Speedtest | None = None
    lte: dict[str, LteBackup] | None = None  # by MAC; None: not looked for yet
    vpn_clients: tuple[VpnClient, ...] | None = None  # None: the console did not tell

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


def essentials(state: NetworkState) -> tuple[Any, ...]:
    """Return what a websocket push must change to be worth a state update.

    The websocket sends the gateway about once a second, with latency values
    that differ nearly every time. Those are sampled by polling; a push counts
    only when an uplink, the active uplink, an address or the LTE state changes.
    """
    return (
        tuple(
            (u.key, u.online, u.ipv4, u.ipv6, u.link_speed, u.full_duplex)
            for u in state.uplinks.values()
        ),
        state.active,
        state.last_transition,
        tuple(
            (mac, lte.connected, lte.in_use, lte.sim_problem)
            for mac, lte in (state.lte or {}).items()
        ),
    )


def parse_state(
    raw: dict[str, Any],
    configs: list[dict[str, Any]],
    lte_raws: list[dict[str, Any]] | None = None,
    clients: list[Any] | None = None,
) -> NetworkState:
    """Build the state from the runtime objects, the uplink configuration and the clients."""
    names: dict[str, str] = {}
    priorities: dict[str, int] = {}
    config_ids: dict[str, str] = {}
    wan_types: dict[str, str] = {}
    for config in configs:
        key = config.get("wan_networkgroup")
        if not isinstance(key, str):
            continue
        names[key] = str(config.get("name") or key)
        priorities[key] = _int(config.get("wan_failover_priority")) or 0
        if config.get("_id"):
            config_ids[key] = str(config["_id"])
        if config.get("wan_type"):
            wan_types[key] = str(config["wan_type"])

    status = _dict(raw.get("last_wan_status"))
    stats = _dict(raw.get("uptime_stats"))
    interfaces = _dict(raw.get("last_wan_interfaces"))

    uplinks = [
        Uplink(
            key=key,
            name=names.get(key, key),
            priority=priorities.get(key, 0),
            online=_online(key, value),
            latency=_int(_dict(stats.get(key)).get("latency_average")),
            availability=_availability(_dict(stats.get(key))),
            ipv4=_ipv4(_dict(interfaces.get(key)).get("ip")),
            ipv6=_public_ipv6(_dict(raw.get(_port_key(key))).get("ipv6")),
            config_id=config_ids.get(key),
            wan_type=wan_types.get(key),
            port=_port_key(key) or None,
            **_link(_dict(raw.get(_port_key(key)))),
        )
        for key, value in status.items()
        if isinstance(key, str)
    ]
    uplinks.sort(key=_failover_order)
    by_key = {u.key: u for u in uplinks}
    active = _active(raw, by_key)
    uplink = _dict(raw.get("uplink"))
    # The monitors' value of the active uplink; the gateway's own latency field
    # measures something else while the traffic runs over LTE
    latency = by_key[active].latency if active else None
    if latency is None:
        latency = _int(uplink.get("latency"))

    return NetworkState(
        gateway=Gateway(
            mac=normalize_mac(raw.get("mac")),
            name=str(raw.get("name") or raw.get("model") or "Gateway"),
            model=str(raw.get("model") or "") or "Gateway",
            version=str(raw["version"]) if raw.get("version") else None,
        ),
        uplinks=by_key,
        active=active,
        last_transition=_timestamp_ms(raw.get("last_lte_failover_transition_timestamp")),
        latency=latency,
        speedtest=_speedtest(uplink),
        lte={lte.mac: lte for lte in map(parse_lte, lte_raws)} if lte_raws is not None else None,
        vpn_clients=parse_clients(clients) if clients is not None else None,
    )


def parse_lte(raw: dict[str, Any]) -> LteBackup:
    """Build the LTE backup from its runtime object.

    ``rx_bytes`` and ``tx_bytes`` of an LTE device are its traffic in the
    current calendar month (checked against the console's monthly report).
    """
    rx, tx = _int(raw.get("rx_bytes")), _int(raw.get("tx_bytes"))
    in_use = raw.get("lte_failover")
    return LteBackup(
        mac=normalize_mac(raw.get("mac")),
        name=str(raw.get("name") or raw.get("model") or "LTE backup"),
        model=str(raw.get("model") or "") or "LTE backup",
        version=str(raw["version"]) if raw.get("version") else None,
        connected=_yes_no(raw.get("lte_connected")),
        in_use=in_use if isinstance(in_use, bool) else None,
        usage_bytes=rx + tx if rx is not None and tx is not None else None,
        soft_limit=_int(raw.get("lte_soft_limit")),
        hard_limit=_int(raw.get("lte_hard_limit")),
        limit_enabled=raw.get("lte_data_limit_enabled") is True,
        rssi=_int(raw.get("lte_rssi")),
        rsrp=_int(raw.get("lte_rsrp")),
        state=str(raw["lte_state"]) if raw.get("lte_state") else None,
        sim_pin_required=_yes_no(raw.get("lte_is_sim_pin_required")),
        sim_pin_verified=_yes_no(raw.get("lte_is_sim_pin_verified")),
        sim_pin_tries_left=_int(raw.get("lte_sim_pin_tries_left")),
    )


def parse_clients(clients: list[Any]) -> tuple[VpnClient, ...]:
    """Return the remote clients (Teleport, VPN) among the active clients."""
    remote = []
    for client in clients:
        if not isinstance(client, dict):
            continue
        kind = str(client.get("type") or "").upper()
        if not kind or kind in LOCAL_CLIENT_TYPES:
            continue
        # The console's history lists remote clients with status "offline"
        if str(client.get("status") or "").lower() == "offline":
            continue
        name = client.get("display_name") or client.get("name") or client.get("hostname")
        remote.append(
            VpnClient(
                name=str(name or kind),
                type=kind,
                since=_timestamp_s(client.get("assoc_time")),
            )
        )
    return tuple(sorted(remote, key=lambda c: (c.type, c.name)))


# Key of the last known active uplink in the dict ``detect_active_events`` keeps
ACTIVE = "active"


def detect_active_events(known: dict[str, str], state: NetworkState) -> list[UplinkEvent]:
    """Return a failover or return against the last known active uplink, and remember it.

    Comparing against the last known uplink instead of the previous state
    catches a switch that passes through a state with an unknown active uplink.
    """
    active = state.active
    if active is None:
        return []
    previous = known.get(ACTIVE)
    known[ACTIVE] = active
    if previous is None or previous == active:
        return []
    return [
        UplinkEvent(
            EVENT_RESTORED if active == state.primary else EVENT_FAILOVER,
            {
                "from_uplink": previous,
                "from_uplink_name": _name(state, previous),
                "to_uplink": active,
                "to_uplink_name": _name(state, active),
            },
        )
    ]


def brief_failover_candidate(
    previous: NetworkState, state: NetworkState, switch_seen_at: datetime | None
) -> datetime | None:
    """Return a transition timestamp that grew while the active uplink stayed the preferred one.

    That is the trace of a failover and return the integration did not see,
    for example while it was not connected. It is only a candidate: the
    gateway may report the timestamp a push before the switch itself, so the
    caller waits ``SWITCH_TOLERANCE`` for a failover to follow before it
    reports a brief failover. A timestamp within the tolerance after the last
    seen switch belongs to that switch.
    """
    if previous.last_transition is None or state.last_transition is None:
        return None
    if state.last_transition <= previous.last_transition:
        return None
    if not (previous.active == state.active == state.primary):
        return None
    if switch_seen_at is not None and state.last_transition <= switch_seen_at + SWITCH_TOLERANCE:
        return None
    return state.last_transition


def detect_online_events(known: dict[str, bool], state: NetworkState) -> list[UplinkEvent]:
    """Return the uplinks that went down or up against their last known status, and remember it.

    ``known`` maps the uplink key to the last status that was not unknown, so
    a change through an unknown status in between still fires.
    """
    events: list[UplinkEvent] = []
    for key, uplink in state.uplinks.items():
        if uplink.online is None:
            continue
        previous = known.get(key)
        if previous is not None and previous != uplink.online:
            events.append(
                UplinkEvent(
                    EVENT_UPLINK_UP if uplink.online else EVENT_UPLINK_DOWN,
                    {"uplink": key, "uplink_name": uplink.name},
                )
            )
        known[key] = uplink.online
    return events


def detect_address_events(
    known: dict[tuple[str, str], str], state: NetworkState
) -> list[UplinkEvent]:
    """Return the address changes against the last known addresses, and remember the new ones.

    ``known`` maps (uplink key, family) to the last address that was seen.
    Comparing against it instead of the previous state catches a change that
    passes through a state without an address, as after a PPPoE reconnect.
    """
    events: list[UplinkEvent] = []
    for key, uplink in state.uplinks.items():
        for family, current in (("ipv4", uplink.ipv4), ("ipv6", uplink.ipv6)):
            if current is None:
                continue
            previous = known.get((key, family))
            if previous is not None and previous != current:
                events.append(
                    UplinkEvent(
                        EVENT_PUBLIC_IP_CHANGED,
                        {
                            "uplink": key,
                            "uplink_name": uplink.name,
                            "family": family,
                            "address": current,
                            "previous": previous,
                        },
                    )
                )
            known[(key, family)] = current
    return events


def _active(raw: dict[str, Any], uplinks: dict[str, Uplink]) -> str | None:
    """Return the uplink that carries the traffic.

    The gateway names it in ``uplink.comment``. If that is missing or unknown,
    the traffic runs over the first uplink in failover order that is online;
    an uplink with unknown status before it leaves the answer unknown.
    """
    comment = _dict(raw.get("uplink")).get("comment")
    if isinstance(comment, str) and comment in uplinks:
        return comment
    for uplink in uplinks.values():
        if uplink.online is None:
            return None
        if uplink.online:
            return uplink.key
    return None


def _availability(stats: dict[str, Any]) -> float | None:
    """Return the uplink's availability.

    While an uplink is down the gateway drops the summary values and keeps
    only its monitors, each with availability 0; their average is used then.
    """
    if (value := _float(stats.get("availability"))) is not None:
        return value
    entries = stats.get("monitors")
    monitors = [
        v
        for m in (entries if isinstance(entries, list) else [])
        if isinstance(m, dict) and (v := _float(m.get("availability"))) is not None
    ]
    return sum(monitors) / len(monitors) if monitors else None


def _speedtest(uplink: dict[str, Any]) -> Speedtest | None:
    if not any(k in uplink for k in ("xput_down", "xput_up", "speedtest_lastrun")):
        return None
    status = uplink.get("speedtest_status")
    return Speedtest(
        download=_float(uplink.get("xput_down")),
        upload=_float(uplink.get("xput_up")),
        ping=_int(uplink.get("speedtest_ping")),
        last_run=_timestamp_s(uplink.get("speedtest_lastrun")),
        status=str(status) if status else None,
    )


def _link(port: dict[str, Any]) -> dict[str, Any]:
    """Return what the port negotiated: speed in Mbit/s and duplex, nothing while it is down."""
    speed = _int(port.get("speed"))
    if port.get("up") is False or not speed or speed <= 0:
        return {"link_speed": None, "full_duplex": None}
    duplex = port.get("full_duplex")
    return {"link_speed": speed, "full_duplex": duplex if isinstance(duplex, bool) else None}


def _port_key(key: str) -> str:
    """Return the gateway's port object of a network group: WAN -> wan1, WAN2 -> wan2."""
    match = _WAN_GROUP.match(key)
    return f"wan{match.group(1) or 1}" if match else ""


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


def _yes_no(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).lower()
    if text in ("yes", "true"):
        return True
    if text in ("no", "false"):
        return False
    return None


def _ipv4(value: Any) -> str | None:
    """Return the port's IPv4 address.

    Private addresses count: behind another router the uplink's address is
    private, and a change of it is still a change of the uplink.
    """
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return None
    if address.version != 4 or _is_no_address(address):
        return None
    return str(address)


def _public_ipv6(values: Any) -> str | None:
    """Return the first IPv6 address of the list that is not local to the link."""
    for value in values if isinstance(values, list) else []:
        try:
            address = ipaddress.ip_address(str(value))
        except ValueError:
            continue
        if address.version == 6 and not (address.is_link_local or _is_no_address(address)):
            return str(address)
    return None


def _is_no_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_unspecified or address.is_loopback or address.is_multicast


def _name(state: NetworkState, key: str) -> str:
    uplink = state.uplinks.get(key)
    return uplink.name if uplink else key


def _timestamp_ms(value: Any) -> datetime | None:
    millis = _int(value)
    return _timestamp(millis / 1000) if millis and millis > 0 else None


def _timestamp_s(value: Any) -> datetime | None:
    seconds = _int(value)
    return _timestamp(seconds) if seconds and seconds > 0 else None


def _iso(value: Any) -> datetime | None:
    """Return the datetime of an ISO string the entry stored; None if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _timestamp(seconds: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except OverflowError, OSError, ValueError:
        # Far outside what a clock can mean
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except TypeError, ValueError, OverflowError:
        # Infinity passes json.loads and overflows int()
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except TypeError, ValueError, OverflowError:
        return None
    return result if math.isfinite(result) else None
