"""TV Mgmt: parental controls for smart TVs in Home Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .backends import create_backend
from .const import (
    ATTR_MINUTES,
    ATTR_PROFILE_ID,
    CONF_MEDIA_PLAYER,
    DOMAIN,
    SERVICE_FORCE_BLOCK,
    SERVICE_GRANT_EXTENSION,
    SERVICE_RESET_USAGE,
    SERVICE_UNBLOCK,
)
from .manager import TVManager

PLATFORMS: list[Platform] = [Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type TVMgmtConfigEntry = ConfigEntry[TVManager]

PROFILE_SCHEMA = vol.Schema({vol.Required(ATTR_PROFILE_ID): cv.string})
EXTENSION_SCHEMA = PROFILE_SCHEMA.extend(
    {vol.Required(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=-240, max=240))}
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> bool:
    backend = create_backend(hass, entry.data[CONF_MEDIA_PLAYER])
    manager = TVManager(hass, entry, backend)
    await manager.async_start()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> None:
    from .storage import ProfileStore

    await ProfileStore(hass, entry.entry_id).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _manager(hass: HomeAssistant, call: ServiceCall) -> TVManager:
    entry_id = call.data[ATTR_PROFILE_ID]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="profile_not_found",
            translation_placeholders={"profile_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="profile_not_loaded",
            translation_placeholders={"profile": entry.title},
        )
    return entry.runtime_data


@callback
def _register_services(hass: HomeAssistant) -> None:
    @callback
    def grant_extension(call: ServiceCall) -> None:
        _manager(hass, call).grant_extension(call.data[ATTR_MINUTES])

    @callback
    def force_block(call: ServiceCall) -> None:
        _manager(hass, call).force_block()

    @callback
    def unblock(call: ServiceCall) -> None:
        _manager(hass, call).unblock()

    @callback
    def reset_usage(call: ServiceCall) -> None:
        _manager(hass, call).reset_usage()

    hass.services.async_register(DOMAIN, SERVICE_GRANT_EXTENSION, grant_extension, EXTENSION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_FORCE_BLOCK, force_block, PROFILE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_UNBLOCK, unblock, PROFILE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET_USAGE, reset_usage, PROFILE_SCHEMA)
