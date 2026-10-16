"""Config flow for Network Plus."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from . import create_client
from .api import NetworkAuthError, NetworkError
from .const import DOMAIN
from .model import Gateway, parse_state

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class CannotConnect(Exception):
    """The console is not reachable."""


class InvalidAuth(Exception):
    """The console rejected the user name or password."""


class NoGateway(Exception):
    """The console answered, but has no gateway in its UniFi Network site."""


def _connection_schema() -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_HOST): str,
            probatio.Required(CONF_USERNAME): str,
            probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            probatio.Optional(CONF_VERIFY_SSL, default=True): bool,
        }
    )


class NetworkPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a UniFi OS console."""

    VERSION = 1

    async def _async_validate(self, data: dict[str, Any]) -> Gateway:
        """Log in and find the gateway."""
        client = create_client(self.hass, data)
        try:
            raw = await client.get_gateway()
        except NetworkAuthError as err:
            raise InvalidAuth from err
        except NetworkError as err:
            raise CannotConnect from err
        finally:
            # The session served one check; the entry gets its own
            client.detach()
        if raw is None or not raw.get("mac"):
            raise NoGateway
        return parse_state(raw, []).gateway

    async def _async_try(self, data: dict[str, Any], errors: dict[str, str]) -> Gateway | None:
        try:
            return await self._async_validate(data)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except NoGateway:
            errors["base"] = "no_gateway"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the console's address and a local user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            if gateway := await self._async_try(data, errors):
                await self.async_set_unique_id(gateway.mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=gateway.name, data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(),
                {k: v for k, v in (user_input or {}).items() if k != CONF_PASSWORD},
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address, the user or the certificate check."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            if gateway := await self._async_try(data, errors):
                await self.async_set_unique_id(gateway.mac)
                self._abort_if_unique_id_mismatch(reason="wrong_gateway")
                return self.async_update_reload_and_abort(entry, data_updates=data)
        suggested = {k: v for k, v in entry.data.items() if k != CONF_PASSWORD}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_connection_schema(), suggested),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The console rejected the login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the current password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            if gateway := await self._async_try(data, errors):
                await self.async_set_unique_id(gateway.mac)
                self._abort_if_unique_id_mismatch(reason="wrong_gateway")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={
                "host": entry.data[CONF_HOST],
                "username": entry.data[CONF_USERNAME],
            },
            errors=errors,
        )
