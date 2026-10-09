"""Streaming box (Apple TV) linked to a TV profile.

Watches the box's media_player from Home Assistant's Apple TV integration,
reports which app is open, and stops apps that the rules don't allow by
sending the box home (through its remote entity) or putting it to sleep.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.components.remote import (
    ATTR_COMMAND,
    DOMAIN as REMOTE_DOMAIN,
    SERVICE_SEND_COMMAND,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_STANDBY,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .apps import ACTION_HOME, ACTION_SLEEP, AppRules, is_home
from .media import PLAYING, media_from, media_key
from .const import APP_STOP_DELAY, ATTEMPT_WINDOW, TURN_OFF_COOLDOWN

_LOGGER = logging.getLogger(__name__)

ATTR_APP_ID = "app_id"
ATTR_APP_NAME = "app_name"
_ASLEEP = {STATE_OFF, STATE_STANDBY, STATE_UNAVAILABLE, STATE_UNKNOWN}
_TURN_ON = 128  # MediaPlayerEntityFeature.TURN_ON
MAX_STOPS_PER_MINUTE = 5


class StreamingBox:
    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        rules: AppRules,
        action: str,
        *,
        rules_active: Callable[[], bool],
        should_act: Callable[[], bool],
        used_seconds: Callable[[str], int],
        on_stop: Callable[[str, str | None, str, str, bool], None],
        on_update: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.rules = rules
        self.action = action if action in (ACTION_HOME, ACTION_SLEEP) else ACTION_HOME
        self._rules_active = rules_active
        self._should_act = should_act
        self._used_seconds = used_seconds
        self._on_stop = on_stop
        self._on_update = on_update

        # The app counted as open: set after usage up to now has been counted.
        self.app_id: str | None = None
        self.app_name: str | None = None
        self.stop_reason: str | None = None
        # What's playing, counted the same way: the show, episode, movie or song.
        self.media: dict[str, Any] | None = None
        self.media_playing = False

        self._unsub: CALLBACK_TYPE | None = None
        self._unsub_stop: CALLBACK_TYPE | None = None
        self._reported: tuple[str, str] | None = None
        self._stops: deque[datetime] = deque()
        self._last_sleep: datetime | None = None
        self._last_wake: datetime | None = None

    # ---- what the box reports ---------------------------------------------------

    @property
    def _state(self) -> State | None:
        return self.hass.states.get(self.entity_id)

    @property
    def available(self) -> bool:
        state = self._state
        return state is not None and state.state != STATE_UNAVAILABLE

    @property
    def is_on(self) -> bool:
        state = self._state
        return state is not None and state.state not in _ASLEEP

    def reported_app(self) -> tuple[str | None, str | None]:
        """(app ID, name) on screen now; (None, None) for home screen or asleep."""
        state = self._state
        if state is None or not self.is_on:
            return None, None
        app_id = state.attributes.get(ATTR_APP_ID)
        name = state.attributes.get(ATTR_APP_NAME)
        if is_home(app_id):
            return None, None
        return app_id, name or app_id

    @property
    def source_list(self) -> list[str]:
        state = self._state
        return list(state.attributes.get("source_list") or []) if state else []

    # ---- lifecycle ------------------------------------------------------------------

    @callback
    def async_start(self, listener: Callable[[], None]) -> None:
        @callback
        def _changed(_event) -> None:
            listener()

        # Must be a @callback: otherwise Home Assistant runs it in a worker thread.
        self._unsub = async_track_state_change_event(self.hass, [self.entity_id], _changed)
        self.app_id, self.app_name = self.reported_app()
        self.refresh_media()

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._cancel_stop()

    @callback
    def refresh_app(self) -> bool:
        """Pick up the app on screen. Returns True if it changed."""
        app_id, name = self.reported_app()
        changed = app_id != self.app_id
        self.app_id, self.app_name = app_id, name
        if changed:
            self._reported = None
        return changed

    def reported_media(self) -> tuple[dict[str, Any] | None, bool]:
        """(what's playing or paused, whether it's playing)."""
        state = self._state
        if state is None or not self.is_on:
            return None, False
        return media_from(state.state, dict(state.attributes)), state.state == PLAYING

    @callback
    def refresh_media(self) -> bool:
        """Pick up what's playing. Returns True if it's a different title or episode."""
        media, playing = self.reported_media()
        changed = media_key(media) != media_key(self.media)
        self.media, self.media_playing = media, playing
        return changed

    # ---- rules ----------------------------------------------------------------------

    @callback
    def evaluate(self) -> None:
        reason = None
        if self.app_id and self._rules_active():
            reason = self.rules.check(self.app_id, self.app_name, self._used_seconds(self.app_id))
        self.stop_reason = reason
        if reason is None:
            self._cancel_stop()
            return
        if self._unsub_stop is None:
            self._unsub_stop = async_call_later(self.hass, APP_STOP_DELAY, self._stop_callback)

    @callback
    def _stop_callback(self, _now: datetime) -> None:
        self._unsub_stop = None
        self.refresh_app()
        if not self.app_id or not self._rules_active():
            return
        reason = self.rules.check(self.app_id, self.app_name, self._used_seconds(self.app_id))
        if reason is None:
            return
        acting = self._should_act()

        # Report each app once per visit, even in monitor-only mode.
        if self._reported != (self.app_id, reason):
            self._reported = (self.app_id, reason)
            self._on_stop(self.app_id, self.app_name, reason, self.action, acting)

        if not acting:
            return
        now = dt_util.utcnow()
        while self._stops and (now - self._stops[0]).total_seconds() > ATTEMPT_WINDOW:
            self._stops.popleft()
        if len(self._stops) >= MAX_STOPS_PER_MINUTE:
            _LOGGER.warning("TV Mgmt: %s keeps reopening %s; waiting", self.entity_id, self.app_name)
            return
        self._stops.append(now)
        _LOGGER.info("TV Mgmt: stopping %s on %s (%s)", self.app_name, self.entity_id, reason)
        if self.action == ACTION_SLEEP:
            self._sleep()
        else:
            self._go_home()
        # Check again in case the box didn't report the change.
        self._unsub_stop = async_call_later(self.hass, APP_STOP_DELAY + 3, self._recheck)

    @callback
    def _recheck(self, _now: datetime) -> None:
        self._unsub_stop = None
        self.refresh_app()
        self.evaluate()
        self._on_update()

    @callback
    def _cancel_stop(self) -> None:
        if self._unsub_stop:
            self._unsub_stop()
            self._unsub_stop = None

    # ---- actions ----------------------------------------------------------------------

    def _remote_entity(self) -> str | None:
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if entry is None or entry.device_id is None:
            return None
        for other in er.async_entries_for_device(registry, entry.device_id):
            if other.domain == REMOTE_DOMAIN and not other.disabled:
                return other.entity_id
        return None

    @callback
    def _go_home(self) -> None:
        remote = self._remote_entity()
        if remote is None:
            # No remote entity to press Home with; sleeping still stops the app.
            self._sleep()
            return
        self.hass.async_create_task(
            self.hass.services.async_call(
                REMOTE_DOMAIN,
                SERVICE_SEND_COMMAND,
                {ATTR_ENTITY_ID: remote, ATTR_COMMAND: ["home"]},
                blocking=False,
            )
        )

    @callback
    def _sleep(self) -> None:
        self.hass.async_create_task(
            self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: self.entity_id},
                blocking=False,
            )
        )

    @callback
    def sleep_if_on(self) -> bool:
        """Put the box to sleep (rate limited). Returns True if it was sent."""
        if not self.is_on:
            return False
        now = dt_util.utcnow()
        if self._last_sleep and (now - self._last_sleep).total_seconds() < TURN_OFF_COOLDOWN:
            return False
        self._last_sleep = now
        self._sleep()
        return True

    @property
    def is_asleep(self) -> bool:
        """Off or in standby, as opposed to on, or unreachable."""
        state = self._state
        return state is not None and state.state in (STATE_OFF, STATE_STANDBY)

    @callback
    def wake_if_asleep(self) -> bool:
        """Wake the box (rate limited). Returns True if it was sent."""
        state = self._state
        if not self.is_asleep or state is None:
            return False
        if not int(state.attributes.get("supported_features") or 0) & _TURN_ON:
            return False
        now = dt_util.utcnow()
        if self._last_wake and (now - self._last_wake).total_seconds() < TURN_OFF_COOLDOWN:
            return False
        self._last_wake = now
        self.hass.async_create_task(
            self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: self.entity_id},
                blocking=False,
            )
        )
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "available": self.available,
            "is_on": self.is_on,
            "app_id": self.app_id,
            "app_name": self.app_name,
            "stop_reason": self.stop_reason,
            "remote_entity": (remote := self._remote_entity()),
            "has_remote": remote is not None,
        }
