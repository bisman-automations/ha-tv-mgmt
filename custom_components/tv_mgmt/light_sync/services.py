"""Light sync actions: title colours, resync and the artwork cache.

Registered once in ``async_setup``. Like TV Mgmt's other actions, only
admins and allowed parents may use them.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from ..access import async_ensure_allowed
from ..const import ATTR_PROFILE_ID
from .color import clamp_rgb, normalize_title, rgb_to_hex
from .const import (
    ATTR_RGB_COLOR,
    ATTR_TITLE,
    ATTR_ZONE,
    DOMAIN,
    LOGGER,
    SERVICE_CLEAR_CACHE,
    SERVICE_LIST_TITLE_COLORS,
    SERVICE_REMOVE_TITLE_COLOR,
    SERVICE_RESYNC,
    SERVICE_SET_TITLE_COLOR,
)
from .controller import LightZone, SyncOutcome
from .storage import DATA_LIGHT_SYNC

_RGB_SCHEMA = vol.All(
    vol.Length(min=3, max=3), [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))]
)
_TITLE = vol.All(cv.string, vol.Length(min=1, max=255))

SET_TITLE_COLOR_SCHEMA = vol.Schema(
    {vol.Required(ATTR_TITLE): _TITLE, vol.Required(ATTR_RGB_COLOR): _RGB_SCHEMA}
)
REMOVE_TITLE_COLOR_SCHEMA = vol.Schema({vol.Required(ATTR_TITLE): _TITLE})
LIST_TITLE_COLORS_SCHEMA = vol.Schema({})
RESYNC_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_PROFILE_ID): cv.string, vol.Optional(ATTR_ZONE): cv.string}
)
CLEAR_CACHE_SCHEMA = vol.Schema(
    {
        vol.Optional("resync", default=False): cv.boolean,
        vol.Optional("include_metadata", default=False): cv.boolean,
    }
)


def _zones(
    hass: HomeAssistant, profile_id: str | None, zone_ref: str | None
) -> list[LightZone]:
    """Zones of one TV (or every TV), optionally just one by id or name."""
    if profile_id is None:
        entries = list(hass.config_entries.async_loaded_entries(DOMAIN))
    else:
        entry = hass.config_entries.async_get_entry(profile_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="profile_not_found",
                translation_placeholders={"profile_id": profile_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="profile_not_loaded",
                translation_placeholders={"profile": entry.title},
            )
        entries = [entry]
    zones: list[LightZone] = []
    for entry in entries:
        if (light_sync := getattr(entry.runtime_data, "light_sync", None)) is None:
            continue
        zones.extend(light_sync.zones.values())
    if zone_ref is not None:
        wanted = zone_ref.strip().casefold()
        zones = [
            z for z in zones if z.zone_id == zone_ref or z.zone_name.casefold() == wanted
        ]
        if not zones:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="zone_not_found",
                translation_placeholders={"zone": zone_ref},
            )
    return zones


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the light sync actions."""

    async def _set_title_color(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        title: str = call.data[ATTR_TITLE]
        if not normalize_title(title):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_title",
                translation_placeholders={"title": title},
            )
        rgb = clamp_rgb(call.data[ATTR_RGB_COLOR])
        key = await hass.data[DATA_LIGHT_SYNC].titles.async_set(title, rgb)
        LOGGER.info("Title colour set: %r (%s) -> %s", title, key, rgb_to_hex(rgb))

    async def _remove_title_color(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        title: str = call.data[ATTR_TITLE]
        if not await hass.data[DATA_LIGHT_SYNC].titles.async_remove(title):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="title_not_found",
                translation_placeholders={"title": title},
            )
        LOGGER.info("Title colour removed: %r", title)

    async def _list_title_colors(call: ServiceCall) -> ServiceResponse:
        await async_ensure_allowed(hass, call.context)
        titles = hass.data[DATA_LIGHT_SYNC].titles.as_list()
        return {"count": len(titles), "titles": titles}

    async def _resync(call: ServiceCall) -> None:
        await async_ensure_allowed(hass, call.context)
        zones = _zones(hass, call.data.get(ATTR_PROFILE_ID), call.data.get(ATTR_ZONE))
        outcomes = [(zone, await zone.async_resync()) for zone in zones]
        failed = [
            f"{zone.label}: {zone.data.last_error or '?'}"
            for zone, outcome in outcomes
            if outcome is SyncOutcome.FAILED
        ]
        if failed:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="resync_failed",
                translation_placeholders={"error": "; ".join(failed)},
            )

    async def _clear_cache(call: ServiceCall) -> ServiceResponse:
        await async_ensure_allowed(hass, call.context)
        data = hass.data[DATA_LIGHT_SYNC]
        dropped = data.cache.clear()
        LOGGER.info("Artwork colour cache cleared (%d entries)", dropped)
        response: dict[str, Any] = {"cleared": dropped}
        if call.data["include_metadata"] and data.metadata is not None:
            response["metadata_cleared"] = await data.metadata.async_clear()
            LOGGER.info("Metadata cache cleared (%d entries)", response["metadata_cleared"])
        if call.data["resync"]:
            for zone in _zones(hass, None, None):
                await zone.async_resync()
        return response

    hass.services.async_register(
        DOMAIN, SERVICE_SET_TITLE_COLOR, _set_title_color, SET_TITLE_COLOR_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_TITLE_COLOR, _remove_title_color, REMOVE_TITLE_COLOR_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_TITLE_COLORS,
        _list_title_colors,
        LIST_TITLE_COLORS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, SERVICE_RESYNC, _resync, RESYNC_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_CACHE,
        _clear_cache,
        CLEAR_CACHE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
