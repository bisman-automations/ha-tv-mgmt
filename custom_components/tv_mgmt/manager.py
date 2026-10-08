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

from .backends import TVBackend
from .const import (
    CONF_ADULT_MODE_DURATION,
    CONF_DAILY_BUDGET,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_WINDOWS,
    CONF_WARN_MINUTES,
    DEFAULT_ADULT_MODE_DURATION,
    DEFAULT_DAILY_BUDGET,
    DEFAULT_WARN_MINUTES,
    EVENT_ENFORCEMENT_CHANGED,
    EVENT_INPUT_BLOCKED,
    EVENT_WARNING,
    SIGNAL_UPDATED,
    TICK_INTERVAL,
    TURN_OFF_COOLDOWN,
    TURN_OFF_DELAY,
)
from .guard import InputGuard
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
from .storage import ProfileState, ProfileStore

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
        )

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

    def _enforcing_actions(self) -> bool:
        """Act on rules (enforced) vs. only report them (monitor only)."""
        return self.state.mode == MODE_ENFORCED

    def _lock_active(self) -> bool:
        return (
            self.state.input_lock
            and self.decision.state in (STATE_OK, STATE_WARNING)
        )

    # ---- lifecycle ---------------------------------------------------------------

    async def async_start(self) -> None:
        await self.store.async_load()
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
        self.guard.async_start()

    async def async_stop(self) -> None:
        self._count_usage()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_turn_off()
        self.guard.async_stop()
        await self.backend.async_stop()
        await self.store.async_save()

    # ---- events ------------------------------------------------------------------

    @callback
    def _on_backend_update(self) -> None:
        self._count_usage()
        self._recompute()
        self.guard.handle_backend_update()
        self._enforce_power()
        self.notify()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._count_usage()
        self._recompute()
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
        self.hass.bus.async_fire(
            EVENT_INPUT_BLOCKED,
            {
                "profile_id": self.entry.entry_id,
                "tv": self.name,
                ATTR_ENTITY_ID: self.tv_entity_id,
                "blocked_source": source,
                "target_source": target,
                "reverted": self._enforcing_actions(),
            },
        )
        self.notify()

    # ---- usage + decisions ----------------------------------------------------------

    def _rollover_if_needed(self, local_now: datetime) -> None:
        today = local_now.date()
        if self.state.day != today.isoformat():
            self.state.reset_day(today)
            self.store.schedule_save()

    @callback
    def _count_usage(self) -> None:
        """Add the time since the last count if the TV was on."""
        now = dt_util.utcnow()
        last, self._last_tick = self._last_tick, now
        local_now = dt_util.as_local(now)

        if last is not None and self.backend.is_on:
            elapsed = now - last
            if timedelta(0) < elapsed <= MAX_TICK_GAP:
                # Split at midnight so viewing books to the right day.
                midnight = dt_util.start_of_local_day(local_now)
                local_last = dt_util.as_local(last)
                if local_last < midnight:
                    self.state.used_seconds += int((midnight - local_last).total_seconds())
                    self._rollover_if_needed(local_now)
                    elapsed = local_now - midnight
                self.state.used_seconds += int(elapsed.total_seconds())
                self.store.schedule_save()

        self._rollover_if_needed(local_now)

        if self.state.adult_mode_until and not self.adult_mode_active:
            self.state.adult_mode_until = None
            self.store.schedule_save()

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
    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        if self.state.mode != mode:
            self.state.mode = mode
            self._changed()

    @callback
    def set_input_lock(self, enabled: bool) -> None:
        if self.state.input_lock != enabled:
            self.state.input_lock = enabled
            self._changed()

    @callback
    def set_adult_mode(self, enabled: bool, minutes: int | None = None) -> None:
        if enabled:
            duration = minutes or self.adult_mode_duration
            self.state.adult_mode_until = (dt_util.utcnow() + timedelta(minutes=duration)).isoformat()
        else:
            self.state.adult_mode_until = None
        self._changed()

    @callback
    def grant_extension(self, minutes: int) -> None:
        self._count_usage()
        self.state.extension_minutes += minutes
        self._changed()

    @callback
    def force_block(self) -> None:
        self.state.force_block = True
        self._changed()

    @callback
    def unblock(self) -> None:
        self.state.force_block = False
        self._changed()

    @callback
    def reset_usage(self) -> None:
        self.state.reset_day(dt_util.now().date())
        self._last_tick = dt_util.utcnow()
        self._changed()

    # ---- output -----------------------------------------------------------------------

    @callback
    def notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATED.format(self.entry.entry_id))
