"""Tests for the switch "Enforce backup"."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.network_plus.api import (
    NetworkAuthError,
    NetworkError,
    NetworkPermissionError,
)
from custom_components.network_plus.const import (
    CONF_AUTO_OFF_MINUTES,
    CONF_DRILL,
    CONF_ENFORCED_AT,
    CONF_ENFORCED_WAN_TYPE,
    CONF_LTE_LIMIT_PERCENT,
    DOMAIN,
)

from .conftest import MAC, TRANSITION, FakeConsole, gateway, lte_device, sync

SWITCH = "switch.gateway_enforce_backup_uplink"
DRILL_LAST = "sensor.gateway_last_backup_drill"
DRILL_FAILOVER = "sensor.gateway_backup_drill_failover_time"
DRILL_RESTORE = "sensor.gateway_backup_drill_restore_time"
DRILL_DATA = "sensor.gateway_backup_drill_data_used"
EVENT = "event.gateway_internet"


async def _turn(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: SWITCH}, blocking=True)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_no_switch_without_admin(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Without the admin opt-in there is no switch, and the coordinator refuses to write."""
    assert hass.states.get(SWITCH) is None
    with pytest.raises(HomeAssistantError, match="admin"):
        await setup_entry.runtime_data.coordinator.async_set_wan_type("WAN", "disabled")


async def test_switch_on_and_off(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Switching on disables the preferred uplink and remembers its type; off restores it."""
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]
    await _turn(hass, SERVICE_TURN_ON)
    assert console.writes == [("net-wan", "disabled")]
    assert hass.states.get(SWITCH).state == STATE_ON  # type: ignore[union-attr]
    assert setup_admin_entry.data[CONF_ENFORCED_WAN_TYPE] == "dhcp"
    attrs = hass.states.get(SWITCH).attributes  # type: ignore[union-attr]
    assert attrs["enforced_since"] is not None
    assert attrs["auto_off_at"] > attrs["enforced_since"]
    # The admin credentials were used for the write, the read-only ones for the rest
    assert ("192.0.2.1", "netadmin", "admin-secret") in console.created_with

    await _turn(hass, SERVICE_TURN_OFF)
    assert console.writes[-1] == ("net-wan", "dhcp")
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]
    assert CONF_ENFORCED_WAN_TYPE not in setup_admin_entry.data
    assert hass.states.get(SWITCH).attributes["enforced_since"] is None  # type: ignore[union-attr]


async def test_auto_off(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The preferred uplink comes back by itself after the configured time."""
    hass.config_entries.async_update_entry(setup_admin_entry, options={CONF_AUTO_OFF_MINUTES: 5})
    await hass.async_block_till_done()
    await _turn(hass, SERVICE_TURN_ON)
    freezer.tick(timedelta(minutes=4, seconds=59))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == STATE_ON  # type: ignore[union-attr]
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert console.writes[-1] == ("net-wan", "dhcp")
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]


async def test_auto_off_retries_after_a_failed_write(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed switch-back is tried again a minute later."""
    await _turn(hass, SERVICE_TURN_ON)
    console.write_error = NetworkError("busy")
    freezer.tick(timedelta(minutes=15, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == STATE_ON  # type: ignore[union-attr]
    console.write_error = None
    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]


async def test_auto_off_survives_a_restart(
    hass: HomeAssistant, console: FakeConsole, admin_entry: MockConfigEntry
) -> None:
    """An enforcement older than the auto-off time is undone right after setup."""
    console.configs[0]["wan_type"] = "disabled"
    started = (dt_util.utcnow() - timedelta(minutes=30)).isoformat()
    admin_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        admin_entry,
        data={**admin_entry.data, CONF_ENFORCED_WAN_TYPE: "pppoe", CONF_ENFORCED_AT: started},
    )
    assert await hass.config_entries.async_setup(admin_entry.entry_id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert console.writes == [("net-wan", "pppoe")]
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]


async def test_refuses_while_backup_active(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """While the backup carries the traffic the switch does nothing."""
    await console.push(hass, sync(gateway(wan="offline", active="WAN_LTE_FAILOVER")))
    with pytest.raises(ServiceValidationError, match="already"):
        await _turn(hass, SERVICE_TURN_ON)
    assert console.writes == []


async def test_refuses_near_the_data_plan_limit(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """From the configured LTE usage on the switch refuses; a lower limit can be set."""
    console.devices[2] = lte_device(rx=6_500_000_000, tx=0)  # 81 % of 8 GB
    await _poll(hass, setup_admin_entry)
    with pytest.raises(ServiceValidationError, match="81 %"):
        await _turn(hass, SERVICE_TURN_ON)
    assert console.writes == []
    hass.config_entries.async_update_entry(
        setup_admin_entry, options={CONF_LTE_LIMIT_PERCENT: 90, CONF_AUTO_OFF_MINUTES: 15}
    )
    await hass.async_block_till_done()
    await _turn(hass, SERVICE_TURN_ON)
    assert console.writes == [("net-wan", "disabled")]


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (NetworkAuthError("no"), "rejected the admin credentials"),
        (NetworkPermissionError("403"), "may not change"),
        (NetworkError("boom"), "did not take the change: boom"),
    ],
)
async def test_write_errors(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    error: Exception,
    match: str,
) -> None:
    """Errors of the console come back as readable errors."""
    console.write_error = error
    with pytest.raises(HomeAssistantError, match=match):
        await _turn(hass, SERVICE_TURN_ON)
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]


async def test_console_keeps_another_type(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A write the console silently ignores is an error."""
    console.configs[0]["_id"] = "net-other"
    await _poll(hass, setup_admin_entry)
    console.configs[0]["_id"] = "net-wan"  # the fake now returns "" for net-other
    with pytest.raises(HomeAssistantError, match="kept nothing"):
        await _turn(hass, SERVICE_TURN_ON)


async def test_unavailable_without_configuration(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Without the uplink in the console's configuration the switch is unavailable."""
    del console.configs[0]["_id"]
    await _poll(hass, setup_admin_entry)
    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    with pytest.raises(HomeAssistantError, match="not found"):
        await setup_admin_entry.runtime_data.coordinator.async_set_wan_type("WAN", "disabled")


async def test_without_uplinks(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Without a preferred uplink the switch is unavailable and refuses to switch."""
    raw = gateway()
    raw["last_wan_status"] = {}
    await console.push(hass, sync(raw))
    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    # Home Assistant does not deliver service calls to an unavailable entity;
    # the methods themselves still refuse cleanly
    entity = hass.data["entity_components"]["switch"].get_entity(SWITCH)
    with pytest.raises(HomeAssistantError, match="not found"):
        await entity.async_turn_on()
    with pytest.raises(HomeAssistantError, match="not found"):
        await entity.async_turn_off()


async def test_unknown_wan_type(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A configuration without a WAN type leaves the switch's state unknown."""
    del console.configs[0]["wan_type"]
    await _poll(hass, setup_admin_entry)
    assert hass.states.get(SWITCH).state == STATE_UNKNOWN  # type: ignore[union-attr]


async def test_timer_does_nothing_when_already_off(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Switched back by hand in the console, the timer has nothing left to do."""
    await _turn(hass, SERVICE_TURN_ON)
    console.configs[0]["wan_type"] = "dhcp"
    await _poll(hass, setup_admin_entry)
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]
    writes = len(console.writes)
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(console.writes) == writes


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def test_drill_protocol(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A drill times the failover and the return and counts the LTE data it cost."""
    for entity_id in (DRILL_LAST, DRILL_FAILOVER, DRILL_RESTORE, DRILL_DATA):
        assert _state(hass, entity_id) == STATE_UNKNOWN
    assert hass.states.get(DRILL_LAST).attributes["completed"] is None  # type: ignore[union-attr]
    await _turn(hass, SERVICE_TURN_ON)
    started = dt_util.utcnow()
    # Timestamp sensors render without microseconds
    assert _state(hass, DRILL_LAST) == started.isoformat(timespec="seconds")
    assert hass.states.get(DRILL_LAST).attributes["completed"] is False  # type: ignore[union-attr]
    assert setup_admin_entry.data[CONF_DRILL]["usage_start"] == 1_500_000

    freezer.tick(timedelta(seconds=15))
    failed_over = gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000)
    console.set_gateway(failed_over)
    await console.push(hass, sync(failed_over))
    assert _state(hass, DRILL_FAILOVER) == "15.0"
    assert _state(hass, DRILL_RESTORE) == STATE_UNKNOWN
    # Data flows over LTE meanwhile
    console.devices[2] = lte_device(rx=2_000_000, tx=500_000)
    await _poll(hass, setup_admin_entry)

    freezer.tick(timedelta(seconds=60))
    await _turn(hass, SERVICE_TURN_OFF)
    assert setup_admin_entry.data[CONF_DRILL]["ended_at"] == dt_util.utcnow().isoformat()
    assert _state(hass, DRILL_RESTORE) == STATE_UNKNOWN
    freezer.tick(timedelta(seconds=12))
    back = gateway(transition=TRANSITION + 2000)
    console.set_gateway(back)
    await console.push(hass, sync(back))
    assert _state(hass, DRILL_RESTORE) == "12.0"
    assert float(_state(hass, DRILL_DATA)) == 1.0  # 1 000 000 bytes, shown in MB
    last = hass.states.get(DRILL_LAST)
    assert last.state == started.isoformat(timespec="seconds")  # type: ignore[union-attr]
    assert last.attributes["completed"] is True  # type: ignore[union-attr]
    assert last.attributes["data_used_bytes"] == 1_000_000  # type: ignore[union-attr]
    event = hass.states.get(EVENT)
    assert event.attributes["event_type"] == "drill_completed"  # type: ignore[union-attr]
    assert event.attributes["failover_seconds"] == 15.0  # type: ignore[union-attr]
    assert event.attributes["restore_seconds"] == 12.0  # type: ignore[union-attr]
    assert event.attributes["data_used_bytes"] == 1_000_000  # type: ignore[union-attr]
    # Another return changes nothing
    await console.push(hass, sync(failed_over))
    await console.push(hass, sync(back))
    assert _state(hass, DRILL_RESTORE) == "12.0"


async def test_drill_back_before_the_switch_back(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Switched back by hand in the console: the drill completes without a restore time."""
    await _turn(hass, SERVICE_TURN_ON)
    failed_over = gateway(wan="offline", active="WAN_LTE_FAILOVER", transition=TRANSITION + 1000)
    console.set_gateway(failed_over)
    await console.push(hass, sync(failed_over))
    freezer.tick(timedelta(seconds=30))
    await console.push(hass, sync(gateway(transition=TRANSITION + 2000)))
    assert hass.states.get(DRILL_LAST).attributes["completed"] is True  # type: ignore[union-attr]
    assert _state(hass, DRILL_RESTORE) == STATE_UNKNOWN
    assert _state(hass, DRILL_FAILOVER) == "0.0"
    assert hass.states.get(EVENT).attributes["event_type"] == "drill_completed"  # type: ignore[union-attr]


async def test_drill_keeps_running_when_the_switch_back_fails(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A switch-back the console refuses does not end the drill."""
    await _turn(hass, SERVICE_TURN_ON)
    console.write_error = NetworkError("busy")
    with pytest.raises(HomeAssistantError):
        await _turn(hass, SERVICE_TURN_OFF)
    assert setup_admin_entry.data[CONF_DRILL]["ended_at"] is None
    console.write_error = None
    await _turn(hass, SERVICE_TURN_OFF)
    assert setup_admin_entry.data[CONF_DRILL]["ended_at"] is not None


async def test_failed_switch_off_keeps_the_timer(
    hass: HomeAssistant,
    setup_admin_entry: MockConfigEntry,
    console: FakeConsole,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A switch-off the console refuses leaves the automatic switch-back armed."""
    await _turn(hass, SERVICE_TURN_ON)
    console.write_error = NetworkError("busy")
    with pytest.raises(HomeAssistantError):
        await _turn(hass, SERVICE_TURN_OFF)
    console.write_error = None
    freezer.tick(timedelta(minutes=15, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert console.writes[-1] == ("net-wan", "dhcp")
    assert hass.states.get(SWITCH).state == STATE_OFF  # type: ignore[union-attr]


async def test_drill_survives_a_restart(
    hass: HomeAssistant, console: FakeConsole, admin_entry: MockConfigEntry
) -> None:
    """The last drill comes back from the entry; unreadable data gives none."""
    admin_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        admin_entry,
        data={
            **admin_entry.data,
            CONF_DRILL: {
                "started_at": "2024-01-01T12:00:00+00:00",
                "usage_start": 10,
                "failover_at": "2024-01-01T12:00:20+00:00",
                "ended_at": "2024-01-01T12:05:00+00:00",
                "restored_at": "2024-01-01T12:05:15+00:00",
                "usage_end": 40_000_010,
            },
        },
    )
    assert await hass.config_entries.async_setup(admin_entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, DRILL_LAST) == "2024-01-01T12:00:00+00:00"
    assert _state(hass, DRILL_FAILOVER) == "20.0"
    assert _state(hass, DRILL_RESTORE) == "15.0"
    assert float(_state(hass, DRILL_DATA)) == 40.0
    hass.config_entries.async_update_entry(admin_entry, data={**admin_entry.data, CONF_DRILL: "x"})
    await hass.config_entries.async_reload(admin_entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, DRILL_LAST) == STATE_UNKNOWN


async def test_no_drill_sensors_without_admin(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """Without the admin opt-in there are no drill sensors, and old ones are removed."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, f"{MAC}_drill_last", config_entry=config_entry)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(DRILL_LAST) is None
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_drill_last") is None
