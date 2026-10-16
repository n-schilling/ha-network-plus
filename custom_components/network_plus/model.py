"""What the integration knows about the gateway, its uplinks and the LTE backup.

Read from the devices' runtime objects (``stat/device`` or the websocket's
``device:sync``, both carry the same fields), the uplink configuration and
the console's list of active clients. Only named fields are taken over: the
raw objects hold secrets such as the SIM PIN and the APN credentials of an
LTE device, and they never leave this module.
"""

from __future__ import annotations

from dataclasses import dataclass
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
EVENT_TYPES = [
    EVENT_FAILOVER,
    EVENT_RESTORED,
    EVENT_BRIEF_FAILOVER,
    EVENT_UPLINK_DOWN,
    EVENT_UPLINK_UP,
    EVENT_PUBLIC_IP_CHANGED,
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

    @property
    def usage_percent(self) -> float | None:
        """Return how much of the data plan is used, in percent."""
        if self.usage_bytes is None or not self.hard_limit or not self.limit_enabled:
            return None
        return self.usage_bytes / self.hard_limit * 100


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
        tuple((u.key, u.online, u.ipv4, u.ipv6) for u in state.uplinks.values()),
        state.active,
        state.last_transition,
        tuple((mac, lte.connected, lte.in_use) for mac, lte in (state.lte or {}).items()),
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
    for config in configs:
        key = config.get("wan_networkgroup")
        if not isinstance(key, str):
            continue
        names[key] = str(config.get("name") or key)
        priorities[key] = _int(config.get("wan_failover_priority")) or 0

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
