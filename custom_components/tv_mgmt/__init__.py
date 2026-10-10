"""TV Mgmt: parental controls for smart TVs in Home Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.typing import ConfigType

from .backends import create_backend
from .const import (
    ATTR_MINUTES,
    ATTR_PROFILE_ID,
    CONF_ALLOWED_SOURCES,
    CONF_ENFORCE_ON_POWER_ON,
    CONF_MAX_ATTEMPTS,
    CONF_MEDIA_PLAYER,
    CONF_REVERT_DELAY,
    CONF_TARGET_SOURCE,
    DOMAIN,
    SECTION_INPUT_LOCK,
    SECTION_SCREEN_TIME,
    SERVICE_FORCE_BLOCK,
    SERVICE_GRANT_EXTENSION,
    SERVICE_RESET_USAGE,
    SERVICE_SEND_MESSAGE,
    SERVICE_UNBLOCK,
)
from .manager import TVManager

PLATFORMS: list[Platform] = [Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type TVMgmtConfigEntry = ConfigEntry[TVManager]

PROFILE_SCHEMA = vol.Schema({vol.Required(ATTR_PROFILE_ID): cv.string})
MESSAGE_SCHEMA = PROFILE_SCHEMA.extend(
    {
        vol.Required("message"): vol.All(cv.string, vol.Strip, vol.Length(min=1, max=120)),
        vol.Optional("apple_tv", default=True): cv.boolean,
        vol.Optional("screen", default=True): cv.boolean,
        vol.Optional("speak", default=False): cv.boolean,
    }
)
EXTENSION_SCHEMA = PROFILE_SCHEMA.extend(
    {vol.Required(ATTR_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=-240, max=240))}
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    from . import websocket
    from .access import async_get_access
    from .panel import async_register_panel

    await async_get_access(hass)
    _register_services(hass)
    websocket.async_register(hass)
    await async_register_panel(hass)
    # Serve message videos for the Apple TV; registered up front, while the web
    # server still accepts new routes.
    from .messages import async_register_messages_path

    await async_register_messages_path(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Upgrade entries created by 0.1.x."""
    if entry.version > 2:
        return False  # Downgrade from a newer version.

    if entry.version == 1:
        # 0.1.x kept the input-lock settings flat; 1.0 groups them in sections.
        lock_keys = {
            CONF_ALLOWED_SOURCES, CONF_TARGET_SOURCE, CONF_REVERT_DELAY,
            CONF_ENFORCE_ON_POWER_ON, CONF_MAX_ATTEMPTS,
        }
        old = dict(entry.options)
        options = {
            SECTION_INPUT_LOCK: {k: v for k, v in old.items() if k in lock_keys},
            SECTION_SCREEN_TIME: {},
        }

        # Keep entity IDs and history for the two entities 0.1.x had.
        renames = {"_lock": "_input_lock", "_blocked_count": "_blocked_switches_today"}

        @callback
        def _migrate_unique_id(entity: er.RegistryEntry) -> dict[str, str] | None:
            for old_suffix, new_suffix in renames.items():
                if entity.unique_id == f"{entry.entry_id}{old_suffix}":
                    return {"new_unique_id": f"{entry.entry_id}{new_suffix}"}
            return None

        await er.async_migrate_entries(hass, entry.entry_id, _migrate_unique_id)
        hass.config_entries.async_update_entry(entry, options=options, version=2)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> bool:
    backend = create_backend(hass, entry.data[CONF_MEDIA_PLAYER])
    manager = TVManager(hass, entry, backend)
    await manager.async_start()
    entry.runtime_data = manager
    _link_devices(hass, entry, manager)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


@callback
def _shared_devices_model(registry: dr.DeviceRegistry) -> bool:
    """Newer Home Assistant: one config entry per device, linked by shared identifiers."""
    return hasattr(registry, "async_get_devices")


def _profile_devices(
    hass: HomeAssistant, entry: ConfigEntry, manager: TVManager
) -> list[dr.DeviceEntry]:
    """The TV's device and the linked Apple TV's, where they have one."""
    entities = er.async_get(hass)
    registry = dr.async_get(hass)
    devices: list[dr.DeviceEntry] = []
    for entity_id in (entry.data[CONF_MEDIA_PLAYER], manager.box.entity_id if manager.box else None):
        entity = entities.async_get(entity_id) if entity_id else None
        if entity is None or entity.device_id is None:
            continue
        if (device := registry.async_get(entity.device_id)) is not None:
            devices.append(device)
    return devices


def _link_devices(hass: HomeAssistant, entry: ConfigEntry, manager: TVManager) -> None:
    """Show TV Mgmt with the TV's and the Apple TV's own devices."""
    registry = dr.async_get(hass)
    devices = _profile_devices(hass, entry, manager)
    if _shared_devices_model(registry):
        _link_shared_devices(registry, entry, devices)
    else:
        _link_by_config_entry(registry, entry, devices)


def _link_shared_devices(
    registry: dr.DeviceRegistry, entry: ConfigEntry, devices: list[dr.DeviceEntry]
) -> None:
    """Newer Home Assistant: list them under each other's Linked devices.

    Home Assistant links devices from different integrations that share an
    identifier, as it does a Rain Bird controller and its UniFi client. So
    TV Mgmt's device carries the TV's and the Apple TV's identifiers too.
    Lookups by those identifiers still find the TV's own device first, since
    Home Assistant prefers the device from the identifier's integration.
    """
    own_id = (DOMAIN, entry.entry_id)
    own = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={own_id},
        name=f"TV Mgmt — {entry.title}",
        manufacturer="TV Mgmt",
        model="TV profile",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    shared: set[tuple[str, str]] = set()
    for device in devices:
        if device.config_entry_id != entry.entry_id:
            shared |= set(device.identifiers)
    # Upgrading split the TV's device that 1.3 to 1.9 added TV Mgmt to into one
    # device per integration. TV Mgmt's copies hold the TV's identifiers, which
    # would clash with linking, and nothing uses them, so they go.
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.id != own.id:
            registry.async_remove_device(device.id)
    registry.async_update_device(
        own.id, new_identifiers={own_id, *shared}, via_device_id=None
    )


def _link_by_config_entry(
    registry: dr.DeviceRegistry, entry: ConfigEntry, devices: list[dr.DeviceEntry]
) -> None:
    """Older Home Assistant: add TV Mgmt to the TV's and the Apple TV's devices.

    The profile's device points at the TV (via_device), and adding this entry
    to the TV's device links it the other way. A device that's no longer part
    of the profile, such as an Apple TV that was swapped or unlinked, is let
    go. Home Assistant removes every link when the profile is deleted.
    """
    wanted: set[str] = set()
    for device in devices:
        wanted.add(device.id)
        if entry.entry_id not in device.config_entries:
            registry.async_update_device(device.id, add_config_entry_id=entry.entry_id)

    own = (DOMAIN, entry.entry_id)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.id in wanted or own in device.identifiers:
            continue
        # Only devices another integration owns; never remove a device outright.
        if len(device.config_entries) > 1:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> None:
    from .storage import ActivityStore, ProfileStore

    await ProfileStore(hass, entry.entry_id).async_remove()
    await ActivityStore(hass, entry.entry_id).async_remove()


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
    from .access import async_ensure_allowed

    async def grant_extension(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        _manager(hass, call).grant_extension(call.data[ATTR_MINUTES])

    async def force_block(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        _manager(hass, call).force_block()

    async def unblock(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        _manager(hass, call).unblock()

    async def reset_usage(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        _manager(hass, call).reset_usage()

    hass.services.async_register(DOMAIN, SERVICE_GRANT_EXTENSION, grant_extension, EXTENSION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_FORCE_BLOCK, force_block, PROFILE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_UNBLOCK, unblock, PROFILE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESET_USAGE, reset_usage, PROFILE_SCHEMA)

    async def send_message(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        manager = _manager(hass, call)
        targets = manager.message_targets()
        wanted = {key: call.data[key] and targets[key] for key in ("apple_tv", "screen", "speak")}
        if not any(wanted.values()):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nowhere_to_send")
        await manager.async_send_message(call.data["message"], **wanted)

    hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGE, send_message, MESSAGE_SCHEMA)
