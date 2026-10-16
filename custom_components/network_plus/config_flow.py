"""Config flow for Network Plus."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import create_client
from .api import NetworkAuthError, NetworkClient, NetworkError
from .const import (
    CONF_ADMIN_PASSWORD,
    CONF_ADMIN_USERNAME,
    CONF_AUTO_OFF_MINUTES,
    CONF_LTE_LIMIT_PERCENT,
    CONF_USE_ADMIN,
    DEFAULT_AUTO_OFF_MINUTES,
    DEFAULT_LTE_LIMIT_PERCENT,
    DOMAIN,
    MAX_AUTO_OFF_MINUTES,
    MAX_LTE_LIMIT_PERCENT,
    MIN_AUTO_OFF_MINUTES,
    MIN_LTE_LIMIT_PERCENT,
)
from .model import Gateway, parse_state

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
ADMIN_KEYS = (CONF_USE_ADMIN, CONF_ADMIN_USERNAME, CONF_ADMIN_PASSWORD)


class CannotConnect(Exception):
    """The console is not reachable."""


class InvalidAuth(Exception):
    """The console rejected the user name or password."""


class NoGateway(Exception):
    """The console answered, but has no gateway in its UniFi Network site."""


class NotAdmin(Exception):
    """The user may not change the Network configuration."""


def _connection_schema() -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_HOST): str,
            probatio.Required(CONF_USERNAME): str,
            probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            probatio.Optional(CONF_VERIFY_SSL, default=True): bool,
            probatio.Optional(CONF_USE_ADMIN, default=False): bool,
        }
    )


def _admin_schema() -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_ADMIN_USERNAME): str,
            probatio.Required(CONF_ADMIN_PASSWORD): PASSWORD_SELECTOR,
        }
    )


class NetworkPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a UniFi OS console."""

    VERSION = 1

    _data: dict[str, Any]
    _reconfigure: bool = False

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

    async def _async_validate_admin(self, data: dict[str, Any]) -> None:
        """Log in with the admin credentials and check that they may write."""
        client: NetworkClient = create_client(
            self.hass,
            {
                **data,
                CONF_USERNAME: data[CONF_ADMIN_USERNAME],
                CONF_PASSWORD: data[CONF_ADMIN_PASSWORD],
            },
        )
        try:
            allowed = await client.can_manage_network()
        except NetworkAuthError as err:
            raise InvalidAuth from err
        except NetworkError as err:
            raise CannotConnect from err
        finally:
            client.detach()
        if not allowed:
            raise NotAdmin

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the console's address and a local user; admin features are an opt-in."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            if gateway := await self._async_try(data, errors):
                await self.async_set_unique_id(gateway.mac)
                self._abort_if_unique_id_configured()
                self._data = {**data, "title": gateway.name}
                if data.get(CONF_USE_ADMIN):
                    return await self.async_step_admin()
                return self._async_finish()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(),
                {k: v for k, v in (user_input or {}).items() if k != CONF_PASSWORD},
            ),
            errors=errors,
        )

    async def async_step_admin(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the admin credentials, kept apart from the read-only ones."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**self._data, **user_input}
            try:
                await self._async_validate_admin(data)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "admin_invalid_auth"
            except NotAdmin:
                errors["base"] = "not_admin"
            else:
                self._data = data
                return self._async_finish()
        return self.async_show_form(
            step_id="admin",
            data_schema=self.add_suggested_values_to_schema(
                _admin_schema(),
                {k: v for k, v in (user_input or {}).items() if k != CONF_ADMIN_PASSWORD},
            ),
            errors=errors,
        )

    @callback
    def _async_finish(self) -> ConfigFlowResult:
        """Create the entry, or update the one being reconfigured."""
        title = str(self._data.pop("title"))
        data = dict(self._data)
        if not data.get(CONF_USE_ADMIN):
            data = {k: v for k, v in data.items() if k not in ADMIN_KEYS}
            data[CONF_USE_ADMIN] = False
        if self._reconfigure:
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=data)
        return self.async_create_entry(title=title, data=data)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address, the user, the certificate check or the admin opt-in."""
        entry = self._get_reconfigure_entry()
        self._reconfigure = True
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            if gateway := await self._async_try(data, errors):
                await self.async_set_unique_id(gateway.mac)
                self._abort_if_unique_id_mismatch(reason="wrong_gateway")
                # What the entry remembers while the backup is enforced stays
                kept = {
                    k: v for k, v in entry.data.items() if k not in data and k not in ADMIN_KEYS
                }
                self._data = {**kept, **data, "title": entry.title}
                if data.get(CONF_USE_ADMIN):
                    return await self.async_step_admin()
                return self._async_finish()
        suggested = {k: v for k, v in entry.data.items() if k not in (CONF_PASSWORD, *ADMIN_KEYS)}
        suggested[CONF_USE_ADMIN] = bool(entry.data.get(CONF_USE_ADMIN))
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_connection_schema(), suggested),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The console rejected the login of the read-only user."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the current password of the read-only user."""
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlowWithReload):
    """How long the backup stays enforced, and from which LTE usage it is refused."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_AUTO_OFF_MINUTES: int(user_input[CONF_AUTO_OFF_MINUTES]),
                    CONF_LTE_LIMIT_PERCENT: int(user_input[CONF_LTE_LIMIT_PERCENT]),
                }
            )
        options = self.config_entry.options
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_AUTO_OFF_MINUTES,
                    default=options.get(CONF_AUTO_OFF_MINUTES, DEFAULT_AUTO_OFF_MINUTES),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_AUTO_OFF_MINUTES,
                        max=MAX_AUTO_OFF_MINUTES,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                probatio.Required(
                    CONF_LTE_LIMIT_PERCENT,
                    default=options.get(CONF_LTE_LIMIT_PERCENT, DEFAULT_LTE_LIMIT_PERCENT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_LTE_LIMIT_PERCENT,
                        max=MAX_LTE_LIMIT_PERCENT,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="%",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
