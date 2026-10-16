"""Tests for reading the gateway and finding events."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.network_plus.model import (
    EVENT_BRIEF_FAILOVER,
    EVENT_FAILOVER,
    EVENT_PUBLIC_IP_CHANGED,
    EVENT_RESTORED,
    EVENT_UPLINK_DOWN,
    EVENT_UPLINK_UP,
    VpnClient,
    detect_events,
    essentials,
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
    raw = gateway(wan="offline", lte="offline")
    del raw["uplink"]
    state = parse_state(raw, wan_configs())
    assert state.active is None
    assert state.backup_active is None


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
    normal = parse_state(gateway(), wan_configs())
    backup = parse_state(
        gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000),
        wan_configs(),
    )
    events = detect_events(normal, backup)
    assert [e.type for e in events] == [EVENT_UPLINK_DOWN, EVENT_FAILOVER]
    assert events[0].attributes == {"uplink": "WAN", "uplink_name": "Cable"}
    assert events[1].attributes == {
        "from_uplink": "WAN",
        "from_uplink_name": "Cable",
        "to_uplink": "WAN_LTE_FAILOVER",
        "to_uplink_name": "LTE",
    }
    back = parse_state(gateway(transition=TRANSITION + 2000), wan_configs())
    assert [e.type for e in detect_events(backup, back)] == [EVENT_UPLINK_UP, EVENT_RESTORED]


def test_brief_failover() -> None:
    """A newer switching time without a seen switch is a brief failover."""
    before = parse_state(gateway(), wan_configs())
    after = parse_state(gateway(transition=TRANSITION + 5000), wan_configs())
    events = detect_events(before, after)
    assert [e.type for e in events] == [EVENT_BRIEF_FAILOVER]
    assert events[0].attributes == {"at": after.last_transition.isoformat()}  # type: ignore[union-attr]


def test_no_events() -> None:
    """Nothing happens without a change, an unknown status or a missing uplink."""
    state = parse_state(gateway(), wan_configs())
    assert detect_events(state, state) == []
    unknown = parse_state(gateway(wan="degraded"), wan_configs())
    assert detect_events(state, unknown) == []
    raw = gateway()
    raw["last_wan_status"]["WAN2"] = "online"
    assert detect_events(state, parse_state(raw, wan_configs())) == []
    # A newer switching time while on the backup is no brief failover
    backup = parse_state(gateway(active="WAN_LTE_FAILOVER"), wan_configs())
    later = parse_state(
        gateway(active="WAN_LTE_FAILOVER", transition=TRANSITION + 1), wan_configs()
    )
    assert detect_events(backup, later) == []


def test_event_name_of_vanished_uplink() -> None:
    """An uplink the gateway no longer reports is named by its key."""
    raw = gateway(active="WAN2")
    raw["last_wan_status"]["WAN2"] = "online"
    before = parse_state(raw, wan_configs())
    after = parse_state(gateway(), wan_configs())
    event = detect_events(before, after)[0]
    assert event.type == EVENT_RESTORED
    assert event.attributes["from_uplink_name"] == "WAN2"


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
    speedtest = parse_state(raw, wan_configs()).speedtest
    assert speedtest is not None
    assert (speedtest.download, speedtest.last_run, speedtest.status) == (None, None, None)


def test_latency_falls_back_to_uplink() -> None:
    """Without monitor values the gateway's uplink latency counts."""
    raw = gateway()
    del raw["uptime_stats"]
    assert parse_state(raw, wan_configs()).latency == 15
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
    state = parse_state(gateway(), wan_configs(), lte_device(), [teleport_client()])
    assert state.lte is not None and state.lte.usage_bytes == 1_500_000
    assert state.vpn_clients == (
        VpnClient("Phone", "TELEPORT", datetime.fromtimestamp(1_700_000_000, UTC)),
    )


def test_essentials() -> None:
    """A push counts for uplinks, addresses, the active uplink and the LTE state, not for numbers."""
    base = parse_state(gateway(), wan_configs(), lte_device())
    same = parse_state(gateway(latency=99, download=1.0), wan_configs(), lte_device(rx=5))
    assert essentials(base) == essentials(same)
    for other in (
        parse_state(gateway(wan="offline"), wan_configs(), lte_device()),
        parse_state(gateway(ipv4="198.51.100.8"), wan_configs(), lte_device()),
        parse_state(gateway(ipv6="2001:db8::8"), wan_configs(), lte_device()),
        parse_state(gateway(active="WAN_LTE_FAILOVER"), wan_configs(), lte_device()),
        parse_state(gateway(transition=TRANSITION + 1), wan_configs(), lte_device()),
        parse_state(gateway(), wan_configs(), lte_device(connected="no")),
        parse_state(gateway(), wan_configs(), lte_device(in_use=True)),
        parse_state(gateway(), wan_configs()),
    ):
        assert essentials(base) != essentials(other)


def test_public_ip_changed_events() -> None:
    """A new address of a known uplink is an event per address family."""
    before = parse_state(gateway(), wan_configs())
    after = parse_state(gateway(ipv4="198.51.100.8", ipv6="2001:db8::8"), wan_configs())
    events = detect_events(before, after)
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
    # An address that appears or vanishes is no change
    raw = gateway()
    del raw["last_wan_interfaces"]
    del raw["wan1"]
    none = parse_state(raw, wan_configs())
    assert detect_events(none, before) == []
    assert detect_events(before, none) == []
