"""Input lock: forces a TV back to an allowed input."""

from __future__ import annotations

from collections import deque
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .backends import TVBackend
from .const import (
    ATTEMPT_WINDOW,
    CONF_ALLOWED_SOURCES,
    CONF_ENFORCE_ON_POWER_ON,
    CONF_MAX_ATTEMPTS,
    CONF_REVERT_DELAY,
    CONF_TARGET_SOURCE,
    DEFAULT_ENFORCE_ON_POWER_ON,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_REVERT_DELAY,
    EVENT_INPUT_BLOCKED,
    SIGNAL_STATE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)


class InputGuard:
    """Watches one TV backend and forces it back to an allowed input."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, backend: TVBackend) -> None:
        self.hass = hass
        self.entry = entry
        self.backend = backend
        self.enabled = True
        self.blocked_count = 0
        self.last_blocked_source: str | None = None
        self.last_blocked_at: datetime | None = None
        self.paused_reason: str | None = None

        self._was_on: bool | None = None
        self._force_next = False
        self._unsub_backend: CALLBACK_TYPE | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._attempts: deque[datetime] = deque()

    # ---- config -----------------------------------------------------------

    @property
    def name(self) -> str:
        return self.entry.title

    @property
    def allowed_sources(self) -> list[str]:
        return list(self.entry.options.get(CONF_ALLOWED_SOURCES, []))

    @property
    def target_source(self) -> str | None:
        if target := self.entry.options.get(CONF_TARGET_SOURCE):
            return target
        allowed = self.allowed_sources
        return allowed[0] if allowed else None

    @property
    def revert_delay(self) -> float:
        return float(self.entry.options.get(CONF_REVERT_DELAY, DEFAULT_REVERT_DELAY))

    @property
    def enforce_on_power_on(self) -> bool:
        return bool(self.entry.options.get(CONF_ENFORCE_ON_POWER_ON, DEFAULT_ENFORCE_ON_POWER_ON))

    @property
    def max_attempts(self) -> int:
        return int(self.entry.options.get(CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS))

    # ---- lifecycle --------------------------------------------------------

    @callback
    def async_start(self) -> None:
        self._unsub_backend = self.backend.add_listener(self._on_backend_update)
        self._was_on = self.backend.is_on
        self._evaluate()

    @callback
    def async_stop(self) -> None:
        if self._unsub_backend:
            self._unsub_backend()
            self._unsub_backend = None
        self._cancel_timer()

    @callback
    def async_set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._attempts.clear()
        self.paused_reason = None
        if enabled:
            self._evaluate()
        else:
            self._cancel_timer()
        self._notify()

    # ---- enforcement ------------------------------------------------------

    @callback
    def _on_backend_update(self) -> None:
        is_on = self.backend.is_on
        powered_on = bool(is_on) and not self._was_on
        self._was_on = is_on
        if powered_on and self.enforce_on_power_on:
            source = self.backend.current_source
            if source is None or not self.backend.reports_source:
                self._force_next = True
        self._evaluate()
        self._notify()

    def _is_allowed(self, source: str | None) -> bool:
        return source is not None and source in self.allowed_sources

    @callback
    def _evaluate(self) -> None:
        if not self.enabled or not self.backend.is_on:
            self._cancel_timer()
            self._force_next = False
            return

        source = self.backend.current_source
        if self._is_allowed(source) and not self._force_next:
            # Back where it should be: cancel pending revert, reset backoff.
            self._cancel_timer()
            self._attempts.clear()
            if self.paused_reason:
                self.paused_reason = None
                self._notify()
            return

        # Unknown source (booting, some built-in apps) only counts right
        # after power-on, which sets _force_next.
        if source is None and not self._force_next:
            return

        if self._unsub_timer is None:
            self._unsub_timer = async_call_later(
                self.hass, self.revert_delay, self._revert_callback
            )

    @callback
    def _revert_callback(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.hass.async_create_task(self._async_revert(), eager_start=True)

    async def _async_revert(self) -> None:
        force = self._force_next
        self._force_next = False
        if not self.enabled or not self.backend.is_on:
            return

        current = self.backend.current_source
        if self._is_allowed(current) and not force:
            return

        target = self.target_source
        if target is None:
            _LOGGER.warning("TV Management for %s has no allowed input set", self.name)
            return

        now = dt_util.utcnow()
        while self._attempts and (now - self._attempts[0]).total_seconds() > ATTEMPT_WINDOW:
            self._attempts.popleft()
        if len(self._attempts) >= self.max_attempts:
            if not self.paused_reason:
                self.paused_reason = (
                    f"TV did not stay on {target!r} after {self.max_attempts} tries; "
                    "paused until it does"
                )
                _LOGGER.warning("TV Management %s: %s", self.name, self.paused_reason)
                self._notify()
            return
        self._attempts.append(now)

        if not force or not self._is_allowed(current):
            self.blocked_count += 1
            self.last_blocked_source = current
            self.last_blocked_at = now
            self.hass.bus.async_fire(
                EVENT_INPUT_BLOCKED,
                {
                    "config_entry_id": self.entry.entry_id,
                    "tv": self.name,
                    "blocked_source": current,
                    "target_source": target,
                },
            )
        _LOGGER.info("TV Management %s: on %r, forcing %r", self.name, current, target)
        self._notify()

        await self.backend.async_select_source(target)

        # Some TVs don't push an update after switching; check again.
        self._unsub_timer = async_call_later(
            self.hass, max(self.revert_delay, 3), self._recheck_callback
        )

    @callback
    def _recheck_callback(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._evaluate()

    # ---- helpers ----------------------------------------------------------

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED.format(self.entry.entry_id))

    @property
    def attributes(self) -> dict[str, Any]:
        return {
            "current_source": self.backend.current_source,
            "allowed_sources": self.allowed_sources,
            "target_source": self.target_source,
            "paused_reason": self.paused_reason,
        }
