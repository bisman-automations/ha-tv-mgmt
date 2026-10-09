"""Input lock: forces a TV back to its allowed input."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
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
)

_LOGGER = logging.getLogger(__name__)


class InputGuard:
    """Watches a TV backend and switches it back to an allowed input.

    The manager decides whether the lock is active (`is_active`) and whether
    violations are acted on or only reported (`should_revert`).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        backend: TVBackend,
        options: dict[str, Any],
        *,
        is_active: Callable[[], bool],
        should_revert: Callable[[], bool],
        on_block: Callable[[str | None, str], None],
        on_change: Callable[[], None],
        pinned_source: str | None = None,
    ) -> None:
        self.hass = hass
        self.name = name
        self.backend = backend
        self.options = options
        self._is_active = is_active
        self._should_revert = should_revert
        self._on_block = on_block
        self._on_change = on_change
        # The Apple TV's input: always allowed, and where the TV is sent back to.
        self.pinned_source = pinned_source

        self.paused_reason: str | None = None
        self._was_on: bool | None = None
        self._force_next = False
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._attempts: deque[datetime] = deque()
        self._reported_source: str | None = None

    # ---- config -----------------------------------------------------------

    @property
    def allowed_sources(self) -> list[str]:
        allowed = list(self.options.get(CONF_ALLOWED_SOURCES, []))
        if self.pinned_source and self.pinned_source not in allowed:
            allowed.insert(0, self.pinned_source)
        return allowed

    @property
    def target_source(self) -> str | None:
        # On Android TV the input is reported as an app but switched with an
        # HDMI key, so the configured HDMI target still does the switching.
        if self.pinned_source and not self.backend.targets_are_keys:
            return self.pinned_source
        if target := self.options.get(CONF_TARGET_SOURCE):
            return target
        allowed = self.allowed_sources
        return allowed[0] if allowed else None

    @property
    def revert_delay(self) -> float:
        return float(self.options.get(CONF_REVERT_DELAY, DEFAULT_REVERT_DELAY))

    @property
    def enforce_on_power_on(self) -> bool:
        return bool(self.options.get(CONF_ENFORCE_ON_POWER_ON, DEFAULT_ENFORCE_ON_POWER_ON))

    @property
    def max_attempts(self) -> int:
        return int(self.options.get(CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS))

    def is_allowed(self, source: str | None) -> bool:
        return source is not None and source in self.allowed_sources

    # ---- driven by the manager ----------------------------------------------

    @callback
    def async_start(self) -> None:
        self._was_on = self.backend.is_on
        self.evaluate()

    @callback
    def async_stop(self) -> None:
        self._cancel_timer()

    @callback
    def handle_backend_update(self) -> None:
        is_on = self.backend.is_on
        powered_on = bool(is_on) and not self._was_on
        self._was_on = is_on
        if powered_on:
            self._attempts.clear()
            if self.enforce_on_power_on and (
                self.backend.current_source is None or not self.backend.reports_source
            ):
                self._force_next = True
        self.evaluate()

    @callback
    def reset(self) -> None:
        """Lock re-enabled or settings changed: start fresh."""
        self._attempts.clear()
        self.paused_reason = None
        self._reported_source = None
        self.evaluate()

    @callback
    def evaluate(self) -> None:
        if not self._is_active() or not self.backend.is_on:
            self._cancel_timer()
            self._force_next = False
            return

        source = self.backend.current_source
        if self.is_allowed(source) and not self._force_next:
            self._cancel_timer()
            self._attempts.clear()
            self._reported_source = None
            if self.paused_reason:
                self.paused_reason = None
                self._on_change()
            return

        # An unknown source (booting, some built-in apps) only counts right
        # after power-on, which sets _force_next.
        if source is None and not self._force_next:
            return

        if self._unsub_timer is None:
            self._unsub_timer = async_call_later(
                self.hass, self.revert_delay, self._revert_callback
            )

    # ---- enforcement ------------------------------------------------------

    @callback
    def _revert_callback(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.hass.async_create_task(self._async_revert(), eager_start=True)

    async def _async_revert(self) -> None:
        force = self._force_next
        self._force_next = False
        if not self._is_active() or not self.backend.is_on:
            return

        current = self.backend.current_source
        violation = not self.is_allowed(current)
        if not violation and not force:
            return

        target = self.target_source
        if target is None:
            _LOGGER.warning("TV Mgmt %s: no allowed input set", self.name)
            return

        # Report each new violation once, even in monitor-only mode.
        if violation and current != self._reported_source:
            self._reported_source = current
            self._on_block(current, target)

        if not self._should_revert():
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
                _LOGGER.warning("TV Mgmt %s: %s", self.name, self.paused_reason)
                self._on_change()
            return
        self._attempts.append(now)

        _LOGGER.info("TV Mgmt %s: on %r, switching to %r", self.name, current, target)
        await self.backend.async_select_source(target)

        # Some TVs don't push an update after switching; check again.
        self._unsub_timer = async_call_later(
            self.hass, max(self.revert_delay, 3), self._recheck_callback
        )

    @callback
    def _recheck_callback(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.evaluate()

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
