"""Sensor counting blocked input switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TVMgmtConfigEntry
from .entity import InputGuardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([BlockedCountSensor(entry.runtime_data)])


class BlockedCountSensor(InputGuardEntity, SensorEntity):
    _attr_icon = "mdi:shield-alert-outline"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, guard) -> None:
        super().__init__(guard, "blocked_count")

    @property
    def native_value(self) -> int:
        return self.guard.blocked_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_blocked_source": self.guard.last_blocked_source,
            "last_blocked_at": (
                self.guard.last_blocked_at.isoformat() if self.guard.last_blocked_at else None
            ),
        }
