"""Tests for the config flow."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.network_plus.api import NetworkAuthError, NetworkError
from custom_components.network_plus.const import DOMAIN

from .conftest import ENTRY_DATA, HOST, MAC, FakeConsole, gateway


async def _start(hass: HomeAssistant, data: dict) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def test_user_creates_entry(hass: HomeAssistant, console: FakeConsole) -> None:
    """A reachable console is added under the gateway's name, keyed by its MAC."""
    result = await _start(hass, {**ENTRY_DATA, CONF_HOST: f" {HOST} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Gateway"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == MAC
    assert console.created_with[0] == (HOST, "homeassistant", "secret")


async def test_verify_ssl_defaults_to_on(hass: HomeAssistant, console: FakeConsole) -> None:
    """Without the checkbox the certificate is checked."""
    data = {k: v for k, v in ENTRY_DATA.items() if k != CONF_VERIFY_SSL}
    result = await _start(hass, data)
    assert result["data"][CONF_VERIFY_SSL] is True


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (NetworkError("down"), "cannot_connect"),
        (NetworkAuthError("no"), "invalid_auth"),
    ],
)
async def test_user_errors(
    hass: HomeAssistant, console: FakeConsole, error: Exception, reason: str
) -> None:
    """Errors keep the form open; the password is not suggested again."""
    console.error = error
    result = await _start(hass, ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
    console.error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_no_gateway(hass: HomeAssistant, console: FakeConsole) -> None:
    """A console without gateway cannot be added."""
    console.devices = [{"type": "usw", "mac": "02:00:00:00:00:02"}]
    result = await _start(hass, ENTRY_DATA)
    assert result["errors"] == {"base": "no_gateway"}
    raw = gateway()
    del raw["mac"]
    console.devices = [raw]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["errors"] == {"base": "no_gateway"}


async def test_user_already_configured(
    hass: HomeAssistant, console: FakeConsole, config_entry: MockConfigEntry
) -> None:
    """A gateway can be added once."""
    config_entry.add_to_hass(hass)
    result = await _start(hass, ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """The address and user change; the gateway must stay the same."""
    result = await setup_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    console.error = NetworkError("down")
    new = {**ENTRY_DATA, CONF_HOST: "192.0.2.2", CONF_USERNAME: "other"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["errors"] == {"base": "cannot_connect"}
    console.error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.data[CONF_HOST] == "192.0.2.2"
    assert setup_entry.data[CONF_USERNAME] == "other"


async def test_reconfigure_wrong_gateway(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """Another console's gateway is refused."""
    result = await setup_entry.start_reconfigure_flow(hass)
    console.set_gateway(gateway(mac="02:00:00:00:00:99"))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_gateway"


async def test_reauth(
    hass: HomeAssistant, setup_entry: MockConfigEntry, console: FakeConsole
) -> None:
    """A new password is checked and stored."""
    result = await setup_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    placeholders = result["description_placeholders"]
    assert (placeholders["host"], placeholders["username"]) == (HOST, "homeassistant")
    console.error = NetworkAuthError("no")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    console.error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_entry.data[CONF_PASSWORD] == "new"
    assert console.created_with[-1][2] == "new"
