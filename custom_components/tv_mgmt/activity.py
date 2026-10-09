"""Activity log: what each TV did, and daily screen-time totals.

Pure module (no Home Assistant imports) so it can be unit tested alone.

Events are small dicts kept in time order:

    {"t": "2026-10-08T19:02:11+00:00", "type": "input", "source": "HDMI 2"}

Daily totals are keyed by local date:

    {"2026-10-08": {"used_seconds": 5400, "budget_minutes": 90,
                    "extension_minutes": 15, "blocked": 3}}
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

# Event types
EV_TV_ON = "tv_on"  # source
EV_TV_OFF = "tv_off"
EV_INPUT = "input"  # source
EV_INPUT_BLOCKED = "input_blocked"  # source, target, reverted
EV_ENFORCEMENT = "enforcement"  # state, reason, quiet_window
EV_TURNED_OFF = "turned_off"  # reason: TV Mgmt turned the TV off
EV_MODE = "mode"  # mode, synced
EV_INPUT_LOCK = "input_lock"  # on
EV_ADULT_MODE = "adult_mode"  # on, minutes
EV_EXTENSION = "extension"  # minutes
EV_BLOCK = "block"
EV_UNBLOCK = "unblock"
EV_RESET = "reset"
# Streaming box (Apple TV)
EV_APP = "app"  # app, name; app is None on the home screen or when asleep
EV_APP_STOPPED = "app_stopped"  # app, name, reason, action, acted
EV_BOX_SLEEP = "box_sleep"  # reason: TV Mgmt put the Apple TV to sleep
EV_FOLLOW = "follow"  # source, target: Apple TV woke, TV switched to its input

EVENT_TYPES = [
    EV_TV_ON, EV_TV_OFF, EV_INPUT, EV_INPUT_BLOCKED, EV_ENFORCEMENT, EV_TURNED_OFF,
    EV_MODE, EV_INPUT_LOCK, EV_ADULT_MODE, EV_EXTENSION, EV_BLOCK, EV_UNBLOCK, EV_RESET,
    EV_APP, EV_APP_STOPPED, EV_BOX_SLEEP, EV_FOLLOW,
]

KEEP_EVENT_DAYS = 90
KEEP_DAILY_DAYS = 400
MAX_EVENTS = 20000


def _ts(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(event["t"])


class ActivityLog:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.events: list[dict[str, Any]] = list(data.get("events", []))
        self.daily: dict[str, dict[str, int]] = dict(data.get("daily", {}))

    def as_dict(self) -> dict[str, Any]:
        return {"events": self.events, "daily": self.daily}

    # ---- writing ---------------------------------------------------------------

    def add(self, when: datetime, event_type: str, **data: Any) -> dict[str, Any]:
        event = {"t": when.isoformat(), "type": event_type, **data}
        if self.events and _ts(self.events[-1]) > when:
            # Keep time order if a clock adjustment slipped in.
            index = len(self.events)
            while index and _ts(self.events[index - 1]) > when:
                index -= 1
            self.events.insert(index, event)
        else:
            self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            del self.events[: len(self.events) - MAX_EVENTS]
        return event

    def record_day(
        self,
        day: date | str,
        *,
        used_seconds: int,
        budget_minutes: int,
        extension_minutes: int,
        blocked: int,
        apps: dict[str, int] | None = None,
        apps_stopped: int = 0,
    ) -> bool:
        """Store a day's totals. Returns True if anything changed."""
        key = day if isinstance(day, str) else day.isoformat()
        record: dict[str, Any] = {
            "used_seconds": int(used_seconds),
            "budget_minutes": int(budget_minutes),
            "extension_minutes": int(extension_minutes),
            "blocked": int(blocked),
        }
        if apps:
            record["apps"] = {app: int(secs) for app, secs in apps.items() if secs}
        if apps_stopped:
            record["apps_stopped"] = int(apps_stopped)
        if self.daily.get(key) == record:
            return False
        self.daily[key] = record
        return True

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=KEEP_EVENT_DAYS)
        # Keep the last power/input event before the cutoff so the first
        # remaining day still knows whether the TV was on.
        first_kept = 0
        while first_kept < len(self.events) and _ts(self.events[first_kept]) < cutoff:
            first_kept += 1
        if first_kept:
            carry = next(
                (
                    e for e in reversed(self.events[:first_kept])
                    if e["type"] in (EV_TV_ON, EV_TV_OFF, EV_INPUT)
                ),
                None,
            )
            carry_app = next(
                (e for e in reversed(self.events[:first_kept]) if e["type"] == EV_APP),
                None,
            )
            carried = sorted((e for e in (carry, carry_app) if e), key=_ts)
            self.events = carried + self.events[first_kept:]

        oldest_day = (now.date() - timedelta(days=KEEP_DAILY_DAYS)).isoformat()
        for key in [k for k in self.daily if k < oldest_day]:
            del self.daily[key]

    # ---- reading ---------------------------------------------------------------

    @property
    def last_power(self) -> tuple[bool | None, str | None]:
        """(on, source) as last logged."""
        on: bool | None = None
        source: str | None = None
        for event in self.events:
            if event["type"] == EV_TV_ON:
                on, source = True, event.get("source")
            elif event["type"] == EV_TV_OFF:
                on, source = False, None
            elif event["type"] == EV_INPUT:
                source = event.get("source")
        return on, source

    @property
    def first_day(self) -> str | None:
        candidates = list(self.daily)
        if self.events:
            candidates.append(_ts(self.events[0]).date().isoformat())
        return min(candidates) if candidates else None

    def events_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return [e for e in self.events if start <= _ts(e) < end]

    def viewing_segments(
        self, start: datetime, end: datetime, now: datetime
    ) -> list[dict[str, Any]]:
        """Stretches of time the TV was on, split by input, clipped to [start, end).

        A stretch still going at `now` is marked live.
        """
        stop = min(end, now)
        segments: list[dict[str, Any]] = []
        on = False
        source: str | None = None
        since: datetime | None = None

        def close(at: datetime) -> None:
            if not on or since is None:
                return
            seg_start, seg_end = max(since, start), min(at, stop)
            if seg_end > seg_start:
                segments.append(
                    {
                        "start": seg_start.isoformat(),
                        "end": seg_end.isoformat(),
                        "seconds": int((seg_end - seg_start).total_seconds()),
                        "source": source,
                        "live": False,
                    }
                )

        for event in self.events:
            when = _ts(event)
            if when >= stop:
                break
            kind = event["type"]
            if kind == EV_TV_ON:
                close(when)
                on, source, since = True, event.get("source"), when
            elif kind == EV_TV_OFF:
                close(when)
                on, source, since = False, None, None
            elif kind == EV_INPUT and on:
                if event.get("source") != source:
                    close(when)
                    source, since = event.get("source"), when

        if on and since is not None:
            close(stop)
            if segments and stop == now and segments[-1]["end"] == stop.isoformat():
                segments[-1]["live"] = True
        return segments

    def app_segments(
        self, start: datetime, end: datetime, now: datetime
    ) -> list[dict[str, Any]]:
        """Stretches of time an app was open on the streaming box, clipped to [start, end)."""
        stop = min(end, now)
        segments: list[dict[str, Any]] = []
        app: str | None = None
        name: str | None = None
        since: datetime | None = None

        def close(at: datetime) -> None:
            if app is None or since is None:
                return
            seg_start, seg_end = max(since, start), min(at, stop)
            if seg_end > seg_start:
                segments.append(
                    {
                        "start": seg_start.isoformat(),
                        "end": seg_end.isoformat(),
                        "seconds": int((seg_end - seg_start).total_seconds()),
                        "app": app,
                        "name": name,
                        "live": False,
                    }
                )

        for event in self.events:
            when = _ts(event)
            if when >= stop:
                break
            if event["type"] == EV_APP and event.get("app") != app:
                close(when)
                app, name, since = event.get("app"), event.get("name"), when

        if app is not None and since is not None:
            close(stop)
            if segments and stop == now and segments[-1]["end"] == stop.isoformat():
                segments[-1]["live"] = True
        return segments

    def daily_series(self, today: date, days: int) -> list[dict[str, Any]]:
        """Totals for the last `days` days, oldest first, zero-filled."""
        series = []
        for offset in range(days - 1, -1, -1):
            key = (today - timedelta(days=offset)).isoformat()
            record = self.daily.get(key)
            series.append(
                {
                    "date": key,
                    "used_seconds": record["used_seconds"] if record else 0,
                    "budget_minutes": record["budget_minutes"] if record else 0,
                    "extension_minutes": record["extension_minutes"] if record else 0,
                    "blocked": record["blocked"] if record else 0,
                    "apps": dict(record.get("apps", {})) if record else {},
                    "apps_stopped": record.get("apps_stopped", 0) if record else 0,
                    "recorded": record is not None,
                }
            )
        return series


def summarize(series: list[dict[str, Any]]) -> dict[str, Any]:
    recorded = [d for d in series if d["recorded"]]
    total = sum(d["used_seconds"] for d in recorded)
    active = [d for d in recorded if d["used_seconds"] > 0]
    over = [
        d for d in recorded
        if d["budget_minutes"] > 0
        and d["used_seconds"] >= (d["budget_minutes"] + d["extension_minutes"]) * 60
    ]
    return {
        "total_seconds": total,
        "average_seconds": int(total / len(recorded)) if recorded else 0,
        "active_days": len(active),
        "days_over_limit": len(over),
        "blocked": sum(d["blocked"] for d in recorded),
        "apps_stopped": sum(d.get("apps_stopped", 0) for d in recorded),
        "top_apps": top_apps(recorded),
        "recorded_days": len(recorded),
    }


def top_apps(days: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Apps with the most time over these days."""
    totals: dict[str, int] = {}
    for day in days:
        for app, seconds in day.get("apps", {}).items():
            totals[app] = totals.get(app, 0) + seconds
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [{"app": app, "seconds": seconds} for app, seconds in ranked]
