
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback, HomeAssistant
from homeassistant.const import CONF_API_KEY, CONF_EXTERNAL_URL
from homeassistant.helpers import selector, entity_registry

from .config.const import DOMAIN, SIGNALING_SERVER_URL
from websockets.asyncio.client import connect
import voluptuous as vol
from typing import Any
import logging
import json



_LOGGER = logging.getLogger(__name__)


class GeminiLiveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gemini Assist."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                pass
            except Exception:
                errors["base"] = "Invalid Authentication"
                _LOGGER.exception("Authentication Error")
            else:
                return self.async_create_entry(title="Gemini Live", data=user_input, options={})


        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_EXTERNAL_URL, default=SIGNALING_SERVER_URL): str,
                vol.Required(CONF_API_KEY): str
            }),
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return GeminiLiveOptionsFlowHandler()


class GeminiLiveOptionsFlowHandler(OptionsFlow):

    @property
    def config_entry(self):
        return self.hass.config_entries.async_get_entry(self.handler)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="Gemini Live", data=user_input)

        dynamic_schema = vol.Schema({
            vol.Optional(
                CONF_EXTERNAL_URL,
                description={"suggested_value": SIGNALING_SERVER_URL}
            ): str,
            vol.Optional(
                CONF_API_KEY,
                description={"suggested_value": "AIzaSyxxxxxxxxxxxx"}
            ): str
        })

        return self.async_show_form(step_id="init", data_schema=dynamic_schema)