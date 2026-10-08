"""Shared entity base for TV Management."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_STATE_UPDATED
from .guard import InputGuard


class InputGuardEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, guard: InputGuard, key: str) -> None:
        self.guard = guard
        entry = guard.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="TV Management",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_STATE_UPDATED.format(self.guard.entry.entry_id),
                self.async_write_ha_state,
            )
        )
