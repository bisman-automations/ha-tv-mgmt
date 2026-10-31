"""Websocket API used by the TV Mgmt sidebar panel."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import wraps
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .access import get_access
from .activity import hide_adult_detail, overlap_seconds, subtract_periods, summarize
from .apps import ACTIONS as APP_ACTIONS, APP_MODES
from .const import (
    CONF_ADULT_MODE_COUNTS,
    CONF_ADULT_MODE_DURATION,
    CONF_APP_ACTION,
    CONF_APP_LIMITS,
    CONF_APP_MODE,
    CONF_APPS,
    CONF_BLOCKED_SHOWS,
    CONF_DAILY_BUDGET,
    CONF_INPUT_NAMES,
    CONF_QUIET_WINDOWS,
    CONF_SLEEP_ON_BLOCK,
    CONF_TV_LIGHTS,
    CONF_TV_LIGHTS_FOLLOW,
    CONF_WAKE_WITH_TV,
    CONF_WARN_MINUTES,
    DEFAULT_QUIET_WINDOWS,
    DEFAULT_TV_LIGHTS_FOLLOW,
    DOMAIN,
    SECTION_APPLE_TV,
    SECTION_INPUT_LOCK,
    SECTION_LIGHTS,
    SECTION_SCREEN_TIME,
    SIGNAL_ANY_UPDATED,
)
from .home import (
    ACTION_TURN_OFF as HOME_ACTION_TURN_OFF,
    ACTIONS as HOME_ACTIONS,
    WEEK_STARTS,
    HomeSettings,
)
from .home_limits import get_home, notify_targets
from .manager import TVManager
from .media import describe
from .messages import MAX_LENGTH as MAX_MESSAGE_LENGTH, PRESETS as MESSAGE_PRESETS
from .names import clean_names
from .quiet import parse_windows
from .remote_keys import DEVICE_APPLE_TV, DEVICE_TV, available_keys, presses
from .state import MODES

_LOGGER = logging.getLogger(__name__)

ACTIONS = [
    "grant_extension",
    "force_block",
    "unblock",
    "reset_usage",
    "set_mode",
    "set_adult_mode",
    "set_input_lock",
]


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in (
        ws_profiles,
        ws_activity,
        ws_analytics,
        ws_limits_get,
        ws_limits_set,
        ws_input_names_set,
        ws_apple_tv_set,
        ws_lights_set,
        ws_lights_off,
        ws_action,
        ws_remote,
        ws_message,
        ws_subscribe,
        ws_access_me,
        ws_access_get,
        ws_access_set,
        ws_home_get,
        ws_home_set,
        ws_home_extension,
    ):
        websocket_api.async_register_command(hass, command)
    from .light_sync.websocket import COMMANDS as LIGHT_SYNC_COMMANDS

    for command in LIGHT_SYNC_COMMANDS:
        websocket_api.async_register_command(hass, command)


# ---- helpers -------------------------------------------------------------------


def require_access(func):
    """Only admins and the parents an admin allowed may use the sidebar app."""

    @wraps(func)
    def with_access(hass: HomeAssistant, connection, msg) -> None:
        if not get_access(hass).allows(connection.user):
            raise Unauthorized
        func(hass, connection, msg)

    return with_access


def _managers(hass: HomeAssistant) -> list[TVManager]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _manager(hass: HomeAssistant, connection, msg) -> TVManager | None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
    ):
        connection.send_error(
            msg["id"], "not_found", "TV Mgmt profile not found or not loaded"
        )
        return None
    return entry.runtime_data


def profile_summary(manager: TVManager) -> dict[str, Any]:
    backend = manager.backend
    state = manager.state
    decision = manager.decision
    guard = manager.guard
    on = backend.is_on
    source = backend.current_source if on else None
    until = state.adult_mode_until_dt if manager.adult_mode_active else None
    return {
        "entry_id": manager.entry.entry_id,
        "name": manager.name,
        "tv_entity_id": manager.tv_entity_id,
        "adapter": backend.kind,
        "is_on": on,
        "current_input": source,
        "input_allowed": guard.is_allowed(source) if on else None,
        "allowed_inputs": guard.allowed_sources,
        "target_input": guard.target_source,
        "input_lock": state.input_lock,
        "lock_paused_reason": guard.paused_reason,
        "state": decision.state,
        "reason": decision.reason,
        "quiet_window": decision.quiet_window,
        "remaining_seconds": decision.remaining_seconds,
        # Which limit remaining_seconds is: "budget" (the TV's), "home_daily" or "home_weekly".
        "limit": decision.limit,
        "mode": state.mode,
        "force_block": state.force_block,
        "adult_mode_until": until.isoformat() if until else None,
        "adult_mode_duration": manager.adult_mode_duration,
        "adult_mode_counts": manager.adult_mode_counts,
        # Time on in adult mode today. Part of used_seconds only when it counts.
        "adult_seconds": state.adult_seconds,
        "used_seconds": state.used_seconds,
        "budget_minutes": manager.daily_budget,
        "extension_minutes": state.extension_minutes,
        "warn_minutes": manager.warn_minutes,
        "blocked_switches": state.blocked_switches,
        "last_blocked_input": state.last_blocked_source,
        "last_blocked_at": state.last_blocked_at,
        "mode_sync_entity": manager.mode_sync.entity_id if manager.mode_sync else None,
        "quiet_windows": [w.format() for w in manager.quiet_windows],
        # Display names for every input this TV has reported (raw -> name).
        "input_names": {raw: manager.name_for(raw) for raw in manager.known_inputs()},
        "custom_input_names": dict(manager.input_names.user),
        "remote_keys": available_keys(manager.hass, manager.tv_entity_id),
        "entities": profile_entities(manager),
        "message_targets": manager.message_targets(),
        "message_presets": MESSAGE_PRESETS,
        "tv_lights": manager.tv_lights,
        "tv_lights_follow": manager.tv_lights_follow,
        # Turned off from here or the button; light sync leaves them off for now.
        "tv_lights_held_off": manager.tv_lights_held_off,
        # Lights around the TV, for the glow around the TV on the dashboard.
        "ambient_lights": ambient_lights(manager),
        "apple_tv": apple_tv_summary(manager),
    }


def ambient_lights(manager: TVManager) -> list[str]:
    """The TV lights and the lights of the TV's turned-on light zones, once each."""
    lights = list(manager.tv_lights or [])
    light_sync = manager.light_sync
    if light_sync is not None:
        for zone in light_sync.zones.values():
            if zone.data.enabled:
                lights.extend(zone.configured_lights)
    return list(dict.fromkeys(lights))


def profile_entities(manager: TVManager) -> list[dict[str, str]]:
    """TV Mgmt's own entities for this profile, so the panel can link to them."""
    registry = er.async_get(manager.hass)
    return [
        {"entity_id": entry.entity_id, "key": entry.translation_key or entry.domain}
        for entry in er.async_entries_for_config_entry(registry, manager.entry.entry_id)
        if not entry.disabled
    ]


def apple_tv_summary(manager: TVManager) -> dict[str, Any] | None:
    box = manager.box
    if box is None:
        return None
    state = manager.state
    ranked = sorted(state.app_seconds.items(), key=lambda item: item[1], reverse=True)
    return {
        **box.summary(),
        "remote_keys": available_keys(manager.hass, box.entity_id),
        "apps_today": [
            {
                "app": app,
                "name": manager.app_name_for(app),
                "seconds": secs,
                "limit_minutes": box.rules.limit_for(app, manager.app_name_for(app)),
            }
            for app, secs in ranked
        ],
        "app_seconds_today": sum(state.app_seconds.values()),
        # Shows, movies and songs played today, most first.
        "media_today": [
            {"show": show, "seconds": secs, "app": state.media_apps.get(show)}
            for show, secs in sorted(
                state.media_seconds.items(), key=lambda item: item[1], reverse=True
            )
        ],
        "now_watching": describe(box.media),
        "apps_stopped": state.apps_stopped,
        "last_stopped_app": manager.app_name_for(state.last_stopped_app),
        "last_stopped_at": state.last_stopped_at,
        "known_apps": manager.known_apps(),
        "known_shows": manager.known_shows(),
        "rules": {
            "mode": box.rules.mode,
            "apps": list(box.rules.apps),
            "limits": dict(box.rules.limits),
            "action": box.action,
            "sleep_on_block": manager.sleep_on_block,
            "wake_with_tv": manager.wake_with_tv,
            "blocked_shows": list(box.blocked_shows),
        },
    }


def _parse_day(raw: str | None) -> date:
    if not raw:
        return dt_util.now().date()
    return date.fromisoformat(raw)


def _local_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = dt_util.start_of_local_day(day)
    end = dt_util.start_of_local_day(day + timedelta(days=1))
    return start, end


# ---- read commands -------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/profiles"})
@require_access
@callback
def ws_profiles(hass: HomeAssistant, connection, msg) -> None:
    managers = sorted(_managers(hass), key=lambda m: m.name.lower())
    home = get_home(hass)
    connection.send_result(
        msg["id"],
        {
            "profiles": [profile_summary(m) for m in managers],
            "home": home.summary() if home else None,
            "now": dt_util.utcnow().isoformat(),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/activity",
        vol.Required("entry_id"): str,
        vol.Optional("date"): str,
    }
)
@require_access
@callback
def ws_activity(hass: HomeAssistant, connection, msg) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    try:
        day = _parse_day(msg.get("date"))
    except ValueError:
        connection.send_error(msg["id"], "invalid_format", "date must be YYYY-MM-DD")
        return
    start, end = _local_day_bounds(day)
    now = dt_util.utcnow()
    log = manager.activity
    segments = log.viewing_segments(start, end, now)
    # Adult mode shows as its own stretches, without the apps or titles in it.
    adult = log.adult_periods(start, end, now)
    app_segments = subtract_periods(log.app_segments(start, end, now), adult)
    record = log.daily.get(day.isoformat())
    connection.send_result(
        msg["id"],
        {
            "entry_id": manager.entry.entry_id,
            "date": day.isoformat(),
            "today": dt_util.now().date().isoformat(),
            "first_date": log.first_day,
            "events": list(
                reversed(hide_adult_detail(log.events_between(start, end), adult))
            ),
            "segments": segments,
            "viewing_seconds": sum(s["seconds"] for s in segments),
            "app_segments": app_segments,
            "adult_periods": adult,
            # The part of viewing_seconds in adult mode.
            "adult_viewing_seconds": overlap_seconds(segments, adult),
            "totals": record,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/analytics",
        vol.Optional("entry_id"): str,
        vol.Optional("days", default=7): vol.All(int, vol.Range(min=1, max=366)),
    }
)
@require_access
@callback
def ws_analytics(hass: HomeAssistant, connection, msg) -> None:
    today = dt_util.now().date()
    if "entry_id" in msg:
        if (manager := _manager(hass, connection, msg)) is None:
            return
        managers = [manager]
    else:
        managers = _managers(hass)
    result = []
    for manager in sorted(managers, key=lambda m: m.name.lower()):
        series = manager.activity.daily_series(today, msg["days"])
        result.append(
            {
                "entry_id": manager.entry.entry_id,
                "name": manager.name,
                "days": series,
                "summary": _named_summary(manager, summarize(series)),
                "has_apple_tv": manager.box is not None,
            }
        )
    connection.send_result(msg["id"], {"profiles": result, "days": msg["days"]})


def _named_summary(manager: TVManager, summary: dict[str, Any]) -> dict[str, Any]:
    for item in summary.get("top_apps", []):
        item["name"] = manager.app_name_for(item["app"])
    for item in summary.get("top_media", []):
        item["app"] = manager.state.media_apps.get(item["show"])
    return summary


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/limits/get", vol.Required("entry_id"): str}
)
@require_access
@callback
def ws_limits_get(hass: HomeAssistant, connection, msg) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(
        msg["id"],
        {
            CONF_DAILY_BUDGET: manager.daily_budget,
            CONF_WARN_MINUTES: manager.warn_minutes,
            CONF_QUIET_WINDOWS: manager.options.get(
                CONF_QUIET_WINDOWS, DEFAULT_QUIET_WINDOWS
            ),
            CONF_ADULT_MODE_DURATION: manager.adult_mode_duration,
            CONF_ADULT_MODE_COUNTS: manager.adult_mode_counts,
        },
    )


# ---- write commands (admin only) ---------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/limits/set",
        vol.Required("entry_id"): str,
        vol.Optional(CONF_DAILY_BUDGET): vol.All(int, vol.Range(min=0, max=1440)),
        vol.Optional(CONF_WARN_MINUTES): vol.All(int, vol.Range(min=0, max=60)),
        vol.Optional(CONF_QUIET_WINDOWS): str,
        vol.Optional(CONF_ADULT_MODE_DURATION): vol.All(int, vol.Range(min=5, max=720)),
        vol.Optional(CONF_ADULT_MODE_COUNTS): bool,
    }
)
@require_access
@callback
def ws_limits_set(hass: HomeAssistant, connection, msg) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    changes: dict[str, Any] = {
        key: msg[key]
        for key in (
            CONF_DAILY_BUDGET,
            CONF_WARN_MINUTES,
            CONF_ADULT_MODE_DURATION,
            CONF_ADULT_MODE_COUNTS,
        )
        if key in msg
    }
    if CONF_QUIET_WINDOWS in msg:
        try:
            windows = parse_windows(msg[CONF_QUIET_WINDOWS])
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_format", f"Quiet windows: {err}")
            return
        changes[CONF_QUIET_WINDOWS] = ", ".join(w.format() for w in windows)

    entry = manager.entry
    options = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in entry.options.items()
    }
    options.setdefault(SECTION_SCREEN_TIME, {}).update(changes)
    # The update listener applies the new limits to the running TV (no reload).
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": changes})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/input_names/set",
        vol.Required("entry_id"): str,
        vol.Required("names"): {str: str},
    }
)
@require_access
@callback
def ws_input_names_set(hass: HomeAssistant, connection, msg) -> None:
    """Replace this TV's custom input names. Blank names are removed."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    names = clean_names(msg["names"])
    entry = manager.entry
    options = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in entry.options.items()
    }
    options.setdefault(SECTION_INPUT_LOCK, {})[CONF_INPUT_NAMES] = names
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": names})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/lights/set",
        vol.Required("entry_id"): str,
        vol.Optional("lights"): [vol.All(str, vol.Match(r"^light\.[a-z0-9_]+$"))],
        vol.Optional("follow"): bool,
    }
)
@require_access
@callback
def ws_lights_set(hass: HomeAssistant, connection, msg) -> None:
    """Pick the lights that go on and off with this TV, and whether they do."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    changes: dict[str, Any] = {}
    if "lights" in msg:
        changes[CONF_TV_LIGHTS] = list(dict.fromkeys(msg["lights"]))
    if "follow" in msg:
        changes[CONF_TV_LIGHTS_FOLLOW] = msg["follow"]
    entry = manager.entry
    options = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in entry.options.items()
    }
    section = options.setdefault(SECTION_LIGHTS, {})
    section.update(changes)
    hass.config_entries.async_update_entry(
        entry, options=options
    )  # applied live, no reload
    connection.send_result(
        msg["id"],
        {
            "lights": list(section.get(CONF_TV_LIGHTS) or []),
            "follow": bool(
                section.get(CONF_TV_LIGHTS_FOLLOW, DEFAULT_TV_LIGHTS_FOLLOW)
            ),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/lights/off",
        vol.Required("entry_id"): str,
    }
)
@require_access
@websocket_api.async_response
async def ws_lights_off(hass: HomeAssistant, connection, msg) -> None:
    """Turn the TV lights off now. Reports any light that didn't answer."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    result = await manager.async_turn_off_tv_lights()
    connection.send_result(msg["id"], {**result, "profile": profile_summary(manager)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/apple_tv/set",
        vol.Required("entry_id"): str,
        vol.Optional("mode"): vol.In(APP_MODES),
        vol.Optional("apps"): [str],
        vol.Optional("limits"): {str: vol.All(int, vol.Range(min=0, max=1440))},
        vol.Optional("action"): vol.In(APP_ACTIONS),
        vol.Optional("sleep_on_block"): bool,
        vol.Optional("wake_with_tv"): bool,
        vol.Optional("blocked_shows"): [str],
    }
)
@require_access
@callback
def ws_apple_tv_set(hass: HomeAssistant, connection, msg) -> None:
    """Change this TV's Apple TV app rules (not which Apple TV it is)."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if manager.box is None:
        connection.send_error(
            msg["id"], "not_supported", "This TV has no Apple TV linked"
        )
        return
    changes: dict[str, Any] = {}
    if "mode" in msg:
        changes[CONF_APP_MODE] = msg["mode"]
    if "apps" in msg:
        changes[CONF_APPS] = list(
            dict.fromkeys(a.strip() for a in msg["apps"] if a.strip())
        )
    if "limits" in msg:
        # 0 or blank means no limit for that app.
        changes[CONF_APP_LIMITS] = {
            app.strip(): minutes
            for app, minutes in msg["limits"].items()
            if app.strip() and minutes
        }
    if "action" in msg:
        changes[CONF_APP_ACTION] = msg["action"]
    if "sleep_on_block" in msg:
        changes[CONF_SLEEP_ON_BLOCK] = msg["sleep_on_block"]
    if "wake_with_tv" in msg:
        changes[CONF_WAKE_WITH_TV] = msg["wake_with_tv"]
    if "blocked_shows" in msg:
        changes[CONF_BLOCKED_SHOWS] = list(
            dict.fromkeys(s.strip() for s in msg["blocked_shows"] if s.strip())
        )
    entry = manager.entry
    options = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in entry.options.items()
    }
    options.setdefault(SECTION_APPLE_TV, {}).update(changes)
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": changes})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/action",
        vol.Required("entry_id"): str,
        vol.Required("action"): vol.In(ACTIONS),
        vol.Optional("minutes"): vol.All(int, vol.Range(min=-240, max=720)),
        vol.Optional("mode"): vol.In(MODES),
        vol.Optional("enabled"): bool,
    }
)
@require_access
@callback
def ws_action(hass: HomeAssistant, connection, msg) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    action = msg["action"]
    try:
        if action == "grant_extension":
            manager.grant_extension(msg["minutes"])
        elif action == "force_block":
            manager.force_block()
        elif action == "unblock":
            manager.unblock()
        elif action == "reset_usage":
            manager.reset_usage()
        elif action == "set_mode":
            manager.set_mode(msg["mode"])
        elif action == "set_adult_mode":
            manager.set_adult_mode(msg["enabled"], msg.get("minutes"))
        elif action == "set_input_lock":
            manager.set_input_lock(msg["enabled"])
    except KeyError as err:
        connection.send_error(
            msg["id"], "invalid_format", f"{action} needs {err.args[0]}"
        )
        return
    connection.send_result(msg["id"], {"profile": profile_summary(manager)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/remote",
        vol.Required("entry_id"): str,
        vol.Required("device"): vol.In([DEVICE_TV, DEVICE_APPLE_TV]),
        vol.Required("key"): str,
    }
)
@require_access
@websocket_api.async_response
async def ws_remote(hass: HomeAssistant, connection, msg) -> None:
    """Press a remote button on the TV or its Apple TV."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if msg["device"] == DEVICE_APPLE_TV:
        entity_id = manager.box.entity_id if manager.box else None
    else:
        entity_id = manager.tv_entity_id
    press = presses(hass, entity_id).get(msg["key"]) if entity_id else None
    if press is None:
        connection.send_error(
            msg["id"], "not_supported", f"The {msg['device']} can't press {msg['key']}"
        )
        return
    try:
        await hass.services.async_call(
            press.domain,
            press.service,
            press.data,
            blocking=True,
            context=connection.context(msg),
        )
    except Exception as err:  # noqa: BLE001 - report any failure to the panel
        connection.send_error(msg["id"], "press_failed", str(err))
        return
    connection.send_result(
        msg["id"], {"key": msg["key"], "service": f"{press.domain}.{press.service}"}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/message",
        vol.Required("entry_id"): str,
        vol.Required("text"): vol.All(
            str, vol.Strip, vol.Length(min=1, max=MAX_MESSAGE_LENGTH)
        ),
        vol.Optional("apple_tv", default=False): bool,
        vol.Optional("screen", default=False): bool,
        vol.Optional("speak", default=False): bool,
    }
)
@require_access
@websocket_api.async_response
async def ws_message(hass: HomeAssistant, connection, msg) -> None:
    """Send a message to the TV: on the Apple TV, the TV screen, or speakers."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    targets = manager.message_targets()
    wanted = {key: msg[key] and targets[key] for key in ("apple_tv", "screen", "speak")}
    if not any(wanted.values()):
        connection.send_error(
            msg["id"], "not_supported", "Pick where to send the message"
        )
        return
    try:
        sent = await manager.async_send_message(
            msg["text"], **wanted, by=connection.user.name if connection.user else None
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "send_failed", str(err))
        return
    except Exception as err:  # noqa: BLE001 - tell the parent what went wrong
        _LOGGER.exception("TV Mgmt couldn't send a message")
        connection.send_error(
            msg["id"], "send_failed", f"Couldn't send the message: {err!r}"
        )
        return
    connection.send_result(msg["id"], {"sent": sent})


# ---- whole-home limits ------------------------------------------------------------


def _home_result(hass: HomeAssistant) -> dict[str, Any]:
    home = get_home(hass)
    return {
        "home": home.summary(),
        "notify_targets": notify_targets(hass),
        "tvs": [
            {"entry_id": m.entry.entry_id, "name": m.name}
            for m in sorted(_managers(hass), key=lambda m: m.name.lower())
        ],
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/home/get"})
@require_access
@callback
def ws_home_get(hass: HomeAssistant, connection, msg) -> None:
    connection.send_result(msg["id"], _home_result(hass))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/home/set",
        vol.Required("daily_minutes"): vol.All(int, vol.Range(min=0, max=1440)),
        vol.Required("weekly_minutes"): vol.All(int, vol.Range(min=0, max=7 * 1440)),
        vol.Optional("week_start", default="monday"): vol.In(list(WEEK_STARTS)),
        vol.Optional("warn_minutes", default=10): vol.All(
            int, vol.Range(min=0, max=120)
        ),
        vol.Optional("action", default=HOME_ACTION_TURN_OFF): vol.In(HOME_ACTIONS),
        vol.Optional("notify", default=[]): [str],
        vol.Optional("excluded", default=[]): [str],
    }
)
@require_access
@callback
def ws_home_set(hass: HomeAssistant, connection, msg) -> None:
    """Save the whole home's limits. Every TV applies them straight away."""
    try:
        settings = HomeSettings.from_dict(
            {k: v for k, v in msg.items() if k not in ("id", "type")}
        )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    get_home(hass).set_settings(settings)
    connection.send_result(msg["id"], _home_result(hass))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/home/extension",
        vol.Required("minutes"): vol.All(int, vol.Range(min=-240, max=720)),
    }
)
@require_access
@callback
def ws_home_extension(hass: HomeAssistant, connection, msg) -> None:
    """Give the whole home extra time today (negative takes time away)."""
    get_home(hass).grant_extension(
        msg["minutes"], by=connection.user.name if connection.user else None
    )
    connection.send_result(msg["id"], {"home": get_home(hass).summary()})


# ---- access ---------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/access/me"})
@callback
def ws_access_me(hass: HomeAssistant, connection, msg) -> None:
    """Whether the person viewing may use the app, so it can say so."""
    user = connection.user
    connection.send_result(
        msg["id"],
        {
            "allowed": get_access(hass).allows(user),
            "is_admin": bool(user and user.is_admin),
            "user_id": user.id if user else None,
            "name": user.name if user else None,
        },
    )


async def _people(hass: HomeAssistant) -> list[dict[str, Any]]:
    users = [
        u
        for u in await hass.auth.async_get_users()
        if not u.system_generated and u.is_active
    ]
    return [
        {
            "id": u.id,
            "name": u.name or u.id,
            "is_admin": u.is_admin,
            "is_owner": u.is_owner,
        }
        for u in sorted(users, key=lambda u: (u.name or "").casefold())
    ]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/access/get"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_access_get(hass: HomeAssistant, connection, msg) -> None:
    connection.send_result(
        msg["id"],
        {"user_ids": list(get_access(hass).user_ids), "people": await _people(hass)},
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/access/set", vol.Required("user_ids"): [str]}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_access_set(hass: HomeAssistant, connection, msg) -> None:
    from .panel import async_register_panel

    access = get_access(hass)
    await access.async_set(msg["user_ids"])
    await async_register_panel(hass, update=True)
    connection.send_result(
        msg["id"], {"user_ids": list(access.user_ids), "people": await _people(hass)}
    )


# ---- live updates -------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@require_access
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg) -> None:
    """Send an event whenever any profile changes, carrying its entry_id."""

    @callback
    def forward(entry_id: str) -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], {"entry_id": entry_id})
        )

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, SIGNAL_ANY_UPDATED, forward
    )
    connection.send_result(msg["id"])
