"""Selects: the mode (enforced / monitor only / paused) and light sync zone settings."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TVMgmtConfigEntry
from .entity import TVMgmtEntity
from .light_sync.entities import FACTORIES
from .state import MODES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ModeSelect(entry.runtime_data)])
    if (light_sync := entry.runtime_data.light_sync) is not None:
        light_sync.register_platform(FACTORIES["select"], async_add_entities)


class ModeSelect(TVMgmtEntity, SelectEntity):
    _attr_options = MODES

    def __init__(self, manager) -> None:
        super().__init__(manager, "mode")

    @property
    def current_option(self) -> str:
        return self.manager.state.mode

    async def async_select_option(self, option: str) -> None:
        await self._ensure_allowed()
        self.manager.set_mode(option)
