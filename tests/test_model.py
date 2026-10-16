"""Tests for reading the gateway and finding events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.network_plus.model import (
    EVENT_FAILOVER,
    EVENT_PUBLIC_IP_CHANGED,
    EVENT_RESTORED,
    EVENT_UPLINK_DOWN,
    EVENT_UPLINK_UP,
    Drill,
    UplinkEvent,
    VpnClient,
    brief_failover_candidate,
    detect_active_events,
    detect_address_events,
    detect_online_events,
    drill_progress,
    essentials,
    lte_usage,
    normalize_mac,
    parse_clients,
    parse_lte,
    parse_state,
)

from .conftest import (
    IPV4,
    IPV6,
    LTE_MAC,
    MAC,
    SPEEDTEST_RUN,
    TRANSITION,
    gateway,
    lte_device,
    teleport_client,
    wan_configs,
)


def test_parse_state() -> None:
    """Uplinks come in failover order, with names, latency and availability."""
    state = parse_state(gateway(), wan_configs())
    assert state.gateway.mac == MAC
    assert state.gateway.model == "UDMPRO"
    assert state.gateway.version == "4.0.0"
    # WAN2 is configured, but the gateway does not monitor it
    assert list(state.uplinks) == ["WAN", "WAN_LTE_FAILOVER"]
    wan = state.uplinks["WAN"]
    assert (wan.name, wan.online, wan.latency, wan.availability) == ("Cable", True, 14, 100.0)
    assert (wan.ipv4, wan.ipv6) == (IPV4, IPV6)
    assert state.uplinks["WAN_LTE_FAILOVER"].ipv4 is None
    assert state.latency == 14
    assert state.lte is None
    assert state.vpn_clients is None
    assert state.uplinks["WAN_LTE_FAILOVER"].name == "LTE"
    assert state.primary == "WAN"
    assert state.active == "WAN"
    assert state.backup_active is False
    assert state.last_transition == datetime.fromtimestamp(TRANSITION / 1000, UTC)


def test_failover_order_without_configuration() -> None:
    """Without configuration the key is the name and the order is alphabetical."""
    raw = gateway()
    raw["last_wan_status"] = {"WAN2": "online", "WAN": "online"}
    state = parse_state(raw, [])
    assert list(state.uplinks) == ["WAN", "WAN2"]
    assert state.uplinks["WAN2"].name == "WAN2"


def test_backup_active() -> None:
    """The backup is active while the gateway names it as uplink."""
    state = parse_state(gateway(wan="offline", active="WAN_LTE_FAILOVER"), wan_configs())
    assert state.uplinks["WAN"].online is False
    assert state.active == "WAN_LTE_FAILOVER"
    assert state.backup_active is True


def test_unknown_status_is_unknown() -> None:
    """A status the integration does not know is neither online nor offline."""
    state = parse_state(gateway(wan="degraded"), wan_configs())
    assert state.uplinks["WAN"].online is None


def test_active_falls_back_to_first_online() -> None:
    """Without a known uplink name the first online uplink carries the traffic."""
    state = parse_state(gateway(wan="offline", active="eth9"), wan_configs())
    assert state.active == "WAN_LTE_FAILOVER"
    # An uplink with unknown status before the first online one leaves it unknown
    state = parse_state(gateway(wan="degraded", active="eth9"), wan_configs())
    assert state.active is None
    assert state.backup_active is None
    raw = gateway(wan="offline", lte="offline")
    del raw["uplink"]
    state = parse_state(raw, wan_configs())
    assert state.active is None
    assert state.backup_active is None


def test_values_beyond_any_clock_or_number() -> None:
    """Infinity, NaN, huge timestamps and odd monitor fields give None, not an error."""
    raw = gateway()
    raw["last_lte_failover_transition_timestamp"] = float("inf")
    raw["uptime_stats"]["WAN"] = {"latency_average": float("nan"), "monitors": 5}
    raw["uplink"]["xput_down"] = float("inf")
    raw["uplink"]["speedtest_lastrun"] = 10**20
    state = parse_state(raw, wan_configs())
    assert state.last_transition is None
    wan = state.uplinks["WAN"]
    assert (wan.latency, wan.availability) == (None, None)
    assert state.speedtest is not None
    assert (state.speedtest.download, state.speedtest.last_run) == (None, None)
    assert parse_lte(lte_device(rx=float("inf"))).usage_bytes is None  # type: ignore[arg-type]


def test_missing_and_odd_fields() -> None:
    """Missing or malformed fields give None instead of an error."""
    raw = {
        "mac": "02:A1:B2:C3:D4:E5",
        "last_wan_status": {"WAN": "online"},
        "uptime_stats": {"WAN": {"latency_average": True, "availability": "n/a"}},
        "last_lte_failover_transition_timestamp": 0,
    }
    configs = [{"name": "No group"}, {"wan_networkgroup": "WAN", "wan_failover_priority": "x"}]
    state = parse_state(raw, configs)
    assert state.gateway.mac == "02:a1:b2:c3:d4:e5"
    assert state.gateway.name == "Gateway"
    assert state.gateway.model == "Gateway"
    assert state.gateway.version is None
    wan = state.uplinks["WAN"]
    assert (wan.name, wan.priority, wan.latency, wan.availability) == ("WAN", 0, None, None)
    assert state.last_transition is None
    raw["last_wan_status"] = "online"
    raw["uptime_stats"] = []
    assert parse_state(raw, []).uplinks == {}
    assert parse_state(raw, []).primary is None
    raw["uptime_stats"] = {"WAN": {"availability": True}}
    raw["last_wan_status"] = {"WAN": "online"}
    assert parse_state(raw, []).uplinks["WAN"].availability is None


def test_events_failover_and_restore() -> None:
    """Switching to the backup and back are two events, each naming both uplinks."""
    known: dict[str, str] = {}
    normal = parse_state(gateway(), wan_configs())
    assert detect_active_events(known, normal) == []  # the first state only seeds
    backup = parse_state(
        gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000),
        wan_configs(),
    )
    events = detect_active_events(known, backup)
    assert [e.type for e in events] == [EVENT_FAILOVER]
    assert events[0].attributes == {
        "from_uplink": "WAN",
        "from_uplink_name": "Cable",
        "to_uplink": "WAN_LTE_FAILOVER",
        "to_uplink_name": "LTE",
    }
    assert detect_active_events(known, backup) == []
    back = parse_state(gateway(transition=TRANSITION + 2000), wan_configs())
    assert [e.type for e in detect_active_events(known, back)] == [EVENT_RESTORED]


def test_active_events_through_unknown() -> None:
    """A switch through a state with an unknown active uplink still fires."""
    known: dict[str, str] = {}
    detect_active_events(known, parse_state(gateway(), wan_configs()))
    unknown = parse_state(gateway(wan="degraded", active="eth9"), wan_configs())
    assert unknown.active is None
    assert detect_active_events(known, unknown) == []
    backup = parse_state(gateway(wan="offline", active="WAN_LTE_FAILOVER"), wan_configs())
    assert [e.type for e in detect_active_events(known, backup)] == [EVENT_FAILOVER]


def test_online_events_through_unknown() -> None:
    """Down and up are found against the last known status, also through an unknown one."""
    known: dict[str, bool] = {}
    assert detect_online_events(known, parse_state(gateway(), wan_configs())) == []
    assert known == {"WAN": True, "WAN_LTE_FAILOVER": True}
    assert detect_online_events(known, parse_state(gateway(wan="degraded"), wan_configs())) == []
    assert known["WAN"] is True
    events = detect_online_events(known, parse_state(gateway(wan="offline"), wan_configs()))
    assert [e.type for e in events] == [EVENT_UPLINK_DOWN]
    assert events[0].attributes == {"uplink": "WAN", "uplink_name": "Cable"}
    assert detect_online_events(known, parse_state(gateway(wan="offline"), wan_configs())) == []
    events = detect_online_events(known, parse_state(gateway(), wan_configs()))
    assert [e.type for e in events] == [EVENT_UPLINK_UP]


def test_brief_failover() -> None:
    """A newer switching time without a seen switch is a candidate for a brief failover."""
    before = parse_state(gateway(), wan_configs())
    after = parse_state(gateway(transition=TRANSITION + 5000), wan_configs())
    assert brief_failover_candidate(before, after, None) == after.last_transition
    # No candidate without timestamps, without a change, or off the preferred uplink
    assert brief_failover_candidate(after, after, None) is None
    assert brief_failover_candidate(after, before, None) is None
    none = parse_state(gateway(transition=0), wan_configs())
    assert brief_failover_candidate(none, after, None) is None
    assert brief_failover_candidate(before, none, None) is None
    on_lte = parse_state(
        gateway(active="WAN_LTE_FAILOVER", transition=TRANSITION + 1), wan_configs()
    )
    assert brief_failover_candidate(before, on_lte, None) is None


def test_no_events() -> None:
    """Nothing happens without a change, an unknown status or a missing uplink."""
    state = parse_state(gateway(), wan_configs())
    online: dict[str, bool] = {}
    active: dict[str, str] = {}
    detect_online_events(online, state)
    detect_active_events(active, state)
    assert detect_online_events(online, state) == []
    assert detect_active_events(active, state) == []
    unknown = parse_state(gateway(wan="degraded"), wan_configs())
    assert detect_online_events(online, unknown) == []
    raw = gateway()
    raw["last_wan_status"]["WAN2"] = "online"
    assert detect_online_events(online, parse_state(raw, wan_configs())) == []


def test_event_name_of_vanished_uplink() -> None:
    """An uplink the gateway no longer reports is named by its key."""
    known = {"active": "WAN2"}
    event = detect_active_events(known, parse_state(gateway(), wan_configs()))[0]
    assert event.type == EVENT_RESTORED
    assert event.attributes["from_uplink_name"] == "WAN2"


def test_normalize_mac() -> None:
    """MACs compare lower case; a missing one is empty."""
    assert normalize_mac("02:A1:B2:C3:D4:E5") == "02:a1:b2:c3:d4:e5"
    assert normalize_mac(None) == ""
    assert normalize_mac("") == ""


def test_availability_of_a_down_uplink() -> None:
    """Without summary values the monitors' average counts; without monitors nothing."""
    raw = gateway(wan="offline", active="WAN_LTE_FAILOVER")
    raw["uptime_stats"]["WAN"] = {
        "downtime": 45,
        "monitors": [
            {"availability": 0.0, "target": "a", "type": "icmp"},
            {"availability": 50.0, "target": "b", "type": "icmp"},
            {"target": "c"},
            "junk",
        ],
    }
    wan = parse_state(raw, wan_configs()).uplinks["WAN"]
    assert (wan.online, wan.latency, wan.availability) == (False, None, 25.0)
    raw["uptime_stats"]["WAN"] = {"monitors": "none"}
    assert parse_state(raw, wan_configs()).uplinks["WAN"].availability is None


def test_speedtest() -> None:
    """The last speed test comes from the gateway's uplink object."""
    speedtest = parse_state(gateway(), wan_configs()).speedtest
    assert speedtest is not None
    assert (speedtest.download, speedtest.upload, speedtest.ping) == (949.0, 54.0, 18)
    assert speedtest.last_run == datetime.fromtimestamp(SPEEDTEST_RUN, UTC)
    assert speedtest.status == "Success"
    raw = gateway()
    raw["uplink"] = {"comment": "WAN", "latency": 15}
    assert parse_state(raw, wan_configs()).speedtest is None
    raw["uplink"] = {"xput_down": "fast", "speedtest_lastrun": 0, "speedtest_status": ""}
    del raw["speedtest-status"]
    speedtest = parse_state(raw, wan_configs()).speedtest
    assert speedtest is not None
    assert (speedtest.download, speedtest.last_run, speedtest.status) == (None, None, None)
    # Without the status block: no running flag, and the active uplink is the best guess
    assert (speedtest.running, speedtest.uplink) == (None, "WAN")


def test_speedtest_uplink() -> None:
    """The interface of the last test maps to a WAN port's uplink, anything else to the backup."""
    assert parse_state(gateway(), wan_configs()).speedtest.uplink == "WAN"  # type: ignore[union-attr]
    # The LTE backup routes over a tunnel, which is no WAN port
    over_lte = parse_state(gateway(interface="gre1"), wan_configs()).speedtest
    assert over_lte.uplink == "WAN_LTE_FAILOVER"  # type: ignore[union-attr]
    # Only source_interface, with the console's prefix
    raw = gateway()
    del raw["speedtest-status"]["interface_name"]
    assert parse_state(raw, wan_configs()).speedtest.uplink == "WAN"  # type: ignore[union-attr]
    # No interface at all: the active uplink
    raw = gateway(interface=None, active="WAN_LTE_FAILOVER")
    assert parse_state(raw, wan_configs()).speedtest.uplink == "WAN_LTE_FAILOVER"  # type: ignore[union-attr]
    # An unknown interface without a backup uplink to blame: the active uplink
    raw = gateway(interface="eth9")
    raw["last_wan_status"] = {"WAN": "online"}
    assert parse_state(raw, wan_configs()).speedtest.uplink == "WAN"  # type: ignore[union-attr]


def test_speedtest_running() -> None:
    """The status block says whether a test is in progress."""
    assert parse_state(gateway(), wan_configs()).speedtest.running is False  # type: ignore[union-attr]
    running = parse_state(gateway(speedtest_summary=1), wan_configs()).speedtest
    assert running.running is True  # type: ignore[union-attr]
    raw = gateway()
    raw["speedtest-status"]["status_summary"] = "soon"
    assert parse_state(raw, wan_configs()).speedtest.running is None  # type: ignore[union-attr]


def test_latency_falls_back_to_uplink() -> None:
    """Without monitor values the gateway's uplink latency counts."""
    raw = gateway()
    del raw["uptime_stats"]
    assert parse_state(raw, wan_configs()).latency == 15
    assert parse_state(gateway(latency=0), wan_configs()).latency == 0  # 0 ms is a value
    raw = gateway(wan="offline", lte="offline", active="none")
    del raw["uplink"]
    assert parse_state(raw, wan_configs()).latency is None


def test_addresses() -> None:
    """Addresses come per uplink: IPv4 by network group, IPv6 from the matching port."""
    raw = gateway()
    raw["last_wan_status"]["WAN2"] = "online"
    raw["last_wan_interfaces"]["WAN2"] = {"ip": "not an address"}
    raw["wan2"] = {"ipv6": ["fe80::2", "::1", "ff02::1", "::", "junk", "2001:db8::9"]}
    state = parse_state(raw, wan_configs())
    assert (state.uplinks["WAN2"].ipv4, state.uplinks["WAN2"].ipv6) == (None, "2001:db8::9")
    raw["last_wan_interfaces"]["WAN2"] = {"ip": "0.0.0.0"}  # unspecified is no address
    assert parse_state(raw, wan_configs()).uplinks["WAN2"].ipv4 is None
    raw["last_wan_interfaces"]["WAN2"] = {"ip": "192.168.178.2"}  # private counts: double NAT
    raw["wan2"] = {"ipv6": ["fd00::2"]}  # a ULA counts as well
    wan2 = parse_state(raw, wan_configs()).uplinks["WAN2"]
    assert (wan2.ipv4, wan2.ipv6) == ("192.168.178.2", "fd00::2")
    raw["last_wan_interfaces"]["WAN"] = {"ip": "2001:db8::1"}  # an IPv6 is no IPv4
    raw["wan1"] = {"ipv6": "2001:db8::7"}  # a string is no list
    state = parse_state(raw, wan_configs())
    assert (state.uplinks["WAN"].ipv4, state.uplinks["WAN"].ipv6) == (None, None)
    raw["wan1"] = {"ipv6": ["fe80::1"]}
    assert parse_state(raw, wan_configs()).uplinks["WAN"].ipv6 is None


def test_parse_lte() -> None:
    """The LTE device gives usage, limits and signal; secrets are not taken over."""
    lte = parse_lte(lte_device())
    assert (lte.mac, lte.name, lte.model, lte.version) == (
        LTE_MAC,
        "LTE backup",
        "ULTEPRO",
        "6.0.0",
    )
    assert (lte.connected, lte.in_use) == (True, False)
    assert (lte.usage_bytes, lte.soft_limit, lte.hard_limit) == (
        1_500_000,
        6_000_000_000,
        8_000_000_000,
    )
    assert lte.limit_enabled is True
    assert (lte.rssi, lte.rsrp) == (-77, -112)
    assert lte.usage_percent == 0.01875
    assert "9876" not in repr(lte)
    assert "Carrier" not in repr(lte)


def test_parse_lte_odd_values() -> None:
    """Missing or odd fields give None, not an error."""
    lte = parse_lte(lte_device(connected=True, in_use="yes", hard=None, soft=None, limit=False))
    assert (lte.connected, lte.in_use, lte.hard_limit, lte.limit_enabled) == (
        True,
        None,
        None,
        False,
    )
    assert lte.usage_percent is None
    # A limit the console keeps but does not enforce gives no share either
    assert parse_lte(lte_device(limit=False)).usage_percent is None
    assert parse_lte(lte_device(connected="no")).connected is False
    assert parse_lte(lte_device(connected="maybe")).connected is None
    raw = lte_device(hard=0)
    del raw["rx_bytes"]
    del raw["name"]
    del raw["version"]
    lte = parse_lte(raw)
    assert (lte.usage_bytes, lte.usage_percent, lte.name, lte.version) == (
        None,
        None,
        "ULTEPRO",
        None,
    )
    raw = {"lte_state": "ready"}
    lte = parse_lte(raw)
    assert (lte.mac, lte.name, lte.model) == ("", "LTE backup", "LTE backup")


def test_parse_clients() -> None:
    """Only remote clients count, sorted by kind and name, named as well as possible."""
    clients = [
        {"type": "WIRED", "display_name": "Desk"},
        {"type": "WIRELESS", "display_name": "Laptop"},
        teleport_client("Zed"),
        teleport_client("Amy"),
        {"type": "VPN", "name": "Office", "assoc_time": "soon"},
        {"type": "vpn", "hostname": "road-warrior"},
        {"type": "VPN"},
        {"type": "TELEPORT", "display_name": "Gone", "status": "offline"},
        {"display_name": "No type"},
        "junk",
    ]
    assert parse_clients(clients) == (
        VpnClient("Amy", "TELEPORT", datetime.fromtimestamp(1_700_000_000, UTC)),
        VpnClient("Zed", "TELEPORT", datetime.fromtimestamp(1_700_000_000, UTC)),
        VpnClient("Office", "VPN", None),
        VpnClient("VPN", "VPN", None),
        VpnClient("road-warrior", "VPN", None),
    )
    assert parse_clients([]) == ()


def test_state_with_lte_and_clients() -> None:
    """LTE device and clients become part of the state."""
    state = parse_state(gateway(), wan_configs(), [lte_device()], [teleport_client()])
    assert state.lte is not None and state.lte[LTE_MAC].usage_bytes == 1_500_000
    assert parse_state(gateway(), wan_configs(), []).lte == {}
    assert state.vpn_clients == (
        VpnClient("Phone", "TELEPORT", datetime.fromtimestamp(1_700_000_000, UTC)),
    )


def test_essentials() -> None:
    """A push counts for uplinks, addresses, the active uplink, the LTE state and a speed test.

    Numbers alone (latency, a changed result with the same run time) do not count.
    """
    base = parse_state(gateway(), wan_configs(), [lte_device()])
    same = parse_state(gateway(latency=99, download=1.0), wan_configs(), [lte_device(rx=5)])
    assert essentials(base) == essentials(same)
    started = gateway(speedtest_summary=1)
    finished = gateway()
    finished["uplink"]["speedtest_lastrun"] = SPEEDTEST_RUN + 60
    for other in (
        parse_state(started, wan_configs(), [lte_device()]),
        parse_state(finished, wan_configs(), [lte_device()]),
        parse_state(gateway(wan="offline"), wan_configs(), [lte_device()]),
        parse_state(gateway(ipv4="198.51.100.8"), wan_configs(), [lte_device()]),
        parse_state(gateway(ipv6="2001:db8::8"), wan_configs(), [lte_device()]),
        parse_state(gateway(active="WAN_LTE_FAILOVER"), wan_configs(), [lte_device()]),
        parse_state(gateway(transition=TRANSITION + 1), wan_configs(), [lte_device()]),
        parse_state(gateway(), wan_configs(), [lte_device(connected="no")]),
        parse_state(gateway(), wan_configs(), [lte_device(in_use=True)]),
        parse_state(gateway(), wan_configs(), [lte_device(), lte_device(mac="02:a1:b2:c3:d4:e7")]),
        parse_state(gateway(), wan_configs()),
    ):
        assert essentials(base) != essentials(other)


def test_address_events_across_an_outage() -> None:
    """Address changes count against the last known address, also through a gap."""
    known: dict[tuple[str, str], str] = {}
    before = parse_state(gateway(), wan_configs())
    assert detect_address_events(known, before) == []  # the first state only seeds
    assert known == {("WAN", "ipv4"): IPV4, ("WAN", "ipv6"): IPV6}
    raw = gateway()
    del raw["last_wan_interfaces"]
    del raw["wan1"]
    assert detect_address_events(known, parse_state(raw, wan_configs())) == []
    assert known[("WAN", "ipv4")] == IPV4  # a vanished address is kept
    after = parse_state(gateway(ipv4="198.51.100.8", ipv6="2001:db8::8"), wan_configs())
    events = detect_address_events(known, after)
    assert [e.type for e in events] == [EVENT_PUBLIC_IP_CHANGED, EVENT_PUBLIC_IP_CHANGED]
    assert events[0].attributes == {
        "uplink": "WAN",
        "uplink_name": "Cable",
        "family": "ipv4",
        "address": "198.51.100.8",
        "previous": IPV4,
    }
    assert events[1].attributes["family"] == "ipv6"
    assert events[1].attributes["address"] == "2001:db8::8"
    assert detect_address_events(known, after) == []


def test_brief_failover_not_for_a_seen_switch() -> None:
    """A transition timestamp within the tolerance after the last seen switch is no candidate."""
    before = parse_state(gateway(), wan_configs())
    after = parse_state(gateway(transition=TRANSITION + 5000), wan_configs())
    assert after.last_transition is not None
    seen = after.last_transition
    assert brief_failover_candidate(before, after, seen + timedelta(seconds=1)) is None
    assert brief_failover_candidate(before, after, seen - timedelta(seconds=29)) is None
    assert brief_failover_candidate(before, after, seen - timedelta(seconds=31)) == seen


def test_link_speed_of_the_port() -> None:
    """The WAN port's negotiated speed and duplex; nothing while the link is down."""
    state = parse_state(gateway(), wan_configs())
    wan = state.uplinks["WAN"]
    assert (wan.port, wan.link_speed, wan.full_duplex) == ("wan1", 2500, True)
    lte = state.uplinks["WAN_LTE_FAILOVER"]
    assert (lte.port, lte.link_speed, lte.full_duplex) == (None, None, None)
    down = parse_state(gateway(up=False), wan_configs()).uplinks["WAN"]
    assert (down.link_speed, down.full_duplex) == (None, None)
    zero = parse_state(gateway(speed=0), wan_configs()).uplinks["WAN"]
    assert zero.link_speed is None
    raw = gateway(duplex=True)
    raw["wan1"]["full_duplex"] = "yes"
    assert parse_state(raw, wan_configs()).uplinks["WAN"].full_duplex is None
    # A changed link speed is essential: a push with it updates the state
    assert essentials(state) != essentials(parse_state(gateway(speed=1000), wan_configs()))


@pytest.mark.parametrize(
    ("required", "verified", "tries", "expected"),
    [
        (True, True, 3, False),
        (True, False, 2, True),
        (True, True, 0, True),
        (False, None, None, False),
        (None, None, None, None),
        (True, None, 3, None),
        ("yes", "no", "1", True),
    ],
)
def test_sim_problem(
    required: object, verified: object, tries: object, expected: bool | None
) -> None:
    """A PIN the device could not verify, or no tries left, is a SIM problem."""
    lte = parse_lte(lte_device(pin_required=required, pin_verified=verified, pin_tries=tries))
    assert lte.sim_problem is expected
    assert lte.state == "ready"


def test_drill_results() -> None:
    """Durations and data of a drill, and what stays unknown."""
    start = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    drill = Drill(started_at=start, usage_start=1000)
    assert (drill.completed, drill.failover_seconds, drill.restore_seconds, drill.data_used) == (
        False,
        None,
        None,
        None,
    )
    done = Drill(
        started_at=start,
        usage_start=1000,
        failover_at=start + timedelta(seconds=14),
        ended_at=start + timedelta(minutes=2),
        restored_at=start + timedelta(minutes=2, seconds=16),
        usage_end=61_000,
    )
    assert done.completed is True
    assert (done.failover_seconds, done.restore_seconds, done.data_used) == (14.0, 16.0, 60_000)
    # Came back before the switch-back (by hand in the console): no restore time
    manual = Drill(started_at=start, restored_at=start + timedelta(seconds=30))
    assert manual.completed is True and manual.restore_seconds is None
    early = Drill(
        started_at=start,
        ended_at=start + timedelta(seconds=40),
        restored_at=start + timedelta(seconds=30),
    )
    assert early.restore_seconds is None
    # A monthly reset in between makes the data unknown, a clock step the failover time 0
    reset = Drill(started_at=start, usage_start=5000, usage_end=100, restored_at=start)
    assert reset.data_used is None
    assert Drill(started_at=start, failover_at=start - timedelta(seconds=5)).failover_seconds == 0.0


def test_drill_stored_and_read_back() -> None:
    """The entry stores the drill as strings and numbers; unreadable data gives no drill."""
    start = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    drill = Drill(started_at=start, usage_start=7, failover_at=start + timedelta(seconds=9))
    assert Drill.from_dict(drill.as_dict()) == drill
    assert drill.event_attributes() == {
        "started_at": "2024-01-01T12:00:00+00:00",
        "failover_seconds": 9.0,
        "restore_seconds": None,
        "data_used_bytes": None,
    }
    assert Drill.from_dict(None) is None
    assert Drill.from_dict({"usage_start": 1}) is None
    assert Drill.from_dict({"started_at": "yesterday"}) is None
    naive = Drill.from_dict({"started_at": "2024-01-01T12:00:00", "ended_at": 5})
    assert naive is not None and naive.started_at == start and naive.ended_at is None


def test_drill_progress() -> None:
    """The first failover and the return advance the drill; anything else leaves it."""
    start = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    drill = Drill(started_at=start, usage_start=100)
    failover = UplinkEvent(EVENT_FAILOVER, {})
    restored = UplinkEvent(EVENT_RESTORED, {})
    at = start + timedelta(seconds=20)
    stepped = drill_progress(drill, [failover], at, 150)
    assert stepped.failover_at == at and stepped.usage_end is None
    # A second failover (flapping) keeps the first time
    assert drill_progress(stepped, [failover], at + timedelta(seconds=5), 160) == stepped
    done = drill_progress(stepped, [restored], at + timedelta(seconds=60), 400)
    assert done.completed and done.usage_end == 400 and done.data_used == 300
    assert drill_progress(done, [restored], at + timedelta(seconds=90), 500) == done
    assert drill_progress(drill, [UplinkEvent(EVENT_UPLINK_DOWN, {})], at, 1) == drill


def test_lte_usage_over_all_devices() -> None:
    """The usage of all LTE devices counts; none reporting gives None."""
    state = parse_state(
        gateway(), wan_configs(), [lte_device(), lte_device(mac="02:00:00:00:00:09", rx=10, tx=5)]
    )
    assert lte_usage(state) == 1_500_015
    assert lte_usage(parse_state(gateway(), wan_configs())) is None
    raw = lte_device()
    del raw["rx_bytes"]
    assert lte_usage(parse_state(gateway(), wan_configs(), [raw])) is None
