"""Mode select: enforced / monitor only / paused."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TVMgmtConfigEntry
from .entity import TVMgmtEntity
from .state import MODES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ModeSelect(entry.runtime_data)])


class ModeSelect(TVMgmtEntity, SelectEntity):
    _attr_options = MODES

    def __init__(self, manager) -> None:
        super().__init__(manager, "mode")

    @property
    def current_option(self) -> str:
        return self.manager.state.mode

    async def async_select_option(self, option: str) -> None:
        self.manager.set_mode(option)
