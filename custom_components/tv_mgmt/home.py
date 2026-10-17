"""Whole-home limits: TV time added up across every TV in the home.

Pure module (no Home Assistant imports) so it can be unit tested alone.

Each TV's screen time counts, so two TVs on for an hour is two hours of the
home's time. A limit can be daily, weekly, or both; the one with less time
left is the one that applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# Why a TV is blocked by the home (Decision.reason)
REASON_HOME_DAILY = "home_daily"
REASON_HOME_WEEKLY = "home_weekly"
HOME_REASONS = (REASON_HOME_DAILY, REASON_HOME_WEEKLY)

# What happens when the home's time runs out
ACTION_TURN_OFF = "turn_off"  # the TVs are turned off, like their own limits
ACTION_NOTIFY = "notify"  # only the people picked are told
ACTIONS = [ACTION_TURN_OFF, ACTION_NOTIFY]

WEEK_STARTS = {"monday": 0, "sunday": 6}  # date.weekday() of the first day

DEFAULT_WARN_MINUTES = 10
MAX_MINUTES_DAY = 1440
MAX_MINUTES_WEEK = 7 * 1440


@dataclass
class HomeSettings:
    daily_minutes: int = 0  # 0 = no daily limit
    weekly_minutes: int = 0  # 0 = no weekly limit
    week_start: str = "monday"
    warn_minutes: int = DEFAULT_WARN_MINUTES
    action: str = ACTION_TURN_OFF
    notify: list[str] = field(default_factory=list)  # notify services or entities
    excluded: list[str] = field(default_factory=list)  # TV profiles (entry IDs) that don't count

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> HomeSettings:
        """Validate stored or submitted settings. Raises ValueError."""
        raw = raw or {}

        def minutes(key: str, top: int) -> int:
            value = raw.get(key) or 0
            try:
                value = int(value)
            except (TypeError, ValueError) as err:
                raise ValueError(f"{key} must be a whole number of minutes") from err
            if not 0 <= value <= top:
                raise ValueError(f"{key} must be from 0 to {top} minutes")
            return value

        week_start = raw.get("week_start") or "monday"
        if week_start not in WEEK_STARTS:
            raise ValueError(f"unknown first day of the week {week_start!r}")
        action = raw.get("action") or ACTION_TURN_OFF
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        return cls(
            daily_minutes=minutes("daily_minutes", MAX_MINUTES_DAY),
            weekly_minutes=minutes("weekly_minutes", MAX_MINUTES_WEEK),
            week_start=week_start,
            warn_minutes=minutes("warn_minutes", 120) if raw.get("warn_minutes") is not None else DEFAULT_WARN_MINUTES,
            action=action,
            notify=list(dict.fromkeys(str(t).strip() for t in raw.get("notify") or [] if str(t).strip())),
            excluded=list(dict.fromkeys(str(e) for e in raw.get("excluded") or [] if e)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "daily_minutes": self.daily_minutes,
            "weekly_minutes": self.weekly_minutes,
            "week_start": self.week_start,
            "warn_minutes": self.warn_minutes,
            "action": self.action,
            "notify": list(self.notify),
            "excluded": list(self.excluded),
        }

    @property
    def active(self) -> bool:
        """Is there any home limit at all?"""
        return self.daily_minutes > 0 or self.weekly_minutes > 0

    def counts(self, entry_id: str) -> bool:
        return entry_id not in self.excluded


def week_start(day: date, first_day: str = "monday") -> date:
    """The first day of the week `day` is in."""
    return day - timedelta(days=(day.weekday() - WEEK_STARTS[first_day]) % 7)


def week_days_before(day: date, first_day: str = "monday") -> list[str]:
    """ISO dates of this week's days before `day`, oldest first."""
    start = week_start(day, first_day)
    return [(start + timedelta(days=i)).isoformat() for i in range((day - start).days)]


@dataclass(frozen=True)
class HomeStatus:
    today_seconds: int
    week_seconds: int
    daily_remaining: int | None  # None = no daily limit
    weekly_remaining: int | None  # None = no weekly limit
    extension_minutes: int = 0  # extra time today

    @property
    def remaining(self) -> int | None:
        """Seconds left under whichever limit has less left."""
        left = [r for r in (self.daily_remaining, self.weekly_remaining) if r is not None]
        return min(left) if left else None

    @property
    def reason(self) -> str | None:
        """Which limit `remaining` comes from. On a tie, the weekly one (it lasts longer)."""
        if self.weekly_remaining is not None and (
            self.daily_remaining is None or self.weekly_remaining <= self.daily_remaining
        ):
            return REASON_HOME_WEEKLY
        if self.daily_remaining is not None:
            return REASON_HOME_DAILY
        return None


def home_status(
    settings: HomeSettings,
    *,
    today_seconds: int,
    week_seconds: int,
    extension_today: int = 0,
    extension_week: int = 0,
) -> HomeStatus:
    """Time used and left.

    Extra time given today adds to today's limit; extra time given on any day
    this week (today included) adds to the weekly limit, so it isn't taken back
    the next day.
    """
    daily = weekly = None
    if settings.daily_minutes > 0:
        daily = (settings.daily_minutes + extension_today) * 60 - today_seconds
    if settings.weekly_minutes > 0:
        weekly = (settings.weekly_minutes + extension_week) * 60 - week_seconds
    return HomeStatus(int(today_seconds), int(week_seconds), daily, weekly, extension_today)
