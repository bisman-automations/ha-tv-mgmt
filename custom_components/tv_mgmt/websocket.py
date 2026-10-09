"""Websocket API used by the TV Mgmt sidebar panel."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import voluptuous as vol

from functools import wraps

from homeassistant.components import websocket_api
from homeassistant.exceptions import Unauthorized
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .access import get_access
from .activity import summarize
from .const import (
    CONF_ADULT_MODE_DURATION,
    CONF_APP_ACTION,
    CONF_APP_LIMITS,
    CONF_APP_MODE,
    CONF_APPS,
    CONF_DAILY_BUDGET,
    CONF_INPUT_NAMES,
    CONF_QUIET_WINDOWS,
    CONF_SLEEP_ON_BLOCK,
    CONF_WAKE_WITH_TV,
    CONF_WARN_MINUTES,
    DEFAULT_QUIET_WINDOWS,
    DOMAIN,
    SECTION_APPLE_TV,
    SECTION_INPUT_LOCK,
    SECTION_SCREEN_TIME,
    SIGNAL_ANY_UPDATED,
)
from .manager import TVManager
from .apps import ACTIONS as APP_ACTIONS, APP_MODES
from .media import describe
from .names import clean_names
from .quiet import parse_windows
from .remote_keys import DEVICE_APPLE_TV, DEVICE_TV, available_keys, presses
from .state import MODES

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
        ws_action,
        ws_remote,
        ws_subscribe,
        ws_access_me,
        ws_access_get,
        ws_access_set,
    ):
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
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], "not_found", "TV Mgmt profile not found or not loaded")
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
        "mode": state.mode,
        "force_block": state.force_block,
        "adult_mode_until": until.isoformat() if until else None,
        "adult_mode_duration": manager.adult_mode_duration,
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
        "apple_tv": apple_tv_summary(manager),
    }


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
            for show, secs in sorted(state.media_seconds.items(), key=lambda item: item[1], reverse=True)
        ],
        "now_watching": describe(box.media),
        "apps_stopped": state.apps_stopped,
        "last_stopped_app": manager.app_name_for(state.last_stopped_app),
        "last_stopped_at": state.last_stopped_at,
        "known_apps": manager.known_apps(),
        "rules": {
            "mode": box.rules.mode,
            "apps": list(box.rules.apps),
            "limits": dict(box.rules.limits),
            "action": box.action,
            "sleep_on_block": manager.sleep_on_block,
            "wake_with_tv": manager.wake_with_tv,
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
    connection.send_result(
        msg["id"],
        {"profiles": [profile_summary(m) for m in managers], "now": dt_util.utcnow().isoformat()},
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
    app_segments = log.app_segments(start, end, now)
    record = log.daily.get(day.isoformat())
    connection.send_result(
        msg["id"],
        {
            "entry_id": manager.entry.entry_id,
            "date": day.isoformat(),
            "today": dt_util.now().date().isoformat(),
            "first_date": log.first_day,
            "events": list(reversed(log.events_between(start, end))),
            "segments": segments,
            "viewing_seconds": sum(s["seconds"] for s in segments),
            "app_segments": app_segments,
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
            CONF_QUIET_WINDOWS: manager.options.get(CONF_QUIET_WINDOWS, DEFAULT_QUIET_WINDOWS),
            CONF_ADULT_MODE_DURATION: manager.adult_mode_duration,
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
    }
)
@require_access
@callback
def ws_limits_set(hass: HomeAssistant, connection, msg) -> None:
    if (manager := _manager(hass, connection, msg)) is None:
        return
    changes: dict[str, Any] = {
        key: msg[key]
        for key in (CONF_DAILY_BUDGET, CONF_WARN_MINUTES, CONF_ADULT_MODE_DURATION)
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
    options = {key: (dict(value) if isinstance(value, dict) else value) for key, value in entry.options.items()}
    options.setdefault(SECTION_SCREEN_TIME, {}).update(changes)
    # Saving options reloads the profile with the new limits.
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
    options = {key: (dict(value) if isinstance(value, dict) else value) for key, value in entry.options.items()}
    options.setdefault(SECTION_INPUT_LOCK, {})[CONF_INPUT_NAMES] = names
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"saved": names})


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
    }
)
@require_access
@callback
def ws_apple_tv_set(hass: HomeAssistant, connection, msg) -> None:
    """Change this TV's Apple TV app rules (not which Apple TV it is)."""
    if (manager := _manager(hass, connection, msg)) is None:
        return
    if manager.box is None:
        connection.send_error(msg["id"], "not_supported", "This TV has no Apple TV linked")
        return
    changes: dict[str, Any] = {}
    if "mode" in msg:
        changes[CONF_APP_MODE] = msg["mode"]
    if "apps" in msg:
        changes[CONF_APPS] = list(dict.fromkeys(a.strip() for a in msg["apps"] if a.strip()))
    if "limits" in msg:
        # 0 or blank means no limit for that app.
        changes[CONF_APP_LIMITS] = {
            app.strip(): minutes for app, minutes in msg["limits"].items() if app.strip() and minutes
        }
    if "action" in msg:
        changes[CONF_APP_ACTION] = msg["action"]
    if "sleep_on_block" in msg:
        changes[CONF_SLEEP_ON_BLOCK] = msg["sleep_on_block"]
    if "wake_with_tv" in msg:
        changes[CONF_WAKE_WITH_TV] = msg["wake_with_tv"]
    entry = manager.entry
    options = {key: (dict(value) if isinstance(value, dict) else value) for key, value in entry.options.items()}
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
        connection.send_error(msg["id"], "invalid_format", f"{action} needs {err.args[0]}")
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
            press.domain, press.service, press.data, blocking=True, context=connection.context(msg)
        )
    except Exception as err:  # noqa: BLE001 - report any failure to the panel
        connection.send_error(msg["id"], "press_failed", str(err))
        return
    connection.send_result(msg["id"], {"key": msg["key"], "service": f"{press.domain}.{press.service}"})


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
    users = [u for u in await hass.auth.async_get_users() if not u.system_generated and u.is_active]
    return [
        {"id": u.id, "name": u.name or u.id, "is_admin": u.is_admin, "is_owner": u.is_owner}
        for u in sorted(users, key=lambda u: (u.name or "").casefold())
    ]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/access/get"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_access_get(hass: HomeAssistant, connection, msg) -> None:
    connection.send_result(
        msg["id"], {"user_ids": list(get_access(hass).user_ids), "people": await _people(hass)}
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
        connection.send_message(websocket_api.event_message(msg["id"], {"entry_id": entry_id}))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, SIGNAL_ANY_UPDATED, forward
    )
    connection.send_result(msg["id"])
