"""Websocket commands for the panel's Lights tab.

Zones and the home-wide settings are saved in TV Mgmt's own storage, so
saving them never reloads a TV. Like the rest of the sidebar app, only admins
and the parents an admin allowed can use these.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send

from ..const import SIGNAL_ANY_UPDATED
from ..websocket import _manager, _managers, require_access
from . import LightSync, async_rebuild_metadata, new_zone_id
from .color import clamp_rgb, normalize_title
from .const import (
    COLOR_SOURCE_PRESETS,
    CONF_BRIGHTNESS_PCT,
    CONF_COLOR_MODE,
    CONF_COLOR_SOURCES,
    CONF_DARK_THRESHOLD,
    CONF_DEFAULT_COLOR,
    CONF_ENABLED,
    CONF_EXPAND_GROUPS,
    CONF_EXTRA_PLAYERS,
    CONF_EXTRACTION_METHOD,
    CONF_FUZZY_TITLE_MATCH,
    CONF_LIGHT_THRESHOLD,
    CONF_LIGHTS,
    CONF_MEDIA_TYPES,
    CONF_METADATA_ARTWORK,
    CONF_METADATA_PROVIDERS,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_SATURATION,
    CONF_NAME,
    CONF_PALETTE_SIZE,
    CONF_PALETTE_STRATEGY,
    CONF_PAUSE_BEHAVIOR,
    CONF_PERSIST_SNAPSHOT,
    CONF_REQUIRE_TV_ON,
    CONF_RESTORE_DELAY,
    CONF_SATURATION_BOOST,
    CONF_SKIP_ACHROMATIC,
    CONF_STOP_BEHAVIOR,
    CONF_TMDB_API_KEY,
    CONF_TRAKT_CLIENT_ID,
    CONF_TRANSITION,
    CONF_UNSUPPORTED_LIGHTS,
    CONF_USE_APPLE_TV,
    CONF_USE_TV_PLAYER,
    CONF_ZONE_ID,
    DEFAULT_NAME,
    DOMAIN,
    MAX_PALETTE_SIZE,
    MAX_ZONES_PER_TV,
    MEDIA_TYPE_OPTIONS,
    MIN_PALETTE_SIZE,
    SELECTABLE_COLOR_SOURCES,
    ZONE_DEFAULTS,
    ExtractionMethod,
    IdleBehavior,
    MetadataProviderName,
    PaletteStrategy,
    SyncColorMode,
    UnsupportedLightBehavior,
)
from .metadata import MetadataAuthError, MetadataError, async_validate_credentials
from .storage import DATA_LIGHT_SYNC

# Panel-side id of the home-wide light sync data in live updates.
LIGHT_SYNC_UPDATED = "light_sync"

_ENTITY = r"^{}\.[a-z0-9_]+$"
_LIGHT = vol.All(str, vol.Match(_ENTITY.format("light")))
_PLAYER = vol.All(str, vol.Match(_ENTITY.format("media_player")))
_RGB = vol.All(
    vol.Length(min=3, max=3), [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))]
)


def _unique(items: list[Any]) -> list[Any]:
    return list(dict.fromkeys(items))


def _values(enum: type) -> vol.In:
    return vol.In([m.value for m in enum])


def _number(low: float, high: float, kind: type = float) -> vol.All:
    return vol.All(vol.Coerce(kind), vol.Range(min=low, max=high))


ZONE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ZONE_ID): vol.All(str, vol.Match(r"^[a-z0-9]{1,16}$")),
        vol.Optional(CONF_NAME, default=""): vol.All(str, vol.Strip, vol.Length(max=60)),
        vol.Required(CONF_LIGHTS): vol.All([_LIGHT], _unique, vol.Length(min=1, max=50)),
        vol.Optional(CONF_ENABLED): bool,
        vol.Optional(CONF_USE_TV_PLAYER): bool,
        vol.Optional(CONF_USE_APPLE_TV): bool,
        vol.Optional(CONF_EXTRA_PLAYERS): vol.All([_PLAYER], _unique, vol.Length(max=10)),
        vol.Optional(CONF_REQUIRE_TV_ON): bool,
        vol.Optional(CONF_BRIGHTNESS_PCT): _number(1, 100, int),
        vol.Optional(CONF_TRANSITION): _number(0, 30),
        vol.Optional(CONF_UNSUPPORTED_LIGHTS): _values(UnsupportedLightBehavior),
        vol.Optional(CONF_EXPAND_GROUPS): bool,
        vol.Optional(CONF_DEFAULT_COLOR): _RGB,
        vol.Optional(CONF_COLOR_SOURCES): vol.All(
            [vol.In(SELECTABLE_COLOR_SOURCES)], _unique, vol.Length(min=1)
        ),
        vol.Optional(CONF_FUZZY_TITLE_MATCH): bool,
        vol.Optional(CONF_MEDIA_TYPES): vol.All([vol.In(MEDIA_TYPE_OPTIONS)], _unique),
        vol.Optional(CONF_EXTRACTION_METHOD): _values(ExtractionMethod),
        vol.Optional(CONF_MIN_SATURATION): _number(0, 100, int),
        vol.Optional(CONF_SATURATION_BOOST): _number(1.0, 3.0),
        vol.Optional(CONF_MIN_BRIGHTNESS): _number(0, 100, int),
        vol.Optional(CONF_DARK_THRESHOLD): _number(0, 255, int),
        vol.Optional(CONF_LIGHT_THRESHOLD): _number(0, 255, int),
        vol.Optional(CONF_SKIP_ACHROMATIC): bool,
        vol.Optional(CONF_PAUSE_BEHAVIOR): _values(IdleBehavior),
        vol.Optional(CONF_STOP_BEHAVIOR): _values(IdleBehavior),
        vol.Optional(CONF_RESTORE_DELAY): _number(0, 300),
        vol.Optional(CONF_PERSIST_SNAPSHOT): bool,
        vol.Optional(CONF_COLOR_MODE): _values(SyncColorMode),
        vol.Optional(CONF_PALETTE_SIZE): _number(MIN_PALETTE_SIZE, MAX_PALETTE_SIZE, int),
        vol.Optional(CONF_PALETTE_STRATEGY): _values(PaletteStrategy),
        vol.Optional(CONF_METADATA_ARTWORK): bool,
    }
)


def _light_sync(hass: HomeAssistant, connection, msg) -> LightSync | None:
    if (manager := _manager(hass, connection, msg)) is None:
        return None
    if manager.light_sync is None:
        connection.send_error(msg["id"], "not_found", "Light sync isn't running for this TV")
        return None
    return manager.light_sync


@callback
def _changed(hass: HomeAssistant) -> None:
    async_dispatcher_send(hass, SIGNAL_ANY_UPDATED, LIGHT_SYNC_UPDATED)


def _settings_for_panel(settings: dict[str, Any]) -> dict[str, Any]:
    """Home-wide settings without the keys themselves (only whether they're set)."""
    return {
        CONF_METADATA_PROVIDERS: list(settings[CONF_METADATA_PROVIDERS] or []),
        "tmdb_key_set": bool(settings[CONF_TMDB_API_KEY]),
        "trakt_key_set": bool(settings[CONF_TRAKT_CLIENT_ID]),
    }


# ---- read ------------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/light_sync/get"})
@require_access
@callback
def ws_get(hass: HomeAssistant, connection, msg) -> None:
    """Everything the Lights tab shows: every TV's zones and the shared settings."""
    data = hass.data[DATA_LIGHT_SYNC]
    tvs = []
    for manager in _managers(hass):
        light_sync = manager.light_sync
        tvs.append(
            {
                "entry_id": manager.entry.entry_id,
                "name": manager.name,
                "tv_lights": manager.tv_lights,
                **(light_sync.as_panel() if light_sync else {"zones": []}),
            }
        )
    connection.send_result(
        msg["id"],
        {
            "tvs": tvs,
            "settings": _settings_for_panel(data.config.settings),
            "titles": data.titles.as_list(),
            "cache": data.cache.stats(),
            "defaults": {**ZONE_DEFAULTS, CONF_NAME: DEFAULT_NAME},
            "max_zones": MAX_ZONES_PER_TV,
            "choices": {
                "color_sources": list(SELECTABLE_COLOR_SOURCES),
                "presets": {k: [str(s) for s in v] for k, v in COLOR_SOURCE_PRESETS.items()},
                "media_types": list(MEDIA_TYPE_OPTIONS),
                "extraction_methods": [m.value for m in ExtractionMethod],
                "idle_behaviors": [m.value for m in IdleBehavior],
                "unsupported_lights": [m.value for m in UnsupportedLightBehavior],
                "color_modes": [m.value for m in SyncColorMode],
                "palette_strategies": [m.value for m in PaletteStrategy],
                "palette_sizes": list(range(MIN_PALETTE_SIZE, MAX_PALETTE_SIZE + 1)),
                "metadata_providers": [m.value for m in MetadataProviderName],
            },
        },
    )


# ---- zones -----------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/light_sync/zone/save",
        vol.Required("entry_id"): str,
        vol.Required("zone"): ZONE_SCHEMA,
    }
)
@require_access
@websocket_api.async_response
async def ws_zone_save(hass: HomeAssistant, connection, msg) -> None:
    """Add a zone to a TV, or change one. Applied straight away, no reload."""
    if (light_sync := _light_sync(hass, connection, msg)) is None:
        return
    config = hass.data[DATA_LIGHT_SYNC].config
    entry_id = light_sync.entry.entry_id
    zone = dict(msg["zone"])
    existing = config.zone(entry_id, zone[CONF_ZONE_ID]) if CONF_ZONE_ID in zone else None
    if CONF_ZONE_ID in zone and existing is None:
        connection.send_error(msg["id"], "not_found", "That light zone doesn't exist")
        return
    if existing is None:
        if len(config.zones_for(entry_id)) >= MAX_ZONES_PER_TV:
            connection.send_error(
                msg["id"], "too_many", f"A TV can have up to {MAX_ZONES_PER_TV} light zones"
            )
            return
        zone[CONF_ZONE_ID] = new_zone_id()
        merged = {**ZONE_DEFAULTS, **zone}
    else:
        merged = {**existing, **zone}
    merged[CONF_NAME] = merged.get(CONF_NAME) or DEFAULT_NAME
    await config.async_save_zone(entry_id, merged)
    saved = config.zone(entry_id, merged[CONF_ZONE_ID])
    assert saved is not None
    await light_sync.async_save_zone(saved)
    _changed(hass)
    connection.send_result(msg["id"], {"zone": saved})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/light_sync/zone/remove",
        vol.Required("entry_id"): str,
        vol.Required("zone_id"): str,
    }
)
@require_access
@websocket_api.async_response
async def ws_zone_remove(hass: HomeAssistant, connection, msg) -> None:
    """Delete a zone: its lights go back to how they were."""
    if (light_sync := _light_sync(hass, connection, msg)) is None:
        return
    config = hass.data[DATA_LIGHT_SYNC].config
    if not await config.async_remove_zone(light_sync.entry.entry_id, msg["zone_id"]):
        connection.send_error(msg["id"], "not_found", "That light zone doesn't exist")
        return
    await light_sync.async_remove_zone(msg["zone_id"])
    _changed(hass)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/light_sync/zone/action",
        vol.Required("entry_id"): str,
        vol.Required("zone_id"): str,
        vol.Required("action"): vol.In(["resync", "save_title_color"]),
    }
)
@require_access
@websocket_api.async_response
async def ws_zone_action(hass: HomeAssistant, connection, msg) -> None:
    """Look again now, or keep the current colour for the title playing."""
    if (light_sync := _light_sync(hass, connection, msg)) is None:
        return
    if (zone := light_sync.zones.get(msg["zone_id"])) is None:
        connection.send_error(msg["id"], "not_found", "That light zone doesn't exist")
        return
    try:
        if msg["action"] == "resync":
            outcome = await zone.async_resync()
            connection.send_result(
                msg["id"], {"outcome": outcome, "error": zone.data.last_error}
            )
        else:
            title = await zone.async_save_current_title_color()
            connection.send_result(msg["id"], {"title": title})
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "failed", str(err) or "Couldn't do that now")


# ---- home-wide settings and title colours ------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/light_sync/settings/set",
        vol.Optional(CONF_METADATA_PROVIDERS): vol.All(
            [vol.In([m.value for m in MetadataProviderName])], _unique
        ),
        # Left out: keep the saved key. Empty: forget it.
        vol.Optional(CONF_TMDB_API_KEY): vol.All(str, vol.Strip, vol.Length(max=200)),
        vol.Optional(CONF_TRAKT_CLIENT_ID): vol.All(str, vol.Strip, vol.Length(max=200)),
    }
)
@require_access
@websocket_api.async_response
async def ws_settings_set(hass: HomeAssistant, connection, msg) -> None:
    """Save the TMDB / Trakt settings shared by every TV (new keys are checked)."""
    config = hass.data[DATA_LIGHT_SYNC].config
    settings = config.settings
    for key, provider in (
        (CONF_TMDB_API_KEY, MetadataProviderName.TMDB),
        (CONF_TRAKT_CLIENT_ID, MetadataProviderName.TRAKT),
    ):
        if key not in msg:
            continue
        value = msg[key]
        if value and value != settings[key]:
            try:
                await async_validate_credentials(hass, provider, value)
            except MetadataAuthError:
                connection.send_error(
                    msg["id"], "invalid_key", f"{provider.value.upper()} didn't accept that key"
                )
                return
            except MetadataError as err:
                connection.send_error(
                    msg["id"],
                    "cannot_connect",
                    f"Couldn't reach {provider.value.upper()} to check the key: {err}",
                )
                return
        settings[key] = value
    if CONF_METADATA_PROVIDERS in msg:
        settings[CONF_METADATA_PROVIDERS] = msg[CONF_METADATA_PROVIDERS]
    await config.async_set_settings(settings)
    async_rebuild_metadata(hass)
    _changed(hass)
    connection.send_result(msg["id"], {"settings": _settings_for_panel(config.settings)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/light_sync/titles/set",
        vol.Required("title"): vol.All(cv.string, vol.Length(min=1, max=255)),
        vol.Required("rgb_color"): _RGB,
    }
)
@require_access
@websocket_api.async_response
async def ws_titles_set(hass: HomeAssistant, connection, msg) -> None:
    """Always use this colour for a movie or show."""
    if not normalize_title(msg["title"]):
        connection.send_error(msg["id"], "invalid_title", "That title can't be matched")
        return
    key = await hass.data[DATA_LIGHT_SYNC].titles.async_set(
        msg["title"], clamp_rgb(msg["rgb_color"])
    )
    connection.send_result(msg["id"], {"key": key})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/light_sync/titles/remove",
        vol.Required("title"): vol.All(cv.string, vol.Length(min=1, max=255)),
    }
)
@require_access
@websocket_api.async_response
async def ws_titles_remove(hass: HomeAssistant, connection, msg) -> None:
    """Forget a title's colour."""
    if not await hass.data[DATA_LIGHT_SYNC].titles.async_remove(msg["title"]):
        connection.send_error(msg["id"], "not_found", "No color is saved for that title")
        return
    connection.send_result(msg["id"])


COMMANDS = (
    ws_get,
    ws_zone_save,
    ws_zone_remove,
    ws_zone_action,
    ws_settings_set,
    ws_titles_set,
    ws_titles_remove,
)
