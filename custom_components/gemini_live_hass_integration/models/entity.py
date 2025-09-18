"""Device entities."""

from __future__ import annotations

from homeassistant.helpers import entity
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from ..config.const import DOMAIN
from .devices import GeminiLiveDevice


class GeminiLiveEntity(entity.Entity):
    """Processor entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: GeminiLiveDevice) -> None:
        """Initialize entity."""
        self._device = device
        self._attr_unique_id = f"{device.processor_id}-{self.entity_description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.processor_id)},
            # entry_type=DeviceEntryType.SERVICE,
        )