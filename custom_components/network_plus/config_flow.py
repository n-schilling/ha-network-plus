"""Config flow for Network Plus."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
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
from .api import (
    NetworkAuthError,
    NetworkCertificateError,
    NetworkClient,
    NetworkError,
    format_fingerprint,
    normalize_fingerprint,
)
from .const import (
    CONF_ADMIN_PASSWORD,
    CONF_ADMIN_USERNAME,
    CONF_AUTO_OFF_MINUTES,
    CONF_CERT_FINGERPRINT,
    CONF_ENFORCED_AT,
    CONF_HISTORY_DAYS,
    CONF_LTE_BILLING_DAY,
    CONF_LTE_LIMIT_PERCENT,
    CONF_PIN_CERTIFICATE,
    CONF_USE_ADMIN,
    DEFAULT_AUTO_OFF_MINUTES,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_LTE_BILLING_DAY,
    DEFAULT_LTE_LIMIT_PERCENT,
    DOMAIN,
    EXPECTED_SPEED_OPTIONS,
    MAX_AUTO_OFF_MINUTES,
    MAX_EXPECTED_SPEED,
    MAX_HISTORY_DAYS,
    MAX_LTE_BILLING_DAY,
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


class CertificateNotTrusted(Exception):
    """The certificate check failed: no trusted chain, or not the pinned certificate."""


def _connection_schema() -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_HOST): str,
            probatio.Required(CONF_USERNAME): str,
            probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
            probatio.Optional(CONF_VERIFY_SSL, default=True): bool,
            probatio.Optional(CONF_PIN_CERTIFICATE, default=False): bool,
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
    """Add a UniFi OS console.

    The certificate is checked against the system's trusted certificates by
    default. Pinning is optional, for a console with a certificate of its own:
    then the step "certificate" shows the fingerprint the console presents,
    and an administrator enters it, compared with the console.
    """

    VERSION = 1

    _data: dict[str, Any]
    _reconfigure: bool = False
    # The step waiting for the certificate, with its data, and the fingerprint
    # the console presented
    _pending: tuple[str, dict[str, Any]]
    _presented: str = ""
    # A fingerprint confirmed in this flow, by host
    _confirmed: tuple[str, str] | None = None

    async def _async_validate(self, data: dict[str, Any]) -> Gateway:
        """Log in and find the gateway."""
        client = create_client(self.hass, data)
        try:
            raw = await client.get_gateway()
        except NetworkAuthError as err:
            raise InvalidAuth from err
        except NetworkCertificateError as err:
            raise CertificateNotTrusted from err
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
        except CertificateNotTrusted:
            errors["base"] = "certificate_not_trusted"
        return None

    def _known_fingerprint(self, host: str) -> str | None:
        """Return the fingerprint confirmed for ``host`` before: in this flow or in the entry."""
        if self._confirmed is not None and self._confirmed[0] == host:
            return self._confirmed[1]
        if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
            entry = (
                self._get_reconfigure_entry()
                if self.source == SOURCE_RECONFIGURE
                else self._get_reauth_entry()
            )
            if entry.data.get(CONF_HOST) == host and entry.data.get(CONF_CERT_FINGERPRINT):
                return str(entry.data[CONF_CERT_FINGERPRINT])
        return None

    async def _async_connect(
        self, step: str, data: dict[str, Any]
    ) -> tuple[ConfigFlowResult | None, dict[str, str]]:
        """Settle the certificate, log in and go on; return the errors for the form of ``step``.

        With pinning asked for, a fingerprint not confirmed for this host
        before leads to the step that asks for it.
        """
        pin = bool(data.get(CONF_PIN_CERTIFICATE))
        data = {
            k: v for k, v in data.items() if k not in (CONF_CERT_FINGERPRINT, CONF_PIN_CERTIFICATE)
        }
        if not pin:
            return await self._async_login(step, data)
        client = create_client(self.hass, data)
        try:
            presented = await client.get_fingerprint()
        except NetworkError:
            return None, {"base": "cannot_connect"}
        finally:
            client.detach()
        if presented != self._known_fingerprint(str(data[CONF_HOST])):
            self._pending = (step, data)
            self._presented = presented
            return await self.async_step_certificate(), {}
        data[CONF_CERT_FINGERPRINT] = presented
        return await self._async_login(step, data)

    async def _async_login(
        self, step: str, data: dict[str, Any]
    ) -> tuple[ConfigFlowResult | None, dict[str, str]]:
        """Log in with ``data``, certificate settled, and go on with ``step``."""
        errors: dict[str, str] = {}
        if not (gateway := await self._async_try(data, errors)):
            return None, errors
        await self.async_set_unique_id(gateway.mac)
        if step == "user":
            self._abort_if_unique_id_configured()
            self._data = {**data, "title": gateway.name}
        else:
            self._abort_if_unique_id_mismatch(reason="wrong_gateway")
        if step in ("reauth", "reauth_confirm"):
            entry = self._get_reauth_entry()
            kept = {k: v for k, v in entry.data.items() if k != CONF_CERT_FINGERPRINT}
            return self.async_update_reload_and_abort(entry, data={**kept, **data}), {}
        if step == "reconfigure":
            entry = self._get_reconfigure_entry()
            # What the entry remembers while the backup is enforced stays
            kept = {
                k: v
                for k, v in entry.data.items()
                if k not in data and k not in (*ADMIN_KEYS, CONF_CERT_FINGERPRINT, CONF_VERIFY_SSL)
            }
            self._data = {**kept, **data, "title": entry.title}
        if data.get(CONF_USE_ADMIN):
            return await self.async_step_admin(), {}
        return self._async_finish(), {}

    async def async_step_certificate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the fingerprint of the console's certificate, to compare and pin it."""
        step, data = self._pending
        errors: dict[str, str] = {}
        if user_input is not None:
            entered = normalize_fingerprint(str(user_input[CONF_CERT_FINGERPRINT]))
            if entered != self._presented:
                errors["base"] = "fingerprint_mismatch"
            else:
                self._confirmed = (str(data[CONF_HOST]), entered)
                result, failed = await self._async_login(
                    step, {**data, CONF_CERT_FINGERPRINT: entered}
                )
                return result or self._async_show_step(step, failed, data)
        return self.async_show_form(
            step_id="certificate",
            data_schema=probatio.Schema({probatio.Required(CONF_CERT_FINGERPRINT): str}),
            description_placeholders={
                "host": str(data[CONF_HOST]),
                "fingerprint": format_fingerprint(self._presented),
            },
            errors=errors,
        )

    def _async_show_step(
        self, step: str, errors: dict[str, str], values: dict[str, Any]
    ) -> ConfigFlowResult:
        """Show the form of ``step`` again, with ``errors``; passwords are never filled in."""
        if step in ("reauth", "reauth_confirm"):
            entry = self._get_reauth_entry()
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=probatio.Schema({probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
                description_placeholders={
                    "host": entry.data[CONF_HOST],
                    "username": entry.data[CONF_USERNAME],
                },
                errors=errors,
            )
        suggested = {
            k: v
            for k, v in values.items()
            if k
            in (CONF_HOST, CONF_USERNAME, CONF_VERIFY_SSL, CONF_PIN_CERTIFICATE, CONF_USE_ADMIN)
        }
        return self.async_show_form(
            step_id=step,
            data_schema=self.add_suggested_values_to_schema(_connection_schema(), suggested),
            errors=errors,
        )

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
        if user_input is None:
            return self._async_show_step("user", {}, {})
        data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
        result, errors = await self._async_connect("user", data)
        return result or self._async_show_step("user", errors, data)

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
        """Change the address, the user or the admin opt-in."""
        entry = self._get_reconfigure_entry()
        self._reconfigure = True
        if user_input is None:
            suggested = {k: v for k, v in entry.data.items() if k in (CONF_HOST, CONF_USERNAME)}
            suggested[CONF_VERIFY_SSL] = bool(entry.data.get(CONF_VERIFY_SSL, True))
            suggested[CONF_PIN_CERTIFICATE] = bool(entry.data.get(CONF_CERT_FINGERPRINT))
            suggested[CONF_USE_ADMIN] = bool(entry.data.get(CONF_USE_ADMIN))
            return self._async_show_step("reconfigure", {}, suggested)
        data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
        if not data.get(CONF_USE_ADMIN) and entry.data.get(CONF_ENFORCED_AT):
            # Without the admin user nothing could switch the preferred uplink on again
            return self._async_show_step("reconfigure", {"base": "backup_enforced"}, data)
        result, errors = await self._async_connect("reconfigure", data)
        return result or self._async_show_step("reconfigure", errors, data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The console rejected the login of the read-only user, or its pinned certificate changed.

        With a pin, a changed certificate is asked for first; the password
        only if the login fails after that.
        """
        pinned = entry_data.get(CONF_CERT_FINGERPRINT)
        if pinned:
            data = {k: v for k, v in entry_data.items() if k != CONF_CERT_FINGERPRINT}
            client = create_client(self.hass, data)
            try:
                presented: str | None = await client.get_fingerprint()
            except NetworkError:
                presented = None
            finally:
                client.detach()
            if presented is not None and presented != pinned:
                self._pending = ("reauth", data)
                self._presented = presented
                return await self.async_step_certificate()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the current password of the read-only user."""
        entry = self._get_reauth_entry()
        if user_input is None:
            return self._async_show_step("reauth_confirm", {}, {})
        data = {
            **entry.data,
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            CONF_PIN_CERTIFICATE: bool(entry.data.get(CONF_CERT_FINGERPRINT)),
        }
        result, errors = await self._async_connect("reauth_confirm", data)
        return result or self._async_show_step("reauth_confirm", errors, data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlowWithReload):
    """The switch's limits, and the link speed each WAN port should negotiate."""

    def _speed_options(self) -> list[str]:
        """Return the option keys of the uplinks with a port the gateway reports."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return []
        uplinks = entry.runtime_data.coordinator.data.uplinks
        return [
            option
            for key, option in EXPECTED_SPEED_OPTIONS.items()
            if (uplink := uplinks.get(key)) is not None and uplink.port
        ]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            data: dict[str, Any] = {
                CONF_AUTO_OFF_MINUTES: int(user_input[CONF_AUTO_OFF_MINUTES]),
                CONF_LTE_LIMIT_PERCENT: int(user_input[CONF_LTE_LIMIT_PERCENT]),
                CONF_LTE_BILLING_DAY: int(user_input[CONF_LTE_BILLING_DAY]),
                CONF_HISTORY_DAYS: int(user_input[CONF_HISTORY_DAYS]),
            }
            shown = self._speed_options()
            for option in EXPECTED_SPEED_OPTIONS.values():
                # A field the form did not show (the console was unreachable)
                # keeps its value; 0 or empty: no expectation, the sensor goes
                value = (
                    user_input.get(option)
                    if option in shown
                    else self.config_entry.options.get(option)
                )
                if value and int(value) > 0:
                    data[option] = int(value)
            return self.async_create_entry(data=data)
        options = self.config_entry.options
        speed_fields = {
            probatio.Optional(
                option, description={"suggested_value": options.get(option)}
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_EXPECTED_SPEED,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="Mbit/s",
                )
            )
            for option in self._speed_options()
        }
        schema = probatio.Schema(
            {
                **speed_fields,
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
                probatio.Required(
                    CONF_LTE_BILLING_DAY,
                    default=options.get(CONF_LTE_BILLING_DAY, DEFAULT_LTE_BILLING_DAY),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=DEFAULT_LTE_BILLING_DAY,
                        max=MAX_LTE_BILLING_DAY,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                probatio.Required(
                    CONF_HISTORY_DAYS,
                    default=options.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=MAX_HISTORY_DAYS,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="d",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
