"""Diagnostics for TV Mgmt."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TVMgmtConfigEntry
from .const import CONF_ANNOUNCE_PLAYERS, CONF_ANNOUNCE_SCREEN
from .home_limits import get_home


# Picture URLs carry an access token that works until Home Assistant restarts,
# and notify and speaker targets are often named after people.
TO_REDACT = {
    # Light sync: TMDB / Trakt keys (and the request headers that carry them).
    "tmdb_api_key",
    "trakt_client_id",
    "api_key",
    "trakt-api-key",
    "Authorization",
    "applied_signature",
    "access_token",
    "token",
    "entity_picture",
    "entity_picture_local",
    "media_image_url",
    "notify",
    CONF_ANNOUNCE_SCREEN,
    CONF_ANNOUNCE_PLAYERS,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TVMgmtConfigEntry
) -> dict[str, Any]:
    manager = entry.runtime_data
    backend = manager.backend
    tv_state = hass.states.get(manager.tv_entity_id)
    return async_redact_data(_diagnostics(hass, entry, manager, backend, tv_state), TO_REDACT)


def _diagnostics(hass: HomeAssistant, entry: TVMgmtConfigEntry, manager, backend, tv_state) -> dict[str, Any]:
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "profile_state": asdict(manager.state),
        "decision": asdict(manager.decision),
        "adapter": {
            "kind": backend.kind,
            "reports_source": backend.reports_source,
            "is_on": backend.is_on,
            "current_source": backend.current_source,
            "source_list": backend.source_list,
            "target_list": backend.target_list,
        },
        "input_lock": {
            "allowed": manager.guard.allowed_sources,
            "target": manager.guard.target_source,
            "paused_reason": manager.guard.paused_reason,
        },
        "quiet_windows": [w.format() for w in manager.quiet_windows],
        "adult_mode": {
            "active": manager.adult_mode_active,
            "counts_toward_limits": manager.adult_mode_counts,
            "seconds_today": manager.state.adult_seconds,
            "counted_seconds_today": manager.state.adult_counted_seconds,
        },
        "tv_lights": {
            "count": len(manager.tv_lights),
            "follow_tv": manager.tv_lights_follow,
            "held_off": manager.tv_lights_held_off,
        },
        "home": (home.summary() if (home := get_home(hass)) else None),
        "mode_sync": (
            {"entity_id": manager.mode_sync.entity_id, "linked_mode": manager.mode_sync.linked_mode}
            if manager.mode_sync
            else None
        ),
        "apple_tv": (
            {
                **manager.box.summary(),
                "rules": {
                    "mode": manager.box.rules.mode,
                    "apps": manager.box.rules.apps,
                    "limits": manager.box.rules.limits,
                    "action": manager.box.action,
                },
                "box_state": (
                    s.as_dict() if (s := hass.states.get(manager.box.entity_id)) else None
                ),
            }
            if manager.box
            else None
        ),
        "tv_entity_state": tv_state.as_dict() if tv_state else None,
        "light_sync": _light_sync(hass, manager),
    }


def _light_sync(hass: HomeAssistant, manager) -> dict[str, Any] | None:
    from .light_sync.storage import DATA_LIGHT_SYNC

    data = hass.data.get(DATA_LIGHT_SYNC)
    if data is None or manager.light_sync is None:
        return None

    def _state(entity_id: str) -> dict[str, Any] | None:
        state = hass.states.get(entity_id)
        return {"state": state.state, "attributes": dict(state.attributes)} if state else None

    return {
        "settings": data.config.settings,
        "zones": [
            {
                **zone.as_diagnostics(),
                "player_states": {e: _state(e) for e in zone.media_players},
                "light_states": {e: _state(e) for e in zone.configured_lights},
            }
            for zone in manager.light_sync.zones.values()
        ],
        "title_mappings": len(data.titles),
        "cache": data.cache.stats(),
        "metadata": (
            data.metadata_service.as_diagnostics() if data.metadata_service else None
        ),
    }
