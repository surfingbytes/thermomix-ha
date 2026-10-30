"""Config flow for the Thermomix integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
import logging
from typing import Any
from uuid import uuid4

from cookidoo_api import (
    CookidooAuthData,
    CookidooAuthException,
    CookidooParseException,
    CookidooRequestException,
    get_country_options,
    get_localization_options,
)
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_TOKEN,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    CountrySelector,
    CountrySelectorConfig,
    LanguageSelector,
    LanguageSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_MOBILE_APP_ID, DOMAIN
from .helpers import cookidoo_from_config_data

_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = {
    vol.Required(CONF_EMAIL): TextSelector(
        TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="email")
    ),
    vol.Required(CONF_PASSWORD): TextSelector(
        TextSelectorConfig(
            type=TextSelectorType.PASSWORD, autocomplete="current-password"
        )
    ),
}


class ThermomixConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Thermomix."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._user_input: dict[str, Any] = {}
        self._token: dict[str, Any] = {}
        self._user_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Cookidoo account and country."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate(user_input)
            if not errors:
                await self.async_set_unique_id(self._user_id)
                self._abort_if_unique_id_configured()
                self._user_input = user_input
                return await self.async_step_language()

        countries = [country.upper() for country in await get_country_options()]
        schema = vol.Schema(
            {
                **AUTH_SCHEMA,
                vol.Required(CONF_COUNTRY): CountrySelector(
                    CountrySelectorConfig(countries=countries)
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_language(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Cookidoo content language and create the entry."""
        if user_input is not None:
            return self.async_create_entry(
                title="Thermomix",
                data={
                    **self._user_input,
                    **user_input,
                    CONF_TOKEN: self._token,
                    CONF_MOBILE_APP_ID: str(uuid4()),
                },
            )

        options = await get_localization_options(
            country=self._user_input[CONF_COUNTRY].lower()
        )
        return self.async_show_form(
            step_id="language",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LANGUAGE): LanguageSelector(
                        LanguageSelectorConfig(
                            languages=[option.language for option in options],
                            native_name=True,
                        )
                    )
                }
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication after the credentials stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await self._async_validate({**reauth_entry.data, **user_input})
            if not errors:
                await self.async_set_unique_id(self._user_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={**user_input, CONF_TOKEN: self._token},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(AUTH_SCHEMA),
                {CONF_EMAIL: reauth_entry.data[CONF_EMAIL]},
            ),
            errors=errors,
        )

    @callback
    def _save_token(self, auth_data: CookidooAuthData) -> None:
        """Keep the tokens the login hands us, so the entry starts logged in."""
        self._token = asdict(auth_data)

    async def _async_validate(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Try to log in; return form errors."""
        data = dict(user_input)
        if CONF_LANGUAGE not in data:
            options = await get_localization_options(country=data[CONF_COUNTRY].lower())
            data[CONF_LANGUAGE] = options[0].language

        # Only this attempt's tokens may reach the entry.
        self._token = {}
        cookidoo = await cookidoo_from_config_data(
            self.hass, data, on_auth_data_update=self._save_token
        )
        try:
            await cookidoo.login()
            self._user_id = (await cookidoo.get_user_info()).id
        except CookidooRequestException:
            return {"base": "cannot_connect"}
        except CookidooAuthException:
            return {"base": "invalid_auth"}
        except CookidooParseException:
            return {"base": "unknown"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected exception during Cookidoo login")
            return {"base": "unknown"}
        return {}
