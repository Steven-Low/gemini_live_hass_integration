from __future__ import annotations
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import callback, HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .config.const import DOMAIN
from .models.devices import GeminiLiveDevice
from .models.entity import GeminiLiveEntity  # your base entity

if TYPE_CHECKING:
    from .core.app import GeminiApp

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Gemini Live switches dynamically."""

    app: GeminiApp = hass.data[DOMAIN][config_entry.entry_id]["app"]

    async def _handle_new_session(device: GeminiLiveDevice):
        entities = [
            GeminiWakeSwitch(device),
        ]
        async_add_entities(entities)

    app.register_entity_adder(_handle_new_session)


class GeminiWakeSwitch(GeminiLiveEntity, SwitchEntity):
    """Switch to enable/disable wake word detection."""

    entity_description = SwitchEntityDescription(
        key="wake_word_enabled",
        name="Wake Word Enabled",
    )

    def __init__(self, device: GeminiLiveDevice) -> None:
        super().__init__(device)
        self._device = device
        self._attr_name = f"{device.processor_id} Wake Word Enabled"
        self._attr_unique_id = f"{device.processor_id}_wake_switch"
        self._attr_is_on: bool = device.wake_word_enabled

    @property
    def is_on(self) -> bool:
        return self._device.wake_word_enabled

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._device.set_wake_word_enabled_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        self._device.set_wake_word_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._device.set_wake_word_enabled(False)
