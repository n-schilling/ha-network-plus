"""Setting up a console: the certificate of entries from before it was always checked."""

from __future__ import annotations

import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

from custom_components.network_plus.api import NetworkCertificateError, NetworkError
from custom_components.network_plus.const import CONF_CERT_FINGERPRINT

from .conftest import FINGERPRINT, FakeConsole


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_unchecked_certificate_is_pinned(
    hass: HomeAssistant,
    console: FakeConsole,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An entry that took any certificate pins the one the console presents now."""
    console.presented = FINGERPRINT
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_VERIFY_SSL: False}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert CONF_VERIFY_SSL not in config_entry.data
    assert config_entry.data[CONF_CERT_FINGERPRINT] == FINGERPRINT
    assert console.pinned[-1] == FINGERPRINT
    assert ":".join(["AB"] * 32) in caplog.text


async def test_unchecked_but_trusted_certificate_is_not_pinned(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """A certificate the system trusts is checked as such; renewing it needs no confirmation."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_VERIFY_SSL: False}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert CONF_VERIFY_SSL not in config_entry.data
    assert CONF_CERT_FINGERPRINT not in config_entry.data


async def test_checked_certificate_stays_checked(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """An entry that checked the certificate drops the old switch and keeps checking."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_VERIFY_SSL: True}
    )
    console.certificate_error = NetworkError("must not be asked")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert CONF_VERIFY_SSL not in config_entry.data
    assert CONF_CERT_FINGERPRINT not in config_entry.data


async def test_pinning_waits_for_the_console(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """Without the console's certificate the setup is tried again later."""
    console.certificate_error = NetworkError("down")
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_VERIFY_SSL: False}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert config_entry.data[CONF_VERIFY_SSL] is False


async def test_changed_certificate_asks_an_administrator(
    hass: HomeAssistant,
    console: FakeConsole,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another certificate than the pinned one stops the entry and asks; it is never taken."""
    console.error = NetworkCertificateError("another certificate", "cd" * 32)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert ":".join(["CD"] * 32) in caplog.text
