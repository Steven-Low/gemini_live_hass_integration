from __future__ import annotations

from typing import Any
import logging
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback, HomeAssistant
from homeassistant.const import CONF_API_KEY, CONF_EXTERNAL_URL

from .config.const import DOMAIN, SIGNALING_SERVER_URL, CONF_WAKE_URL

_LOGGER = logging.getLogger(__name__)

# ---------- FIRST INSTALL FORM ----------
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_EXTERNAL_URL, default=SIGNALING_SERVER_URL): str,
        vol.Optional(CONF_WAKE_URL, default=CONF_WAKE_URL): str,
    }
)

class GeminiLiveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow for Gemini Live."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        # Store initial data (STATIC)
        return self.async_create_entry(
            title="Gemini Live",
            data=user_input,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return GeminiLiveOptionsFlowHandler()


# ---------- OPTIONS FLOW (USED AFTER INSTALL) ----------
class GeminiLiveOptionsFlowHandler(OptionsFlow):
    """Handle options for Gemini Live."""

    @property
    def config_entry(self) -> ConfigEntry:
        """Return the linked config entry."""
        return self.hass.config_entries.async_get_entry(self.handler)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options form."""
        if user_input is not None:
            # Saved to entry.options (NOT data)
            return self.async_create_entry(title="Gemini Live", data=user_input)

        # Load current values
        # options override data, fallback to defaults
        current_api_key = self.config_entry.options.get(
            CONF_API_KEY,
            self.config_entry.data.get(CONF_API_KEY, "")
        )
        current_signaling = self.config_entry.options.get(
            CONF_EXTERNAL_URL,
            self.config_entry.data.get(CONF_EXTERNAL_URL, SIGNALING_SERVER_URL)
        )
        current_wake = self.config_entry.options.get(
            CONF_WAKE_URL,
            self.config_entry.data.get(CONF_WAKE_URL, CONF_WAKE_URL)
        )

        options_schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY, default=current_api_key): str,
                vol.Optional(CONF_EXTERNAL_URL, default=current_signaling): str,
                vol.Optional(CONF_WAKE_URL, default=current_wake): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)
