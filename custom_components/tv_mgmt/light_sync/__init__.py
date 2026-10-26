"""Light sync: colour a TV's lights from what's playing on it.

Ported from Movie Light Sync and built into TV Mgmt. Each TV can have
several *zones* (groups of lights with their own settings), set up in the
panel's Lights tab and saved in TV Mgmt's own storage, never in the TV's
config entry, so saving a zone never reloads the TV.

TV Mgmt's rules come first:

* When the TV turns off, the TV's own lights turn off with it and their
  pending restore is dropped.
* The lights' state is saved only after TV Mgmt has turned the TV's lights
  on, so restoring never turns them off under a TV that's on.
* Nothing is coloured while TV Mgmt is keeping the TV off.
* When TV Mgmt stops a blocked app or show, the lights are restored.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import Entity

from ..const import DOMAIN, SIGNAL_ANY_UPDATED
from .const import (
    CONF_ENABLED,
    CONF_METADATA_PROVIDERS,
    CONF_TMDB_API_KEY,
    CONF_TRAKT_CLIENT_ID,
    CONF_ZONE_ID,
    MANUFACTURER,
    MODEL,
    SIGNAL_TITLE_COLORS_UPDATED,
    TV_LIGHTS_SETTLE,
    MetadataProviderName,
)
from .controller import LightZone, ZoneLink
from .metadata import MetadataCache, async_create_service, issue_id
from .storage import (
    DATA_LIGHT_SYNC,
    ColorCache,
    LightSyncConfig,
    LightSyncData,
    TitleColorStore,
)

if TYPE_CHECKING:
    from ..manager import TVManager

type EntityFactory = Callable[[LightZone], list[Entity]]
type AddEntities = Callable[[list[Entity]], None]


async def async_setup_light_sync(hass: HomeAssistant) -> LightSyncData:
    """Load the zones, title colours and settings shared by every TV."""
    if (data := hass.data.get(DATA_LIGHT_SYNC)) is not None:
        return data
    config = LightSyncConfig(hass)
    await config.async_load()
    titles = TitleColorStore(hass)
    await titles.async_load()
    data = LightSyncData(
        config=config, titles=titles, cache=ColorCache(), metadata=MetadataCache(hass)
    )
    hass.data[DATA_LIGHT_SYNC] = data
    async_rebuild_metadata(hass, notify=False)

    @callback
    def _titles_changed() -> None:
        # The Lights tab lists the title colours: refresh it.
        async_dispatcher_send(hass, SIGNAL_ANY_UPDATED, "light_sync")

    async_dispatcher_connect(hass, SIGNAL_TITLE_COLORS_UPDATED, _titles_changed)
    return data


@callback
def async_rebuild_metadata(hass: HomeAssistant, *, notify: bool = True) -> None:
    """(Re)build the TMDB / Trakt lookups from the home-wide settings."""
    data = hass.data[DATA_LIGHT_SYNC]
    settings = data.config.settings
    for name in MetadataProviderName:
        ir.async_delete_issue(hass, DOMAIN, issue_id(name))
    assert data.metadata is not None
    data.metadata_service = async_create_service(
        hass,
        list(settings[CONF_METADATA_PROVIDERS] or []),
        {
            MetadataProviderName.TMDB: str(settings[CONF_TMDB_API_KEY] or ""),
            MetadataProviderName.TRAKT: str(settings[CONF_TRAKT_CLIENT_ID] or ""),
        },
        cache=data.metadata,
        limiters=data.metadata_limiters,
    )
    if notify:
        for light_sync in all_light_syncs(hass):
            for zone in light_sync.zones.values():
                zone.async_metadata_changed()


@callback
def all_light_syncs(hass: HomeAssistant) -> list[LightSync]:
    """Light sync of every loaded TV."""
    result: list[LightSync] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        manager = getattr(entry, "runtime_data", None)
        if manager is not None and getattr(manager, "light_sync", None) is not None:
            result.append(manager.light_sync)
    return result


def new_zone_id() -> str:
    """A short random id for a new zone."""
    return secrets.token_hex(4)


class LightSync(ZoneLink):
    """One TV's light sync zones, and their link to the TV's rules."""

    def __init__(self, hass: HomeAssistant, manager: TVManager) -> None:
        """Create the zones' owner (zones are made in :meth:`async_setup`)."""
        self.hass = hass
        self.manager = manager
        self.entry = manager.entry
        self.data: LightSyncData = hass.data[DATA_LIGHT_SYNC]
        self.zones: dict[str, LightZone] = {}
        self._platforms: list[tuple[EntityFactory, AddEntities]] = []
        self._entities: dict[str, list[Entity]] = {}
        self._lights_on_at: float | None = None
        self._gate: tuple[bool, bool | None] | None = None
        self._started = False

    # ---- ZoneLink ---------------------------------------------------------------

    @property
    def tv_entity_id(self) -> str:
        return self.manager.tv_entity_id

    @property
    def apple_tv_entity_id(self) -> str | None:
        return self.manager.box.entity_id if self.manager.box else None

    def tv_lights(self) -> list[str]:
        return self.manager.tv_lights

    def tv_is_on(self) -> bool | None:
        return self.manager.backend.is_on

    def blocked(self) -> bool:
        return self.manager.is_blocking()

    def tv_lights_settle_remaining(self) -> float:
        if self._lights_on_at is None:
            return 0.0
        return max(0.0, self._lights_on_at + TV_LIGHTS_SETTLE - time.monotonic())

    @callback
    def zone_changed(self, zone: LightZone) -> None:
        # Only the panel needs to hear it; the TV's own entities don't change.
        async_dispatcher_send(self.hass, SIGNAL_ANY_UPDATED, self.entry.entry_id)

    @callback
    def save_changes(self, zone: LightZone, changes: dict[str, Any]) -> None:
        self.data.config.update_zone(self.entry.entry_id, zone.zone_id, changes)

    # ---- lifecycle ---------------------------------------------------------------

    async def async_setup(self) -> None:
        """Create the TV's zones from storage."""
        for config in self.data.config.zones_for(self.entry.entry_id):
            await self._async_create_zone(config)

    async def _async_create_zone(self, config: dict[str, Any]) -> LightZone:
        zone = LightZone(self.hass, self.entry, self, config)
        await zone.async_initialize()
        self.zones[zone.zone_id] = zone
        self._ensure_device(zone)
        return zone

    @callback
    def register_platform(self, factory: EntityFactory, add_entities: AddEntities) -> None:
        """An entity platform: add its entities for every zone, now and later."""
        self._platforms.append((factory, add_entities))
        for zone in self.zones.values():
            self._add_entities(zone, factory, add_entities)

    @callback
    def _add_entities(
        self, zone: LightZone, factory: EntityFactory, add_entities: AddEntities
    ) -> None:
        entities = factory(zone)
        self._entities.setdefault(zone.zone_id, []).extend(entities)
        add_entities(entities)

    @callback
    def async_start(self) -> None:
        """Start following the players (after the platforms are set up)."""
        self._started = True
        self._gate = self._current_gate()
        for zone in self.zones.values():
            zone.async_start()

    async def async_stop(self) -> None:
        """The TV is unloading: stop every zone (snapshots are kept)."""
        self._started = False
        for zone in list(self.zones.values()):
            await zone.async_shutdown()
        self._platforms.clear()
        self._entities.clear()

    # ---- zones from the panel ------------------------------------------------------

    async def async_save_zone(self, config: dict[str, Any]) -> LightZone:
        """Add or change a zone (saved already) and apply it live."""
        zone_id = config[CONF_ZONE_ID]
        if (zone := self.zones.get(zone_id)) is None:
            zone = await self._async_create_zone(config)
            for factory, add_entities in self._platforms:
                self._add_entities(zone, factory, add_entities)
            if self._started:
                zone.async_start()
            return zone
        zone.async_apply_options(config)
        await zone.async_set_enabled(bool(config.get(CONF_ENABLED, True)))
        self._ensure_device(zone)
        return zone

    async def async_remove_zone(self, zone_id: str) -> None:
        """Delete a zone (removed from storage already): restore its lights."""
        zone = self.zones.pop(zone_id, None)
        if zone is None:
            return
        await zone.async_remove()
        entities = er.async_get(self.hass)
        for entity in self._entities.pop(zone_id, []):
            if entity.entity_id and entities.async_get(entity.entity_id):
                entities.async_remove(entity.entity_id)
            elif entity.hass is not None:
                await entity.async_remove()
        devices = dr.async_get(self.hass)
        if device := _own_device(devices, zone.unique_key, self.entry.entry_id):
            # Only this TV's zone uses it.
            devices.async_remove_device(device.id)

    async def async_remove_all(self) -> None:
        """The TV was deleted: give every zone's lights back."""
        for zone_id in list(self.zones):
            await self.async_remove_zone(zone_id)

    @callback
    def _ensure_device(self, zone: LightZone) -> None:
        """The zone's device, shown under the TV's TV Mgmt device."""
        devices = dr.async_get(self.hass)
        device = devices.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, zone.unique_key)},
            name=zone_device_name(self.entry.title, zone.zone_name),
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        profile = _own_device(devices, self.entry.entry_id, self.entry.entry_id)
        changes: dict[str, Any] = {}
        if profile is not None and device.via_device_id != profile.id:
            changes["via_device_id"] = profile.id
        name = zone_device_name(self.entry.title, zone.zone_name)
        if device.name != name:
            changes["name"] = name
        if changes:
            devices.async_update_device(device.id, **changes)

    # ---- TV Mgmt hooks ---------------------------------------------------------------

    def _current_gate(self) -> tuple[bool, bool | None]:
        return (self.blocked(), self.tv_is_on())

    @callback
    def handle_profile_update(self) -> None:
        """TV Mgmt's view of the TV changed: look again if it matters."""
        if not self._started or not self.zones:
            return
        gate = self._current_gate()
        if gate == self._gate:
            return
        self._gate = gate
        for zone in self.zones.values():
            zone.async_schedule_evaluate()

    @callback
    def tv_lights_turned_on(self) -> None:
        """TV Mgmt just asked for the TV's lights to turn on."""
        self._lights_on_at = time.monotonic()

    @callback
    def tv_turned_off(self) -> None:
        """The TV turned off (TV Mgmt turns its lights off next)."""
        self._lights_on_at = None
        for zone in self.zones.values():
            self.entry.async_create_background_task(
                self.hass,
                zone.async_handle_tv_off(),
                f"{DOMAIN} light sync TV off ({zone.label})",
            )

    @callback
    def media_blocked(self) -> None:
        """TV Mgmt stopped a blocked app or show."""
        for zone in self.zones.values():
            self.entry.async_create_background_task(
                self.hass,
                zone.async_handle_media_blocked(),
                f"{DOMAIN} light sync blocked ({zone.label})",
            )

    # ---- panel ----------------------------------------------------------------------

    @callback
    def as_panel(self) -> dict[str, Any]:
        """The TV's zones, their settings and what they're doing, for the panel."""
        entities = er.async_get(self.hass)
        result: list[dict[str, Any]] = []
        for config in self.data.config.zones_for(self.entry.entry_id):
            zone = self.zones.get(config[CONF_ZONE_ID])
            item: dict[str, Any] = {"config": config, "status": None, "entities": {}}
            if zone is not None:
                item["status"] = zone_status(zone)
                prefix = f"{zone.unique_key}_"
                item["entities"] = {
                    entry.unique_id.removeprefix(prefix): entry.entity_id
                    for entry in er.async_entries_for_config_entry(
                        entities, self.entry.entry_id
                    )
                    if entry.unique_id.startswith(prefix)
                }
            result.append(item)
        return {
            "zones": result,
            "tv_player": self.tv_entity_id,
            "apple_tv": self.apple_tv_entity_id,
        }


def _own_device(
    devices: dr.DeviceRegistry, key: str, entry_id: str
) -> dr.DeviceEntry | None:
    """The TV's own TV Mgmt device with identifier ``(tv_mgmt, key)``."""
    identifier = (DOMAIN, key)
    if hasattr(devices, "async_get_device_by_identifier"):
        # Newer Home Assistant: identifiers are only unique per config entry.
        return devices.async_get_device_by_identifier(identifier, entry_id)
    return devices.async_get_device(identifiers={identifier})


def zone_device_name(tv: str, zone: str) -> str:
    """``"TV Mgmt — Living Room · Lights"``."""
    return f"TV Mgmt — {tv} · {zone}"


def zone_status(zone: LightZone) -> dict[str, Any]:
    """What a zone is doing, for the panel."""
    from .color import rgb_to_hex

    data = zone.data
    return {
        "enabled": data.enabled,
        "syncing": data.syncing,
        "held": data.held_reason,
        "color": rgb_to_hex(data.rgb_color) if data.rgb_color else None,
        "source": data.source.value,
        "title": data.media_title,
        "player": data.media_player if data.syncing else None,
        "palette": [rgb_to_hex(c) for c in data.palette],
        "lights": list(data.lights),
        "failed_lights": dict(data.failed_lights),
        "last_error": data.last_error,
        "pending": data.pending_action,
        "save_title": data.save_title,
        "outcome": zone.last_outcome,
        "players": zone.media_players,
        "name": zone.zone_name,
    }


__all__ = [
    "LightSync",
    "LightZone",
    "all_light_syncs",
    "async_rebuild_metadata",
    "async_setup_light_sync",
    "new_zone_id",
]
