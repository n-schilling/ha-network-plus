"""Setting up a console: the certificate settings of the entry are used as they are."""

from __future__ import annotations

import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

from custom_components.network_plus.api import NetworkCertificateError
from custom_components.network_plus.const import CONF_CERT_FINGERPRINT

from .conftest import FINGERPRINT, FakeConsole


async def test_unchecked_certificate_stays_unpinned(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """An entry with the check off keeps it off; nothing is pinned behind its back."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_VERIFY_SSL: False}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_VERIFY_SSL] is False
    assert CONF_CERT_FINGERPRINT not in config_entry.data
    assert console.pinned == [None]


async def test_pinned_certificate_is_used(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """A pinned fingerprint goes to the client."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_CERT_FINGERPRINT: FINGERPRINT}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert console.pinned == [FINGERPRINT]


async def test_changed_pinned_certificate_asks_an_administrator(
    hass: HomeAssistant,
    console: FakeConsole,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another certificate than the pinned one stops the entry and asks; it is never taken."""
    console.error = NetworkCertificateError("another certificate", "cd" * 32)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert ":".join(["CD"] * 32) in caplog.text


async def test_untrusted_certificate_without_pin_retries(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """A certificate the check does not trust (not pinned) is a connection problem."""
    console.error = NetworkCertificateError("not trusted")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()
