"""TV Mgmt: parental controls for smart TVs in Home Assistant."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
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
    SERVICE_TURN_OFF_TV_LIGHTS,
    SERVICE_UNBLOCK,
)
from .manager import TVManager

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type TVMgmtConfigEntry = ConfigEntry[TVManager]

PROFILE_SCHEMA = vol.Schema({vol.Required(ATTR_PROFILE_ID): cv.string})
MESSAGE_SCHEMA = PROFILE_SCHEMA.extend(
    {
        vol.Required("message"): vol.All(
            cv.string, vol.Strip, vol.Length(min=1, max=120)
        ),
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
    from .home_limits import async_get_home

    await async_get_home(hass)
    from .light_sync import async_setup_light_sync
    from .light_sync.services import async_setup_services as async_setup_light_services

    await async_setup_light_sync(hass)
    _register_services(hass)
    async_setup_light_services(hass)
    websocket.async_register(hass)
    await async_register_panel(hass)
    # Serve message videos for the Apple TV; registered up front, while the web
    # server still accepts new routes.
    from .messages import async_register_messages_path

    await async_register_messages_path(hass)
    from .repairs import async_register

    # Entity-registry repairs. Registered here, with the rest of startup,
    # so a TV removed while Home Assistant was stopped is caught on the
    # next start as well as while it's running.
    async_register(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Upgrade entries created by 0.1.x."""
    if entry.version > 2:
        return False  # Downgrade from a newer version.

    if entry.version == 1:
        # 0.1.x kept the input-lock settings flat; 1.0 groups them in sections.
        lock_keys = {
            CONF_ALLOWED_SOURCES,
            CONF_TARGET_SOURCE,
            CONF_REVERT_DELAY,
            CONF_ENFORCE_ON_POWER_ON,
            CONF_MAX_ATTEMPTS,
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
    # What a change to the entry is compared with (see the update listener).
    manager.loaded_as = (entry.title, dict(entry.data))

    from .light_sync import LightSync

    light_sync = LightSync(hass, manager)
    await light_sync.async_setup()
    manager.light_sync = light_sync

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    light_sync.async_start()
    # The listener applies options in place, and reloads when the TV or its
    # name changes. Reconfigure detaches it first (see detach_update_listener)
    # so async_update_reload_and_abort is the only reload. Home Assistant
    # 2026.12 rejects a flow that reloads an entry which still has a listener.
    unsub_update = entry.add_update_listener(_async_update_listener)

    @callback
    def _detach_update_listener() -> None:
        try:
            unsub_update()
        except ValueError:
            return

    entry.async_on_unload(_detach_update_listener)
    manager.detach_update_listener = _detach_update_listener

    from .repairs import async_reconcile_entities

    async_reconcile_entities(hass)
    return True


def detach_update_listener(entry: ConfigEntry) -> None:
    """Drop the update listener so a config flow can reload the entry itself.

    Setup registers the listener again after the reload. Calling this when
    the entry was never loaded (no runtime data) does nothing.
    """
    manager = getattr(entry, "runtime_data", None)
    detach = getattr(manager, "detach_update_listener", None)
    if detach is not None:
        detach()
        manager.detach_update_listener = None


def _profile_devices(
    hass: HomeAssistant, entry: ConfigEntry, manager: TVManager
) -> list[dr.DeviceEntry]:
    """The TV's device and the linked Apple TV's, where they have one."""
    entities = er.async_get(hass)
    registry = dr.async_get(hass)
    devices: list[dr.DeviceEntry] = []
    for entity_id in (
        entry.data[CONF_MEDIA_PLAYER],
        manager.box.entity_id if manager.box else None,
    ):
        entity = entities.async_get(entity_id) if entity_id else None
        if entity is None or entity.device_id is None:
            continue
        if (device := registry.async_get(entity.device_id)) is not None:
            devices.append(device)
    return devices


def _link_devices(hass: HomeAssistant, entry: ConfigEntry, manager: TVManager) -> None:
    """Show TV Mgmt under Linked devices on the TV and the Apple TV.

    Home Assistant 2026.8 gives each device one config entry
    (``device.config_entry_id``) and links devices from different integrations
    that share an identifier. TV Mgmt's device carries the TV's and the Apple
    TV's identifiers. Lookups with ``async_get_device_by_identifier`` still
    find the TV's own device when the TV's config entry is passed, because
    identifiers are unique per config entry.

    The TV's device stays on the TV's integration. It is not moved with
    ``new_config_entry_id``. Copies left over from the 2026.8 device split
    (a second device on this config entry that isn't the profile or a light
    zone) are removed with ``async_remove_device``.
    """
    registry = dr.async_get(hass)
    devices = _profile_devices(hass, entry, manager)
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
        # Only identifiers that belong to the TV's or the Apple TV's own
        # integration. A device already owned by this profile is the profile
        # itself (or a zone) and must not be copied onto itself.
        if device.config_entry_id is None or device.config_entry_id == entry.entry_id:
            continue
        # Identifiers are unique per config entry. Resolve them with the
        # device's own entry so a shared identifier doesn't pick TV Mgmt's copy.
        if not device.identifiers:
            continue
        matched = registry.async_get_devices(
            identifiers=set(device.identifiers),
            config_entry_id=device.config_entry_id,
        )
        if any(item.id == device.id for item in matched):
            shared |= set(device.identifiers)
    zone_prefix = f"{entry.entry_id}_zone_"
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.id == own.id or device.config_entry_id != entry.entry_id:
            continue
        if any(
            domain == DOMAIN and ident.startswith(zone_prefix)
            for domain, ident in device.identifiers
        ):
            continue  # A light sync zone device.
        registry.async_remove_device(device.id)
    # Clear a via device left by a version that nested the profile under the
    # TV. via_device on DeviceInfo is deprecated; the link is the shared
    # identifiers. Only pass via_device_id when there is one to clear.
    update: dict[str, Any] = {}
    if set(own.identifiers) != {own_id, *shared}:
        update["new_identifiers"] = {own_id, *shared}
    if own.via_device_id is not None:
        update["via_device_id"] = None
    if update:
        registry.async_update_device(own.id, **update)


async def async_unload_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        manager = entry.runtime_data
        if manager.light_sync is not None:
            await manager.light_sync.async_stop()
            manager.light_sync = None
        await manager.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> None:
    from .light_sync.controller import async_remove_zone_snapshots
    from .light_sync.storage import DATA_LIGHT_SYNC
    from .storage import ActivityStore, ProfileStore

    await ProfileStore(hass, entry.entry_id).async_remove()
    await ActivityStore(hass, entry.entry_id).async_remove()
    if (light_data := hass.data.get(DATA_LIGHT_SYNC)) is not None:
        zones = light_data.config.zones_for(entry.entry_id)
        # Give the lights back, then forget the TV's zones.
        await async_remove_zone_snapshots(hass, entry.entry_id, zones)
        await light_data.config.async_remove_entry(entry.entry_id)
    from .repairs import async_delete_entry_issues

    async_delete_entry_issues(hass, entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: TVMgmtConfigEntry) -> None:
    """Options changed (the sidebar app or the options flow saved settings).

    Most settings apply in place (TVManager.apply_options), so the TV's
    timers, today's state and the light sync zones carry on. A reload sets the
    TV up from scratch; it's only needed when the TV itself or its name
    changed (entry data or title), or for an option apply_options can't
    change in place, such as which Apple TV is linked.
    """
    manager = entry.runtime_data
    if getattr(manager, "loaded_as", None) == (
        entry.title,
        dict(entry.data),
    ) and manager.apply_options(dict(entry.options)):
        from .repairs import async_reconcile_entities

        # A speaker or light taken off the profile in settings clears its repair.
        async_reconcile_entities(hass)
        return
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

    hass.services.async_register(
        DOMAIN, SERVICE_GRANT_EXTENSION, grant_extension, EXTENSION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_BLOCK, force_block, PROFILE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_UNBLOCK, unblock, PROFILE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_RESET_USAGE, reset_usage, PROFILE_SCHEMA
    )

    async def send_message(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        manager = _manager(hass, call)
        targets = manager.message_targets()
        wanted = {
            key: call.data[key] and targets[key]
            for key in ("apple_tv", "screen", "speak")
        }
        if not any(wanted.values()):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nowhere_to_send"
            )
        await manager.async_send_message(call.data["message"], **wanted)

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_MESSAGE, send_message, MESSAGE_SCHEMA
    )

    async def turn_off_tv_lights(call: ServiceCall) -> ServiceResponse:
        await async_ensure_allowed(hass, call.context)
        result = await _manager(hass, call).async_turn_off_tv_lights()
        return result if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        SERVICE_TURN_OFF_TV_LIGHTS,
        turn_off_tv_lights,
        PROFILE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
