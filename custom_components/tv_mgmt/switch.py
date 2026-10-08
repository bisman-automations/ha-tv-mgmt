"""Switch to turn the input lock on and off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import TVMgmtConfigEntry
from .entity import InputGuardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([InputGuardSwitch(entry.runtime_data)])


class InputGuardSwitch(InputGuardEntity, SwitchEntity, RestoreEntity):
    _attr_icon = "mdi:television-shimmer"

    def __init__(self, guard) -> None:
        super().__init__(guard, "lock")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            # Don't call async_set_enabled: the guard hasn't started yet.
            self.guard.enabled = False

    @property
    def is_on(self) -> bool:
        return self.guard.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.guard.attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.guard.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.guard.async_set_enabled(False)
