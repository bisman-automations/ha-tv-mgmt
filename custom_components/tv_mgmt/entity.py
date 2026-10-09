"""Shared entity base for TV Mgmt."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .access import async_ensure_allowed
from .const import DOMAIN, SIGNAL_UPDATED
from .manager import TVManager


class TVMgmtEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: TVManager, key: str) -> None:
        self.manager = manager
        entry = manager.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            # Same pattern as Apple TV Mgmt ("Apple TV Mgmt — Family Room").
            name=f"TV Mgmt — {entry.title}",
            manufacturer="TV Mgmt",
            model="TV profile",
            entry_type=DeviceEntryType.SERVICE,
            via_device=_tv_device(manager),
        )

    async def _ensure_allowed(self) -> None:
        """Only admins and allowed parents may change TV Mgmt from the UI."""
        await async_ensure_allowed(self.hass, self._context)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATED.format(self.manager.entry.entry_id),
                self.async_write_ha_state,
            )
        )


def _tv_device(manager: TVManager) -> tuple[str, str] | None:
    """Show the profile under the TV's own device, when it has one."""
    hass = manager.hass
    entity = er.async_get(hass).async_get(manager.tv_entity_id)
    if entity is None or entity.device_id is None:
        return None
    device = dr.async_get(hass).async_get(entity.device_id)
    if device is None or not device.identifiers:
        return None
    return next(iter(device.identifiers))
