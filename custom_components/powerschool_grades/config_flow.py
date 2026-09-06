"""Config flow for PowerSchool Grades & Attendance."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client

from .api import PowerSchoolAuthError, PowerSchoolClient, PowerSchoolError
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MIN_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        # e.g. "powerschool.eastnoble.net" -- host only, no https:// prefix.
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class PowerSchoolConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Validates credentials against the real portal before saving them."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            session = aiohttp_client.async_create_clientsession(self.hass)
            client = PowerSchoolClient(
                session=session,
                host=host,
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await client.async_login()
                students = await client.async_get_students()
            except PowerSchoolAuthError:
                errors["base"] = "invalid_auth"
            except PowerSchoolError:
                _LOGGER.exception("Could not reach/parse the PowerSchool portal at %s", host)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating PowerSchool login")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{host}:{user_input[CONF_USERNAME]}")
                self._abort_if_unique_id_configured()
                student_count = len(students) or 1
                title = f"PowerSchool ({host}) - {student_count} student(s)"
                return self.async_create_entry(title=title, data={**user_input, CONF_HOST: host})
            finally:
                await session.close()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> PowerSchoolOptionsFlow:
        return PowerSchoolOptionsFlow()


class PowerSchoolOptionsFlow(config_entries.OptionsFlow):
    """Lets you change the poll interval without re-entering credentials.

    Deliberately no __init__ here. Older HA versions expected you to store
    config_entry yourself (`self.config_entry = config_entry`); current core
    sets `self.config_entry` for you after instantiation and made it a
    read-only property, so doing that assignment now raises instead of just
    warning -- it took down the whole flow with an unhandled 500 the first
    time this got exercised (i.e. any time you open "Configure" on the
    integration, not on initial setup).
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_MINUTES)),
                }
            ),
        )
