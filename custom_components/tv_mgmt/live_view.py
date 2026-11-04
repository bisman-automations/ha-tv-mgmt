"""What the TV's Live Activity shows. Pure module: no Home Assistant imports.

The Live Activity starts when the TV turns on and shows how much TV time is
left today, counting down on the phone by itself (a chronometer), so it only
needs a push when something visible changes: extra time, a warning, the time
running out, the rules pausing, or the TV turning off.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .state import (
    REASON_MANUAL,
    REASON_QUIET,
    STATE_ADULT_MODE,
    STATE_ENFORCING,
    STATE_PAUSED,
    STATE_WARNING,
)

TONE_OK = "ok"
TONE_WARN = "warn"
TONE_BAD = "bad"
TONE_OFF = "off"

HOME_LIMITS = ("home_daily", "home_weekly")


@dataclass(frozen=True)
class LiveView:
    """What the activity shows; compared to decide whether to push."""

    headline: str  # the short status (critical text)
    message: str
    tone: str
    # When the time runs out, epoch seconds rounded to the minute. The phone
    # counts down to it; None when nothing is counting down.
    ends_at: int | None = None


def duration(seconds: float) -> str:
    """'1 h 5 min', '45 min'."""
    total = max(0, round(seconds / 60))
    if total < 60:
        return f"{total} min"
    hours, minutes = divmod(total, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def live_view(
    *,
    on: bool,
    state: str,
    reason: str | None,
    limit: str | None,
    remaining_seconds: int | None,
    used_seconds: int,
    quiet_window: str | None,
    now: float,
    clock: Callable[[int], str] | None = None,
) -> LiveView:
    """The activity for this moment.

    ``now`` is epoch seconds; ``clock`` formats an epoch time as the local
    time of day ("7:45 PM") for when the time runs out.
    """
    watched = f"{duration(used_seconds)} watched today"
    if not on:
        return LiveView("TV off", watched, TONE_OFF)
    if state == STATE_PAUSED:
        return LiveView("Rules paused", "No limits right now.", TONE_OK)
    if state == STATE_ADULT_MODE:
        return LiveView("Adult mode", "Every rule is lifted for now.", TONE_OK)
    if state == STATE_ENFORCING:
        if reason == REASON_QUIET:
            return LiveView(
                "Quiet time",
                f"{quiet_window or 'A quiet window'} is on. The TV is turned off.",
                TONE_BAD,
            )
        if reason == REASON_MANUAL:
            return LiveView("Blocked", "A parent blocked the TV for now.", TONE_BAD)
        whose = "the whole home's" if reason in HOME_LIMITS else "today's"
        return LiveView("Time's up", f"That's all of {whose} TV time.", TONE_BAD)
    if remaining_seconds is None:
        # Time watched isn't shown while on: it would need a push a minute.
        return LiveView("No limit today", "This TV has no daily limit.", TONE_OK)
    ends_at = int(now + remaining_seconds) // 60 * 60
    whose = "The whole home's time" if limit in HOME_LIMITS else "TV time"
    message = f"{whose} ends at {clock(ends_at)}." if clock else f"{whose} left today."
    return LiveView(
        "Time left today",
        message,
        TONE_WARN if state == STATE_WARNING else TONE_OK,
        ends_at,
    )


# Between checks the countdown's end can move by a few seconds; that isn't
# worth a push.
ENDS_TOLERANCE = 90


def same_view(a: LiveView | None, b: LiveView | None) -> bool:
    """Whether the phone already shows ``b`` (``a`` is what it shows)."""
    if a is None or b is None:
        return a is b
    if a.headline != b.headline or a.tone != b.tone:
        return False
    if a.ends_at is None or b.ends_at is None:
        return a.ends_at is b.ends_at and a.message == b.message
    return abs(a.ends_at - b.ends_at) <= ENDS_TOLERANCE


def clock_text(hour: int, minute: int) -> str:
    """'7:45 PM'."""
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
