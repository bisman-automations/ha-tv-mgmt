"""Switches: input lock and adult mode."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TVMgmtConfigEntry
from .entity import TVMgmtEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities([InputLockSwitch(manager), AdultModeSwitch(manager)])


class InputLockSwitch(TVMgmtEntity, SwitchEntity):
    """Keeps the TV on its allowed input."""

    def __init__(self, manager) -> None:
        super().__init__(manager, "input_lock")

    @property
    def is_on(self) -> bool:
        return self.manager.state.input_lock

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        guard = self.manager.guard
        return {
            "allowed_inputs": guard.allowed_sources,
            "target_input": guard.target_source,
            "paused_reason": guard.paused_reason,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.manager.set_input_lock(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.manager.set_input_lock(False)


class AdultModeSwitch(TVMgmtEntity, SwitchEntity):
    """Lifts every rule for a while, then turns itself off."""

    def __init__(self, manager) -> None:
        super().__init__(manager, "adult_mode")

    @property
    def is_on(self) -> bool:
        return self.manager.adult_mode_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        until = self.manager.state.adult_mode_until_dt
        return {"until": until.isoformat() if until and self.is_on else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.manager.set_adult_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.manager.set_adult_mode(False)
