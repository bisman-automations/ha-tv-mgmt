from datetime import date, time

import pytest

from tv_mgmt_pure.home import (
    ACTION_NOTIFY,
    REASON_HOME_DAILY,
    REASON_HOME_WEEKLY,
    HomeSettings,
    home_status,
    week_days_before,
    week_start,
)
from tv_mgmt_pure.state import (
    MODE_ENFORCED,
    MODE_PAUSED,
    REASON_BUDGET,
    REASON_QUIET,
    STATE_ENFORCING,
    STATE_OK,
    STATE_PAUSED,
    STATE_WARNING,
    decide,
)
from tv_mgmt_pure.quiet import parse_windows

FRIDAY = date(2026, 10, 16)


def test_settings_validate():
    s = HomeSettings.from_dict({"daily_minutes": "180", "weekly_minutes": 900, "action": "notify", "notify": ["notify.phone", " ", "notify.phone"]})
    assert (s.daily_minutes, s.weekly_minutes, s.action, s.notify) == (180, 900, ACTION_NOTIFY, ["notify.phone"])
    assert s.active and s.warn_minutes == 10 and s.week_start == "monday"
    assert HomeSettings.from_dict(s.as_dict()) == s
    assert not HomeSettings().active
    for bad in ({"daily_minutes": -1}, {"daily_minutes": 1441}, {"week_start": "friday"}, {"action": "explode"}, {"weekly_minutes": "lots"}):
        with pytest.raises(ValueError):
            HomeSettings.from_dict(bad)


def test_week_start():
    assert week_start(FRIDAY) == date(2026, 10, 12)  # Monday
    assert week_start(FRIDAY, "sunday") == date(2026, 10, 11)
    assert week_start(date(2026, 10, 11), "sunday") == date(2026, 10, 11)
    assert week_days_before(FRIDAY) == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]
    assert week_days_before(date(2026, 10, 12)) == []


def test_status_picks_the_tighter_limit():
    settings = HomeSettings(daily_minutes=180, weekly_minutes=600)
    s = home_status(settings, today_seconds=60 * 60, week_seconds=550 * 60)
    assert s.daily_remaining == 120 * 60 and s.weekly_remaining == 50 * 60
    assert (s.remaining, s.reason) == (50 * 60, REASON_HOME_WEEKLY)
    s = home_status(settings, today_seconds=170 * 60, week_seconds=300 * 60)
    assert (s.remaining, s.reason) == (10 * 60, REASON_HOME_DAILY)
    # Extra time: today's adds to today; the week's extra adds to the week.
    s = home_status(settings, today_seconds=180 * 60, week_seconds=600 * 60, extension_today=30, extension_week=45)
    assert (s.daily_remaining, s.weekly_remaining) == (30 * 60, 45 * 60)
    assert home_status(HomeSettings(), today_seconds=9999, week_seconds=9999).remaining is None


def _decide(**kw):
    args = dict(now=time(12), mode=MODE_ENFORCED, adult_mode=False, force_block=False, budget_minutes=0,
                extension_minutes=0, used_seconds=0, warn_minutes=5, quiet_windows=[])
    return decide(**{**args, **kw})


def test_decide_with_home_limit():
    # Only the home limits this TV.
    d = _decide(home_remaining=0, home_reason=REASON_HOME_DAILY)
    assert (d.state, d.reason, d.limit) == (STATE_ENFORCING, REASON_HOME_DAILY, REASON_HOME_DAILY)
    d = _decide(home_remaining=4 * 60, home_reason=REASON_HOME_WEEKLY)
    assert (d.state, d.reason, d.remaining_seconds) == (STATE_WARNING, REASON_HOME_WEEKLY, 240)
    # The TV's own limit has less left: it's the one that counts.
    d = _decide(budget_minutes=60, used_seconds=50 * 60, home_remaining=30 * 60, home_reason=REASON_HOME_DAILY)
    assert (d.state, d.limit, d.remaining_seconds) == (STATE_OK, REASON_BUDGET, 600)
    # The home has less left.
    d = _decide(budget_minutes=60, used_seconds=10 * 60, home_remaining=0, home_reason=REASON_HOME_DAILY)
    assert (d.state, d.reason) == (STATE_ENFORCING, REASON_HOME_DAILY)
    # Quiet windows, paused and no home limit behave as before.
    d = _decide(quiet_windows=parse_windows("11:00-13:00 Lunch"), home_remaining=0, home_reason=REASON_HOME_DAILY)
    assert d.reason == REASON_QUIET
    assert _decide(mode=MODE_PAUSED, home_remaining=0, home_reason=REASON_HOME_DAILY).state == STATE_PAUSED
    d = _decide()
    assert (d.state, d.limit, d.remaining_seconds) == (STATE_OK, None, None)
