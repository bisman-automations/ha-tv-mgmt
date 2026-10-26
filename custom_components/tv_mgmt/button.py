"""Buttons: turn off the TV lights, and light sync zones' resync and save-title-colour."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TVMgmtConfigEntry
from .entity import TVMgmtEntity
from .light_sync.entities import FACTORIES

# Light sync zones queue their own light calls.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([TurnOffTVLightsButton(entry.runtime_data)])
    if (light_sync := entry.runtime_data.light_sync) is not None:
        light_sync.register_platform(FACTORIES["button"], async_add_entities)


class TurnOffTVLightsButton(TVMgmtEntity, ButtonEntity):
    """Turns the TV's lights off now. Light sync leaves them off until the TV turns on or off."""

    def __init__(self, manager) -> None:
        super().__init__(manager, "turn_off_tv_lights")

    @property
    def available(self) -> bool:
        return bool(self.manager.tv_lights)  # no TV lights picked: nothing to turn off

    async def async_press(self) -> None:
        await self._ensure_allowed()
        await self.manager.async_turn_off_tv_lights()
