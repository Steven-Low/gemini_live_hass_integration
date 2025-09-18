"""Sensors for Gemini Live."""

from __future__ import annotations
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    """Set up Gemini Live entities dynamically."""

    app: GeminiApp = hass.data[DOMAIN][config_entry.entry_id]["app"]

    async def _handle_new_session(device: GeminiLiveDevice):
        entities = [
            GeminiWakeBinarySensor(device),
            GeminiActivitySensor(device),
        ]
        async_add_entities(entities)

    app.register_entity_adder(_handle_new_session)


# -------------------
# Wakeword Binary Sensor
# -------------------

class GeminiWakeBinarySensor(GeminiLiveEntity, BinarySensorEntity):
    """Binary sensor for wake state."""

    entity_description = BinarySensorEntityDescription(
        key="wake",
        translation_key="wake",
    )

    def __init__(self, device: GeminiLiveDevice):
        super().__init__(device)
        self._device = device
        self._attr_name = f"{device.processor_id} Wake State"
        self._attr_unique_id = f"{device.processor_id}_wake"
        self._attr_is_on = device.is_wake

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._device.set_wake_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self._attr_is_on = self._device.is_wake
        self.async_write_ha_state()


# -------------------
# Combined Activity Sensor
# -------------------

class GeminiActivitySensor(GeminiLiveEntity, SensorEntity):
    """Sensor for overall activity (idle / listening / playing)."""

    entity_description = SensorEntityDescription(
        key="activity",
        name="Activity",
    )

    def __init__(self, device: GeminiLiveDevice):
        super().__init__(device)
        self._device = device
        self._attr_name = f"{device.processor_id} Activity"
        self._attr_unique_id = f"{device.processor_id}_activity"
        self._state: str = "idle"

    @property
    def native_value(self) -> str | None:
        return self._device.activity

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._device.set_activity_listener(self._handle_update)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
