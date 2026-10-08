"""Enforcement decisions.

Pure module (no Home Assistant imports) so it can be unit tested alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from .quiet import QuietWindow, active_window

# Modes (select entity)
MODE_ENFORCED = "enforced"
MODE_MONITOR_ONLY = "monitor_only"
MODE_PAUSED = "paused"
MODES = [MODE_ENFORCED, MODE_MONITOR_ONLY, MODE_PAUSED]

# Enforcement states (sensor)
STATE_OK = "ok"
STATE_WARNING = "warning"
STATE_ENFORCING = "enforcing"
STATE_PAUSED = "paused"
STATE_ADULT_MODE = "adult_mode"
STATES = [STATE_OK, STATE_WARNING, STATE_ENFORCING, STATE_PAUSED, STATE_ADULT_MODE]

# Why the TV is blocked
REASON_MANUAL = "manual"
REASON_QUIET = "quiet_window"
REASON_BUDGET = "budget"


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str | None = None
    quiet_window: str | None = None
    remaining_seconds: int | None = None  # None = unlimited

    @property
    def blocked(self) -> bool:
        return self.state == STATE_ENFORCING


def decide(
    *,
    now: time,
    mode: str,
    adult_mode: bool,
    force_block: bool,
    budget_minutes: int,
    extension_minutes: int,
    used_seconds: int,
    warn_minutes: int,
    quiet_windows: list[QuietWindow],
) -> Decision:
    """What should be true about the TV right now.

    budget_minutes == 0 means no daily limit. The decision is the same in
    monitor-only mode; the caller just doesn't act on it.
    """
    remaining: int | None = None
    if budget_minutes > 0:
        remaining = (budget_minutes + extension_minutes) * 60 - used_seconds

    if mode == MODE_PAUSED:
        return Decision(STATE_PAUSED, remaining_seconds=remaining)
    if adult_mode:
        return Decision(STATE_ADULT_MODE, remaining_seconds=remaining)

    if force_block:
        return Decision(STATE_ENFORCING, REASON_MANUAL, remaining_seconds=remaining)
    if window := active_window(quiet_windows, now):
        return Decision(STATE_ENFORCING, REASON_QUIET, window.name, remaining)
    if remaining is not None:
        if remaining <= 0:
            return Decision(STATE_ENFORCING, REASON_BUDGET, remaining_seconds=0)
        if remaining <= warn_minutes * 60:
            return Decision(STATE_WARNING, REASON_BUDGET, remaining_seconds=remaining)
    return Decision(STATE_OK, remaining_seconds=remaining)
