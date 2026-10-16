"""Tests for the repair issues."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SERVICE_TURN_ON
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from custom_components.network_plus.api import (
    NetworkAuthError,
    NetworkError,
    NetworkPermissionError,
)
from custom_components.network_plus.const import (
    DOMAIN,
    ISSUE_ADMIN,
    ISSUE_LTE_LIMIT,
    ISSUE_UNREACHABLE,
    UNREACHABLE_POLLS,
)

from .conftest import LTE_MAC, FakeConsole, lte_device


def _issue(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{key}_{entry.entry_id}")


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_console_unreachable(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """After a quarter of an hour without answers an issue says so; the next answer clears it."""
    console.error = NetworkError("down")
    for _ in range(UNREACHABLE_POLLS - 1):
        await _poll(hass, setup_entry)
    assert _issue(hass, setup_entry, ISSUE_UNREACHABLE) is None
    await _poll(hass, setup_entry)
    issue = _issue(hass, setup_entry, ISSUE_UNREACHABLE)
    assert issue is not None
    assert issue.translation_placeholders == {"title": "Gateway", "minutes": "15"}
    assert issue.severity is ir.IssueSeverity.WARNING and not issue.is_fixable
    # More failures do not re-create it
    await _poll(hass, setup_entry)
    console.error = None
    await _poll(hass, setup_entry)
    assert _issue(hass, setup_entry, ISSUE_UNREACHABLE) is None


async def test_lte_limit_turned_off(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """An LTE device without data limit gets an issue, named after the device."""
    key = f"{ISSUE_LTE_LIMIT}_{LTE_MAC}"
    assert _issue(hass, setup_entry, key) is None
    console.devices[2] = lte_device(limit=False)
    await _poll(hass, setup_entry)
    issue = _issue(hass, setup_entry, key)
    assert issue is not None
    assert issue.translation_key == ISSUE_LTE_LIMIT
    assert issue.translation_placeholders == {"title": "Gateway", "name": "LTE backup"}
    await _poll(hass, setup_entry)  # still off: nothing new
    console.devices[2] = lte_device(limit=True)
    await _poll(hass, setup_entry)
    assert _issue(hass, setup_entry, key) is None


@pytest.mark.parametrize(
    ("permissions", "admin_error", "reason"),
    [
        ({"network.management": ["readonly"]}, None, "the user lacks the admin role"),
        (
            {"network.management": ["admin"]},
            NetworkAuthError("no"),
            "the console rejected the admin credentials",
        ),
    ],
)
async def test_admin_cannot_write(
    hass: HomeAssistant,
    console: FakeConsole,
    admin_entry: MockConfigEntry,
    permissions: dict[str, list[str]],
    admin_error: Exception | None,
    reason: str,
) -> None:
    """An admin user without rights or with a rejected login gets an issue at setup."""
    console.permissions = permissions
    console.admin_error = admin_error
    admin_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(admin_entry.entry_id)
    await hass.async_block_till_done()
    issue = _issue(hass, admin_entry, ISSUE_ADMIN)
    assert issue is not None
    assert issue.translation_placeholders == {"title": "Gateway", "reason": reason}
    # Rights given: the next setup clears it
    console.permissions = {"network.management": ["admin"]}
    console.admin_error = None
    await hass.config_entries.async_reload(admin_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, admin_entry, ISSUE_ADMIN) is None


async def test_admin_check_not_possible(
    hass: HomeAssistant, console: FakeConsole, admin_entry: MockConfigEntry
) -> None:
    """A console that does not answer the rights check gives no issue either way."""
    console.admin_error = NetworkError("busy")
    admin_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(admin_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, admin_entry, ISSUE_ADMIN) is None


async def test_refused_write_raises_the_admin_issue(
    hass: HomeAssistant, setup_admin_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A write the console refuses for lack of rights creates the issue right away."""
    console.write_error = NetworkPermissionError("403")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "switch.gateway_enforce_backup_uplink"},
            blocking=True,
        )
    issue = _issue(hass, setup_admin_entry, ISSUE_ADMIN)
    assert issue is not None
    assert issue.translation_placeholders["reason"] == "the console refused the write"


async def test_issues_go_with_the_entry(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Removing the console removes its issues, and nobody else's."""
    console.devices[2] = lte_device(limit=False)
    await _poll(hass, setup_entry)
    registry = ir.async_get(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        "other_console",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UNREACHABLE,
    )
    assert len([i for d, i in registry.issues if d == DOMAIN]) == 2
    await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert [i for d, i in registry.issues if d == DOMAIN] == ["other_console"]
