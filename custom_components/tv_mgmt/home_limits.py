"""Whole-home limits: one set for the home, shared by every TV profile.

Adds up each counted TV's screen time today and this week, tells each TV how
much time the home has left (when the home's limit turns TVs off), and lets
the people picked know when time is running low and when it's up.
"""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_ANY_UPDATED
from .home import (
    ACTION_TURN_OFF,
    REASON_HOME_DAILY,
    HomeSettings,
    HomeStatus,
    home_status,
    week_days_before,
    week_start,
)

if TYPE_CHECKING:
    from .manager import TVManager

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 10  # seconds
DATA_HOME = f"{DOMAIN}_home"
EVENT_HOME_LIMIT = f"{DOMAIN}_home_limit"
KEEP_EXTENSION_DAYS = 8


def _duration(seconds: int) -> str:
    minutes = max(0, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


class HomeLimits:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.home"
        )
        self.settings = HomeSettings()
        self.extensions: dict[str, int] = {}  # ISO date -> extra minutes given that day
        self.notified: dict[
            str, str
        ] = {}  # what was sent -> the day or week it was for
        self._managers: dict[str, TVManager] = {}
        self._exhausted = False

    # ---- storage ----------------------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        try:
            self.settings = HomeSettings.from_dict(data.get("settings"))
        except ValueError:
            _LOGGER.warning(
                "TV Mgmt: ignoring stored whole-home settings that no longer validate"
            )
            self.settings = HomeSettings()
        self.extensions = {k: int(v) for k, v in (data.get("extensions") or {}).items()}
        self.notified = dict(data.get("notified") or {})

    def _data(self) -> dict[str, Any]:
        return {
            "settings": self.settings.as_dict(),
            "extensions": self.extensions,
            "notified": self.notified,
        }

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    # ---- TVs --------------------------------------------------------------------

    @callback
    def register(self, manager: TVManager) -> None:
        self._managers[manager.entry.entry_id] = manager

    @callback
    def unregister(self, manager: TVManager) -> None:
        self._managers.pop(manager.entry.entry_id, None)

    def counted(self) -> list[TVManager]:
        return [m for eid, m in self._managers.items() if self.settings.counts(eid)]

    # ---- totals -----------------------------------------------------------------

    def status(self, today: date | None = None) -> HomeStatus:
        today = today or dt_util.now().date()
        today_key = today.isoformat()
        earlier = week_days_before(today, self.settings.week_start)
        today_seconds = week_seconds = 0
        for manager in self.counted():
            if manager.state.day == today_key:
                today_seconds += manager.state.used_seconds
            daily = manager.activity.daily
            week_seconds += sum(
                daily.get(day, {}).get("used_seconds", 0) for day in earlier
            )
        week_seconds += today_seconds
        return home_status(
            self.settings,
            today_seconds=today_seconds,
            week_seconds=week_seconds,
            extension_today=self.extensions.get(today_key, 0),
            extension_week=self.extension_week(today),
        )

    def remaining_for(self, manager: TVManager) -> tuple[int | None, str | None]:
        """(seconds left, reason) for a TV, or (None, None) when the home doesn't limit it."""
        if not (
            self.settings.active
            and self.settings.action == ACTION_TURN_OFF
            and self.settings.counts(manager.entry.entry_id)
        ):
            return None, None
        status = self.status()
        return status.remaining, status.reason

    # ---- changes from the sidebar app -------------------------------------------------

    @callback
    def set_settings(self, settings: HomeSettings) -> None:
        self.settings = settings
        self._schedule_save()
        self.refresh_all()

    @callback
    def grant_extension(self, minutes: int, by: str | None = None) -> None:
        today = dt_util.now().date()
        key = today.isoformat()
        self.extensions[key] = self.extensions.get(key, 0) + minutes
        oldest = (today - timedelta(days=KEEP_EXTENSION_DAYS)).isoformat()
        self.extensions = {
            d: m for d, m in self.extensions.items() if d >= oldest and m
        }
        self._schedule_save()
        for manager in self.counted():
            manager.log_home_extension(minutes, by)
        self.refresh_all()

    @callback
    def refresh_all(self) -> None:
        """Every TV re-decides now (settings changed, or the home's time ran out)."""
        for manager in list(self._managers.values()):
            manager.refresh()
        async_dispatcher_send(self.hass, SIGNAL_ANY_UPDATED, None)

    # ---- warnings and notifications ---------------------------------------------------

    @callback
    def check(self) -> None:
        """After a TV counts its time: tell people, and stop the other TVs promptly."""
        if not self.settings.active:
            self._exhausted = False
            return
        today = dt_util.now().date()
        status = self.status(today)
        periods = {
            "daily": (
                status.daily_remaining,
                self.settings.daily_minutes,
                today.isoformat(),
            ),
            "weekly": (
                status.weekly_remaining,
                self.settings.weekly_minutes,
                week_start(today, self.settings.week_start).isoformat(),
            ),
        }
        for period, (remaining, limit, key) in periods.items():
            if remaining is None:
                continue
            used = status.today_seconds if period == "daily" else status.week_seconds
            if remaining <= 0:
                self._announce(period, "reached", key, used, limit)
            elif remaining <= self.settings.warn_minutes * 60:
                self._announce(period, "warning", key, used, limit, remaining)

        exhausted = status.remaining is not None and status.remaining <= 0
        if (
            exhausted
            and not self._exhausted
            and self.settings.action == ACTION_TURN_OFF
        ):
            # The TV that counted the last minute blocks itself; the rest follow now
            # instead of on their next tick.
            self.hass.loop.call_soon(self.refresh_all)
        self._exhausted = exhausted

    def _announce(
        self,
        period: str,
        kind: str,
        key: str,
        used: int,
        limit: int,
        remaining: int | None = None,
    ) -> None:
        marker = f"{period}_{kind}"
        if self.notified.get(marker) == key:
            return
        self.notified[marker] = key
        self._schedule_save()
        when = "today" if period == "daily" else "this week"
        if kind == "warning":
            message = f"{_duration(remaining or 0)} of TV time left for the whole home {when}."
        else:
            message = (
                f"The home has used its {_duration(limit * 60)} of TV time {when}."
            )
            if self.settings.action == ACTION_TURN_OFF:
                message += " The TVs are turning off."
        _LOGGER.info("TV Mgmt home: %s", message)
        self.hass.bus.async_fire(
            EVENT_HOME_LIMIT,
            {
                "period": period,
                "state": kind,
                "used_minutes": round(used / 60),
                "limit_minutes": limit,
                "remaining_minutes": round(remaining / 60)
                if remaining is not None
                else 0,
                "action": self.settings.action,
                "message": message,
            },
        )
        for target in self.settings.notify:
            self.hass.async_create_task(self._async_notify(target, message))

    async def _async_notify(self, target: str, message: str) -> None:
        domain, _, name = target.partition(".")
        try:
            if domain == "notify" and self.hass.states.get(target) is not None:
                # A notify entity
                await self.hass.services.async_call(
                    "notify",
                    "send_message",
                    {"entity_id": target, "title": "TV Mgmt", "message": message},
                    blocking=True,
                )
            elif domain == "notify" and self.hass.services.has_service("notify", name):
                await self.hass.services.async_call(
                    "notify",
                    name,
                    {"title": "TV Mgmt", "message": message},
                    blocking=True,
                )
            else:
                _LOGGER.warning(
                    "TV Mgmt home: can't notify %s, it no longer exists", target
                )
        except Exception:  # noqa: BLE001 - a failing phone mustn't stop the limit
            _LOGGER.exception("TV Mgmt home: couldn't notify %s", target)

    # ---- for the sidebar app -----------------------------------------------------------

    def extension_week(self, today: date | None = None) -> int:
        today = today or dt_util.now().date()
        start = week_start(today, self.settings.week_start).isoformat()
        return sum(
            m for d, m in self.extensions.items() if start <= d <= today.isoformat()
        )

    def summary(self) -> dict[str, Any]:
        status = self.status()
        return {
            **self.settings.as_dict(),
            "active": self.settings.active,
            "today_seconds": status.today_seconds,
            "week_seconds": status.week_seconds,
            "daily_remaining": status.daily_remaining,
            "weekly_remaining": status.weekly_remaining,
            "remaining": status.remaining,
            "reason": status.reason,
            "extension_minutes": status.extension_minutes,
            "extension_week_minutes": self.extension_week(),
            "week_start_date": week_start(
                dt_util.now().date(), self.settings.week_start
            ).isoformat(),
        }


def notify_targets(hass: HomeAssistant) -> list[str]:
    """Notify services (like a phone's) and notify entities that can take a message."""
    services = [
        f"notify.{name}"
        for name in sorted(hass.services.async_services_for_domain("notify"))
        if name not in ("notify", "send_message")
    ]
    entities = sorted(s.entity_id for s in hass.states.async_all("notify"))
    return list(dict.fromkeys([*services, *entities]))


async def async_get_home(hass: HomeAssistant) -> HomeLimits:
    if (home := hass.data.get(DATA_HOME)) is None:
        home = hass.data[DATA_HOME] = HomeLimits(hass)
        await home.async_load()
    return home


def get_home(hass: HomeAssistant) -> HomeLimits | None:
    return hass.data.get(DATA_HOME)


__all__ = [
    "REASON_HOME_DAILY",
    "EVENT_HOME_LIMIT",
    "HomeLimits",
    "async_get_home",
    "get_home",
    "notify_targets",
]
