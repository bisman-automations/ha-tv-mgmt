"""Shared entity base for TV Mgmt."""

from __future__ import annotations

from homeassistant.core import State, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .access import async_ensure_allowed
from .const import DOMAIN, SIGNAL_UPDATED
from .manager import TVManager


class TVMgmtEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    # What this entity last wrote on a profile update, and the state that
    # write left (see _async_updated).
    _last_written: str | None = None
    _written: State | None = None

    def __init__(self, manager: TVManager, key: str) -> None:
        self.manager = manager
        entry = manager.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        # Linked devices (shared identifiers on the device registry) show the
        # profile next to the TV. via_device is deprecated and removed in 2027.8.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            # Same pattern as Apple TV Mgmt ("Apple TV Mgmt — Family Room").
            name=f"TV Mgmt — {entry.title}",
            manufacturer="TV Mgmt",
            model="TV profile",
            entry_type=DeviceEntryType.SERVICE,
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
                self._async_updated,
            )
        )

    def _shown(self) -> str:
        """What this entity shows, to tell whether a write would change it."""
        return repr(
            (
                self.available,
                self.state,
                self.capability_attributes,
                self.extra_state_attributes,
            )
        )

    @callback
    def _async_updated(self) -> None:
        """The profile changed: write this entity only if what it shows did.

        Every update reaches every entity of the profile, and most show the
        same as before (counting time moves the minutes, not the mode). Any
        other write since (a rename, an option picked) is written over.
        """
        shown = self._shown()
        current = self.hass.states.get(self.entity_id)
        if (
            current is not None
            and current is self._written
            and shown == self._last_written
        ):
            return
        self.async_write_ha_state()
        self._last_written = shown
        self._written = self.hass.states.get(self.entity_id)

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)
