"""TVManager: one per profile (TV). Tracks screen time and enforces rules."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import activity as act
from .apps import ACTION_HOME, APP_MODE_BLOCK, AppRules
from .backends import TVBackend
from .const import (
    CONF_ADULT_MODE_DURATION,
    CONF_APP_ACTION,
    CONF_APPLE_TV_INPUT,
    CONF_APP_LIMITS,
    CONF_APP_MODE,
    CONF_APPS,
    CONF_DAILY_BUDGET,
    CONF_INPUT_NAMES,
    CONF_MEDIA_PLAYER,
    CONF_MODE_SYNC_ENTITY,
    CONF_QUIET_WINDOWS,
    CONF_SLEEP_ON_BLOCK,
    CONF_STREAMING_PLAYER,
    CONF_WARN_MINUTES,
    DEFAULT_ADULT_MODE_DURATION,
    DEFAULT_DAILY_BUDGET,
    DEFAULT_SLEEP_ON_BLOCK,
    DEFAULT_WARN_MINUTES,
    EVENT_APP_BLOCKED,
    FOLLOW_DELAY,
    EVENT_ENFORCEMENT_CHANGED,
    EVENT_INPUT_BLOCKED,
    EVENT_WARNING,
    SIGNAL_ANY_UPDATED,
    SIGNAL_UPDATED,
    TICK_INTERVAL,
    TURN_OFF_COOLDOWN,
    TURN_OFF_DELAY,
)
from .guard import InputGuard
from .mode_sync import ModeSync
from .names import InputNames
from .quiet import QuietWindow, parse_windows
from .state import (
    MODE_ENFORCED,
    MODES,
    STATE_ENFORCING,
    STATE_OK,
    STATE_WARNING,
    Decision,
    decide,
)
from .storage import ActivityStore, ProfileState, ProfileStore
from .streaming import StreamingBox

_LOGGER = logging.getLogger(__name__)

# A single tick longer than this means HA was down or the clock jumped;
# don't count it as viewing.
MAX_TICK_GAP = timedelta(minutes=5)


def flatten_options(options: dict[str, Any]) -> dict[str, Any]:
    """Options are grouped into form sections; read them as one dict."""
    flat: dict[str, Any] = {}
    for key, value in options.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


class TVManager:
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, backend: TVBackend
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.backend = backend
        self.options = flatten_options(dict(entry.options))
        self.store = ProfileStore(hass, entry.entry_id)
        self.input_names = InputNames(self.options.get(CONF_INPUT_NAMES))
        self.activity_store = ActivityStore(hass, entry.entry_id)
        # Last power/input state written to the activity log.
        self._logged_on: bool | None = None
        self._logged_source: str | None = None
        self.decision = Decision(STATE_OK)

        self.guard = InputGuard(
            hass,
            entry.title,
            backend,
            self.options,
            is_active=self._lock_active,
            should_revert=self._enforcing_actions,
            on_block=self._on_input_blocked,
            on_change=self.notify,
            pinned_source=(
                self.options.get(CONF_APPLE_TV_INPUT)
                if self.options.get(CONF_STREAMING_PLAYER)
                else None
            ),
        )

        self.mode_sync: ModeSync | None = None
        if sync_entity := self.options.get(CONF_MODE_SYNC_ENTITY):
            self.mode_sync = ModeSync(
                hass,
                sync_entity,
                get_mode=lambda: self.state.mode,
                set_mode=lambda mode: self.set_mode(mode, from_sync=True),
            )

        self.box: StreamingBox | None = None
        if box_entity := self.options.get(CONF_STREAMING_PLAYER):
            self.box = StreamingBox(
                hass,
                box_entity,
                AppRules(
                    mode=self.options.get(CONF_APP_MODE) or APP_MODE_BLOCK,
                    apps=list(self.options.get(CONF_APPS) or []),
                    limits=dict(self.options.get(CONF_APP_LIMITS) or {}),
                ),
                self.options.get(CONF_APP_ACTION) or ACTION_HOME,
                rules_active=lambda: self.decision.state in (STATE_OK, STATE_WARNING),
                should_act=self._enforcing_actions,
                used_seconds=lambda app: self.state.app_seconds.get(app, 0),
                on_stop=self._on_app_stopped,
                on_update=self.notify,
            )
        self._logged_app: str | None = None
        self._box_was_on: bool | None = None
        self._unsub_follow: CALLBACK_TYPE | None = None

        self._last_tick: datetime | None = None
        self._last_turn_off: datetime | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unsub_turn_off: CALLBACK_TYPE | None = None

        try:
            self.quiet_windows: list[QuietWindow] = parse_windows(
                self.options.get(CONF_QUIET_WINDOWS, "")
            )
        except ValueError as err:
            _LOGGER.error("TV Mgmt %s: ignoring bad quiet windows: %s", entry.title, err)
            self.quiet_windows = []

    # ---- properties ------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.entry.title

    @property
    def tv_entity_id(self) -> str:
        return self.entry.data[CONF_MEDIA_PLAYER]

    @property
    def state(self) -> ProfileState:
        return self.store.state

    @property
    def daily_budget(self) -> int:
        return int(self.options.get(CONF_DAILY_BUDGET, DEFAULT_DAILY_BUDGET))

    @property
    def warn_minutes(self) -> int:
        return int(self.options.get(CONF_WARN_MINUTES, DEFAULT_WARN_MINUTES))

    @property
    def adult_mode_duration(self) -> int:
        return int(self.options.get(CONF_ADULT_MODE_DURATION, DEFAULT_ADULT_MODE_DURATION))

    @property
    def adult_mode_active(self) -> bool:
        until = self.state.adult_mode_until_dt
        return until is not None and until > dt_util.utcnow()

    def name_for(self, raw: str | None) -> str | None:
        """Display name for an input (the raw value if it has none)."""
        return self.input_names.name(raw)

    def known_inputs(self) -> list[str]:
        """Every input this TV has reported, most relevant first."""
        seen = [
            e["source"] for e in reversed(self.activity.events)
            if e.get("source") and e["type"] in (act.EV_TV_ON, act.EV_INPUT, act.EV_INPUT_BLOCKED)
        ]
        current = [self.backend.current_source] if self.backend.is_on else []
        return [
            s for s in dict.fromkeys(
                [*self.guard.allowed_sources, self.guard.target_source, *current, *seen,
                 *self.backend.source_list, *self.input_names.user]
            )
            if s
        ]

    @property
    def sleep_on_block(self) -> bool:
        return bool(self.options.get(CONF_SLEEP_ON_BLOCK, DEFAULT_SLEEP_ON_BLOCK))

    def app_name_for(self, app_id: str | None) -> str | None:
        if app_id is None:
            return None
        return self.state.known_apps.get(app_id, app_id)

    def known_apps(self) -> dict[str, str]:
        """Apps this Apple TV has opened or lists (ID or name -> name)."""
        apps = dict(self.state.known_apps)
        if self.box:
            named = set(apps.values())
            for name in self.box.source_list:
                if name not in named:
                    apps[name] = name
        return apps

    def _enforcing_actions(self) -> bool:
        """Act on rules (enforced) vs. only report them (monitor only)."""
        return self.state.mode == MODE_ENFORCED

    def _lock_active(self) -> bool:
        return (
            self.state.input_lock
            and self.decision.state in (STATE_OK, STATE_WARNING)
        )

    @property
    def activity(self) -> act.ActivityLog:
        return self.activity_store.log

    # ---- lifecycle ---------------------------------------------------------------

    async def async_start(self) -> None:
        await self.store.async_load()
        await self.activity_store.async_load()
        self.activity.prune(dt_util.utcnow())
        self._logged_on, self._logged_source = self.activity.last_power
        self._logged_app = next(
            (e.get("app") for e in reversed(self.activity.events) if e["type"] == act.EV_APP),
            None,
        )
        self._rollover_if_needed(dt_util.now())
        if hasattr(self.backend, "set_assumed_source") and self.guard.target_source:
            self.backend.set_assumed_source(self.guard.target_source)

        await self.backend.async_start()
        self._unsubs.append(self.backend.add_listener(self._on_backend_update))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_tick, timedelta(seconds=TICK_INTERVAL),
                name=f"tv_mgmt tick {self.name}",
            )
        )
        self._last_tick = dt_util.utcnow()
        self._recompute(fire_events=False)
        self._log_power()
        self.guard.async_start()
        if self.mode_sync:
            self.mode_sync.async_start()
        if self.box:
            self._box_was_on = self.box.is_on
            self.box.async_start(self._on_box_update)
            self._log_app()
            self.box.evaluate()
        self._record_today()

    async def async_stop(self) -> None:
        self._count_usage()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_turn_off()
        self.guard.async_stop()
        if self.mode_sync:
            self.mode_sync.async_stop()
        if self.box:
            self.box.async_stop()
        if self._unsub_follow:
            self._unsub_follow()
            self._unsub_follow = None
        await self.backend.async_stop()
        self._record_today()
        await self.store.async_save()
        await self.activity_store.async_save()

    # ---- events ------------------------------------------------------------------

    @callback
    def _on_backend_update(self) -> None:
        self._count_usage()
        self._log_power()
        self._recompute()
        self.guard.handle_backend_update()
        self._enforce_power()
        self.notify()

    @callback
    def _on_box_update(self) -> None:
        self._count_usage()  # credit time to the app that was open until now
        if self.box and self.box.refresh_app():
            self._log_app()
        if self.box:
            woke = self.box.is_on and self._box_was_on is False
            self._box_was_on = self.box.is_on
            if woke:
                self._schedule_follow()
        self._recompute()
        if self.box:
            self.box.evaluate()
        self._enforce_power()
        self.notify()

    # ---- switch to the Apple TV when it wakes ------------------------------------------

    @callback
    def _schedule_follow(self) -> None:
        if not self.guard.pinned_source or self._unsub_follow:
            return
        # Give HDMI-CEC a moment: the Apple TV often switches the TV itself.
        self._unsub_follow = async_call_later(self.hass, FOLLOW_DELAY, self._follow_apple_tv)

    @callback
    def _follow_apple_tv(self, _now: datetime) -> None:
        self._unsub_follow = None
        pinned = self.guard.pinned_source
        if not (
            pinned
            and self.box
            and self.box.is_on
            and self.backend.is_on
            and self._lock_active()
            and self._enforcing_actions()
        ):
            return
        if self.backend.current_source == pinned:
            return
        target = self.guard.target_source
        if not target:
            return
        _LOGGER.info("TV Mgmt %s: Apple TV woke, switching TV to %s", self.name, target)
        self._log(act.EV_FOLLOW, source=self.backend.current_source, target=target)
        self.hass.async_create_task(self.backend.async_select_source(target))
        self.notify()

    @callback
    def _on_app_stopped(
        self, app_id: str, name: str | None, reason: str, action: str, acted: bool
    ) -> None:
        now = dt_util.utcnow()
        self.state.apps_stopped += 1
        self.state.last_stopped_app = app_id
        self.state.last_stopped_at = now.isoformat()
        self.store.schedule_save()
        self._log(act.EV_APP_STOPPED, app=app_id, name=name, reason=reason, action=action, acted=acted)
        self.hass.bus.async_fire(
            EVENT_APP_BLOCKED,
            {
                "profile_id": self.entry.entry_id,
                "tv": self.name,
                ATTR_ENTITY_ID: self.box.entity_id if self.box else None,
                "app_id": app_id,
                "app_name": name,
                "reason": reason,
                "action": action,
                "acted": acted,
            },
        )
        self.notify()

    @callback
    def _log_app(self) -> None:
        if not self.box:
            return
        app_id, name = self.box.app_id, self.box.app_name
        if app_id and name and self.state.known_apps.get(app_id) != name:
            self.state.known_apps[app_id] = name
            self.store.schedule_save()
        if app_id != self._logged_app:
            self._log(act.EV_APP, app=app_id, name=name)
            self._logged_app = app_id

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._count_usage()
        self._recompute()
        if self.box:
            self.box.evaluate()
        self.guard.evaluate()
        self._enforce_power()
        self.notify()

    @callback
    def _on_input_blocked(self, source: str | None, target: str) -> None:
        now = dt_util.utcnow()
        self.state.blocked_switches += 1
        self.state.last_blocked_source = source
        self.state.last_blocked_at = now.isoformat()
        self.store.schedule_save()
        self._log(
            act.EV_INPUT_BLOCKED,
            source=source,
            target=target,
            reverted=self._enforcing_actions(),
        )
        self.hass.bus.async_fire(
            EVENT_INPUT_BLOCKED,
            {
                "profile_id": self.entry.entry_id,
                "tv": self.name,
                ATTR_ENTITY_ID: self.tv_entity_id,
                "blocked_source": source,
                "blocked_source_name": self.name_for(source),
                "target_source": target,
                "target_source_name": self.name_for(target),
                "reverted": self._enforcing_actions(),
            },
        )
        self.notify()

    # ---- usage + decisions ----------------------------------------------------------

    def _rollover_if_needed(self, local_now: datetime) -> None:
        today = local_now.date()
        if self.state.day != today.isoformat():
            self._record_today()  # close out the finished day
            self.state.reset_day(today)
            self.store.schedule_save()

    @callback
    def _count_usage(self) -> None:
        """Add the time since the last count if the TV was on."""
        now = dt_util.utcnow()
        last, self._last_tick = self._last_tick, now
        local_now = dt_util.as_local(now)

        tv_on = bool(self.backend.is_on)
        app = self.box.app_id if self.box else None
        if last is not None and (tv_on or app):
            elapsed = now - last
            if timedelta(0) < elapsed <= MAX_TICK_GAP:
                # Split at midnight so viewing books to the right day.
                midnight = dt_util.start_of_local_day(local_now)
                local_last = dt_util.as_local(last)
                if local_last < midnight:
                    self._credit(int((midnight - local_last).total_seconds()), tv_on, app)
                    self._rollover_if_needed(local_now)
                    elapsed = local_now - midnight
                self._credit(int(elapsed.total_seconds()), tv_on, app)
                self.store.schedule_save()

        self._rollover_if_needed(local_now)

        if self.state.adult_mode_until and not self.adult_mode_active:
            self.state.adult_mode_until = None
            self.store.schedule_save()

    def _credit(self, seconds: int, tv_on: bool, app: str | None) -> None:
        if tv_on:
            self.state.used_seconds += seconds
        if app:
            self.state.app_seconds[app] = self.state.app_seconds.get(app, 0) + seconds

    @callback
    def _recompute(self, *, fire_events: bool = True) -> None:
        previous = self.decision
        self.decision = decide(
            now=dt_util.now().time(),
            mode=self.state.mode,
            adult_mode=self.adult_mode_active,
            force_block=self.state.force_block,
            budget_minutes=self.daily_budget,
            extension_minutes=self.state.extension_minutes,
            used_seconds=self.state.used_seconds,
            warn_minutes=self.warn_minutes,
            quiet_windows=self.quiet_windows,
        )
        if not fire_events:
            return
        if (previous.state, previous.reason) != (self.decision.state, self.decision.reason):
            _LOGGER.info(
                "TV Mgmt %s: %s -> %s (%s)",
                self.name, previous.state, self.decision.state, self.decision.reason,
            )
            self._log(
                act.EV_ENFORCEMENT,
                state=self.decision.state,
                reason=self.decision.reason,
                quiet_window=self.decision.quiet_window,
            )
            self.hass.bus.async_fire(
                EVENT_ENFORCEMENT_CHANGED,
                {
                    "profile_id": self.entry.entry_id,
                    "tv": self.name,
                    "state": self.decision.state,
                    "previous_state": previous.state,
                    "reason": self.decision.reason,
                    "quiet_window": self.decision.quiet_window,
                },
            )
            if previous.state != self.decision.state:
                self.guard.reset()
        if self.decision.state == STATE_WARNING and previous.state != STATE_WARNING:
            self.hass.bus.async_fire(
                EVENT_WARNING,
                {
                    "profile_id": self.entry.entry_id,
                    "tv": self.name,
                    "remaining_minutes": round((self.decision.remaining_seconds or 0) / 60),
                },
            )

    # ---- power enforcement ------------------------------------------------------------

    @callback
    def _enforce_power(self) -> None:
        if (
            self.box
            and self.sleep_on_block
            and self.decision.state == STATE_ENFORCING
            and self._enforcing_actions()
            and self.box.sleep_if_on()
        ):
            self._log(act.EV_BOX_SLEEP, reason=self.decision.reason)
        if not (
            self.decision.state == STATE_ENFORCING
            and self._enforcing_actions()
            and self.backend.is_on
        ):
            self._cancel_turn_off()
            return
        if self._unsub_turn_off is None:
            self._unsub_turn_off = async_call_later(
                self.hass, TURN_OFF_DELAY, self._turn_off_callback
            )

    @callback
    def _turn_off_callback(self, _now: datetime) -> None:
        self._unsub_turn_off = None
        if not (self.decision.state == STATE_ENFORCING and self._enforcing_actions()):
            return
        if not self.backend.is_on:
            return
        now = dt_util.utcnow()
        if self._last_turn_off and (now - self._last_turn_off).total_seconds() < TURN_OFF_COOLDOWN:
            # Try again once the cooldown passes.
            self._unsub_turn_off = async_call_later(
                self.hass, TURN_OFF_COOLDOWN, self._turn_off_callback
            )
            return
        self._last_turn_off = now
        self._log(act.EV_TURNED_OFF, reason=self.decision.reason)
        _LOGGER.info("TV Mgmt %s: turning TV off (%s)", self.name, self.decision.reason)
        self.hass.async_create_task(
            self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: self.tv_entity_id},
                blocking=False,
            )
        )

    @callback
    def _cancel_turn_off(self) -> None:
        if self._unsub_turn_off:
            self._unsub_turn_off()
            self._unsub_turn_off = None

    # ---- parent controls ----------------------------------------------------------------

    @callback
    def _changed(self) -> None:
        """A control changed: save, re-decide and act now."""
        self.store.schedule_save()
        self._recompute()
        self.guard.reset()
        self._enforce_power()
        self.notify()

    @callback
    def set_mode(self, mode: str, *, from_sync: bool = False) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        if self.state.mode == mode:
            return
        self.state.mode = mode
        self._log(act.EV_MODE, mode=mode, synced=from_sync)
        self._changed()
        if self.mode_sync and not from_sync:
            self.mode_sync.push(mode)

    @callback
    def set_input_lock(self, enabled: bool) -> None:
        if self.state.input_lock != enabled:
            self.state.input_lock = enabled
            self._log(act.EV_INPUT_LOCK, on=enabled)
            self._changed()

    @callback
    def set_adult_mode(self, enabled: bool, minutes: int | None = None) -> None:
        if enabled:
            duration = minutes or self.adult_mode_duration
            self.state.adult_mode_until = (dt_util.utcnow() + timedelta(minutes=duration)).isoformat()
            self._log(act.EV_ADULT_MODE, on=True, minutes=duration)
        else:
            if self.adult_mode_active:
                self._log(act.EV_ADULT_MODE, on=False)
            self.state.adult_mode_until = None
        self._changed()

    @callback
    def grant_extension(self, minutes: int) -> None:
        self._count_usage()
        self.state.extension_minutes += minutes
        self._log(act.EV_EXTENSION, minutes=minutes)
        self._changed()

    @callback
    def force_block(self) -> None:
        if not self.state.force_block:
            self._log(act.EV_BLOCK)
        self.state.force_block = True
        self._changed()

    @callback
    def unblock(self) -> None:
        if self.state.force_block:
            self._log(act.EV_UNBLOCK)
        self.state.force_block = False
        self._changed()

    @callback
    def reset_usage(self) -> None:
        self.state.reset_day(dt_util.now().date())
        self._last_tick = dt_util.utcnow()
        self._log(act.EV_RESET)
        self._changed()

    # ---- output -----------------------------------------------------------------------

    @callback
    def notify(self) -> None:
        self._record_today()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED.format(self.entry.entry_id))
        async_dispatcher_send(self.hass, SIGNAL_ANY_UPDATED, self.entry.entry_id)

    # ---- activity log -------------------------------------------------------------------

    @callback
    def _log(self, event_type: str, **data: Any) -> None:
        self.activity.add(dt_util.utcnow(), event_type, **data)
        self.activity_store.schedule_save()

    @callback
    def _log_power(self) -> None:
        """Log TV on/off and input changes. Unknown (unreachable) isn't logged."""
        is_on = self.backend.is_on
        if is_on is None:
            return
        source = self.backend.current_source if is_on else None
        if is_on != self._logged_on:
            self._log(act.EV_TV_ON if is_on else act.EV_TV_OFF, **({"source": source} if is_on else {}))
        elif is_on and source is not None and source != self._logged_source:
            self._log(act.EV_INPUT, source=source)
        else:
            return
        self._logged_on, self._logged_source = is_on, source

    @callback
    def _record_today(self) -> None:
        if self.activity.record_day(
            self.state.day,
            used_seconds=self.state.used_seconds,
            budget_minutes=self.daily_budget,
            extension_minutes=self.state.extension_minutes,
            blocked=self.state.blocked_switches,
            apps=self.state.app_seconds,
            apps_stopped=self.state.apps_stopped,
        ):
            self.activity_store.schedule_save()
