"""Config flow for the RCA integration."""
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import RcaBrowserApi, RcaBrowserApiError
from .const import (
    DOMAIN,
    CONF_PLATE,
    CONF_SEARCH_TYPE,
    CONF_BROWSER_SERVICE_URL,
    CONF_UPDATE_INTERVAL,
    CONF_WARNING_DAYS,
    DEFAULT_BROWSER_SERVICE_URL,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WARNING_DAYS,
    MIN_UPDATE_INTERVAL,
    MAX_UPDATE_INTERVAL,
    SEARCH_TYPE_PLATE,
    SEARCH_TYPE_VIN,
)

_LOGGER = logging.getLogger(__name__)


class RcaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for RCA Insurance Check."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            plate = user_input[CONF_PLATE].strip().upper()
            search_type = user_input.get(CONF_SEARCH_TYPE, SEARCH_TYPE_PLATE)
            browser_url = user_input.get(
                CONF_BROWSER_SERVICE_URL, DEFAULT_BROWSER_SERVICE_URL
            )

            # Validate browser service is reachable
            api = RcaBrowserApi(browser_url)
            healthy = await api.health_check()
            if not healthy:
                errors["base"] = "cannot_connect"
            else:
                # Set unique ID based on plate number
                await self.async_set_unique_id(plate)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"RCA ({plate})",
                    data={
                        CONF_PLATE: plate,
                        CONF_SEARCH_TYPE: search_type,
                        CONF_BROWSER_SERVICE_URL: browser_url,
                        CONF_UPDATE_INTERVAL: user_input.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                        CONF_WARNING_DAYS: user_input.get(
                            CONF_WARNING_DAYS, DEFAULT_WARNING_DAYS
                        ),
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_PLATE): str,
                vol.Optional(
                    CONF_SEARCH_TYPE, default=SEARCH_TYPE_PLATE
                ): vol.In(
                    {
                        SEARCH_TYPE_PLATE: "Registration Number",
                        SEARCH_TYPE_VIN: "VIN / Chassis Number",
                    }
                ),
                vol.Optional(
                    CONF_BROWSER_SERVICE_URL,
                    default=DEFAULT_BROWSER_SERVICE_URL,
                ): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=DEFAULT_UPDATE_INTERVAL,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL),
                ),
                vol.Optional(
                    CONF_WARNING_DAYS,
                    default=DEFAULT_WARNING_DAYS,
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return RcaOptionsFlowHandler()


class RcaOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle RCA options (no __init__ — HA 2026.3+ compatible)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL,
            self.config_entry.data.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            ),
        )
        current_browser_url = self.config_entry.options.get(
            CONF_BROWSER_SERVICE_URL,
            self.config_entry.data.get(
                CONF_BROWSER_SERVICE_URL, DEFAULT_BROWSER_SERVICE_URL
            ),
        )
        current_warning_days = self.config_entry.options.get(
            CONF_WARNING_DAYS,
            self.config_entry.data.get(
                CONF_WARNING_DAYS, DEFAULT_WARNING_DAYS
            ),
        )

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BROWSER_SERVICE_URL,
                    default=current_browser_url,
                ): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current_interval,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL),
                ),
                vol.Optional(
                    CONF_WARNING_DAYS,
                    default=current_warning_days,
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
