"""TV Management: lock a TV to its allowed inputs."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .backends import create_backend
from .const import CONF_MEDIA_PLAYER
from .guard import InputGuard

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR]

type TVMgmtConfigEntry = ConfigEntry[InputGuard]


async def async_setup_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> bool:
    backend = create_backend(hass, entry.data[CONF_MEDIA_PLAYER])
    guard = InputGuard(hass, entry, backend)
    if hasattr(backend, "set_assumed_source") and guard.target_source:
        backend.set_assumed_source(guard.target_source)

    await backend.async_start()
    entry.runtime_data = guard

    # Platforms first so the switch can restore its on/off state before
    # the guard starts acting.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    guard.async_start()

    entry.async_on_unload(guard.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.backend.async_stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
