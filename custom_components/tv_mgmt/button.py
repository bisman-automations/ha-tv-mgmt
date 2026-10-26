"""Buttons: light sync zones' resync and save-title-colour."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TVMgmtConfigEntry
from .light_sync.entities import FACTORIES

# Light sync zones queue their own light calls.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if (light_sync := entry.runtime_data.light_sync) is not None:
        light_sync.register_platform(FACTORIES["button"], async_add_entities)
