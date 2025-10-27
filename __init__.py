"""The Gemini Live integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv, device_registry as dr



from .core.app import GeminiApp
from .models.devices import GeminiLiveDevice

DOMAIN = "gemini_live_hass_integration"

SATELLITE_PLATFORMS = [
    Platform.SENSOR,
    Platform.SWITCH
]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Directly setup the Gemini Webrtc entity without platform forwarding."""

    config_entry.async_on_unload(config_entry.add_update_listener(update_listener))

    app = GeminiApp(hass, config_entry)
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = {"app": app}

    if not app.started:
        config_entry.async_create_background_task(
            hass,
            app.run(),
            f"Starting Gemini Application...",
        )
        app.started = True

    await hass.config_entries.async_forward_entry_setups(config_entry, SATELLITE_PLATFORMS)
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, SATELLITE_PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data and "app" in data:
            app: GeminiApp = data["app"]
            await app.shutdown()

    return unload_ok
