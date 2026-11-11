from datetime import date, time

import pytest
from tv_mgmt_pure.home import (
    ACTION_NOTIFY,
    REASON_HOME_DAILY,
    REASON_HOME_WEEKLY,
    HomeSettings,
    carried_extra,
    home_status,
    week_days_before,
    week_start,
)
from tv_mgmt_pure.quiet import parse_windows
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

FRIDAY = date(2026, 10, 16)


def test_settings_validate():
    s = HomeSettings.from_dict(
        {
            "daily_minutes": "180",
            "weekly_minutes": 900,
            "action": "notify",
            "notify": ["notify.phone", " ", "notify.phone"],
        }
    )
    assert (s.daily_minutes, s.weekly_minutes, s.action, s.notify) == (
        180,
        900,
        ACTION_NOTIFY,
        ["notify.phone"],
    )
    assert s.active and s.warn_minutes == 10 and s.week_start == "monday"
    assert HomeSettings.from_dict(s.as_dict()) == s
    assert not HomeSettings().active
    for bad in (
        {"daily_minutes": -1},
        {"daily_minutes": 1441},
        {"week_start": "friday"},
        {"action": "explode"},
        {"weekly_minutes": "lots"},
    ):
        with pytest.raises(ValueError):
            HomeSettings.from_dict(bad)


def test_week_start():
    assert week_start(FRIDAY) == date(2026, 10, 12)  # Monday
    assert week_start(FRIDAY, "sunday") == date(2026, 10, 11)
    assert week_start(date(2026, 10, 11), "sunday") == date(2026, 10, 11)
    assert week_days_before(FRIDAY) == [
        "2026-10-12",
        "2026-10-13",
        "2026-10-14",
        "2026-10-15",
    ]
    assert week_days_before(date(2026, 10, 12)) == []


def test_status_picks_the_tighter_limit():
    settings = HomeSettings(daily_minutes=180, weekly_minutes=600)
    s = home_status(settings, today_seconds=60 * 60, week_seconds=550 * 60)
    assert s.daily_remaining == 120 * 60 and s.weekly_remaining == 50 * 60
    assert (s.remaining, s.reason) == (50 * 60, REASON_HOME_WEEKLY)
    s = home_status(settings, today_seconds=170 * 60, week_seconds=300 * 60)
    assert (s.remaining, s.reason) == (10 * 60, REASON_HOME_DAILY)
    # Extra time: today's adds to today; the week's extra adds to the week.
    # carried_* is unused extra from before today / before this week.
    s = home_status(
        settings,
        today_seconds=180 * 60,
        week_seconds=600 * 60,
        extension_today=30,
        extension_week=45,
    )
    assert (s.daily_remaining, s.weekly_remaining) == (30 * 60, 45 * 60)
    assert (
        home_status(HomeSettings(), today_seconds=9999, week_seconds=9999).remaining
        is None
    )


def _decide(**kw):
    args = dict(
        now=time(12),
        mode=MODE_ENFORCED,
        adult_mode=False,
        force_block=False,
        budget_minutes=None,
        extension_minutes=0,
        used_seconds=0,
        warn_minutes=5,
        quiet_windows=[],
    )
    return decide(**{**args, **kw})


def test_decide_with_home_limit():
    # Only the home limits this TV.
    d = _decide(home_remaining=0, home_reason=REASON_HOME_DAILY)
    assert (d.state, d.reason, d.limit) == (
        STATE_ENFORCING,
        REASON_HOME_DAILY,
        REASON_HOME_DAILY,
    )
    d = _decide(home_remaining=4 * 60, home_reason=REASON_HOME_WEEKLY)
    assert (d.state, d.reason, d.remaining_seconds) == (
        STATE_WARNING,
        REASON_HOME_WEEKLY,
        240,
    )
    # The TV's own limit has less left: it's the one that counts.
    d = _decide(
        budget_minutes=60,
        used_seconds=50 * 60,
        home_remaining=30 * 60,
        home_reason=REASON_HOME_DAILY,
    )
    assert (d.state, d.limit, d.remaining_seconds) == (STATE_OK, REASON_BUDGET, 600)
    # The home has less left.
    d = _decide(
        budget_minutes=60,
        used_seconds=10 * 60,
        home_remaining=0,
        home_reason=REASON_HOME_DAILY,
    )
    assert (d.state, d.reason) == (STATE_ENFORCING, REASON_HOME_DAILY)
    # Quiet windows, paused and no home limit behave as before.
    d = _decide(
        quiet_windows=parse_windows("11:00-13:00 Lunch"),
        home_remaining=0,
        home_reason=REASON_HOME_DAILY,
    )
    assert d.reason == REASON_QUIET
    assert (
        _decide(mode=MODE_PAUSED, home_remaining=0, home_reason=REASON_HOME_DAILY).state
        == STATE_PAUSED
    )
    d = _decide()
    assert (d.state, d.limit, d.remaining_seconds) == (STATE_OK, None, None)


def _status(settings, grants, used, today, **kw):
    """Home status with the balance carried in from earlier days."""
    carried_today, carried_week = carried_extra(
        grants,
        used,
        today=today,
        daily_minutes=settings.daily_minutes,
        week_start=week_start(
            date.fromisoformat(today), settings.week_start
        ).isoformat(),
        weekly_minutes=settings.weekly_minutes,
    )
    return home_status(
        settings,
        today_seconds=kw.get("today_seconds", 0),
        week_seconds=kw.get("week_seconds", sum(used.values())),
        extension_today=kw.get("extension_today", 0),
        extension_week=kw.get(
            "extension_week",
            sum(
                minutes
                for day, minutes in grants.items()
                if week_start(
                    date.fromisoformat(today), settings.week_start
                ).isoformat()
                <= day
                < today
            )
            + kw.get("extension_today", 0),
        ),
        carried_today_seconds=carried_today,
        carried_week_seconds=carried_week,
    )


def test_reward_left_over_is_available_the_next_day():
    """60 granted, 52 watched, both limits 0: the next day has 8 minutes.

    This is the whole-home card that showed 0 of 0 today while the week
    still had 8 minutes. The day's allowance of 0 must not hide the balance.
    """
    earned = HomeSettings(daily_minutes=0, weekly_minutes=0)
    # Monday 15 min and Tuesday 60 min, 67 watched: 8 left on Wednesday.
    grants = {"2026-11-09": 15, "2026-11-10": 60}
    used = {"2026-11-09": 15 * 60, "2026-11-10": 52 * 60}
    s = _status(earned, grants, used, "2026-11-11")
    assert s.carried_seconds == 8 * 60
    assert s.carried_week_seconds == 0  # both grants are this week
    assert s.daily_remaining == 8 * 60
    assert s.weekly_remaining == 8 * 60
    assert (s.remaining, s.reason) == (8 * 60, REASON_HOME_WEEKLY)
    # Not a 0-minute day. The cap is the 8 minutes, not 0.
    assert s.today_seconds + s.daily_remaining == 8 * 60


def test_partial_use_of_the_balance_carries_again():
    earned = HomeSettings(daily_minutes=0, weekly_minutes=0)
    grants = {"2026-11-10": 60}
    used = {"2026-11-10": 52 * 60}
    # Wednesday they watch 3 of the 8.
    wednesday = _status(
        earned,
        grants,
        used,
        "2026-11-11",
        today_seconds=3 * 60,
        week_seconds=55 * 60,
    )
    assert wednesday.daily_remaining == 5 * 60
    assert wednesday.weekly_remaining == 5 * 60
    # Thursday the 5 minutes are still there, and the 0 allowance is not back.
    thursday = _status(
        earned,
        grants,
        {**used, "2026-11-11": 3 * 60},
        "2026-11-12",
    )
    assert thursday.carried_seconds == 5 * 60
    assert thursday.daily_remaining == 5 * 60
    assert thursday.weekly_remaining == 5 * 60


def test_balance_used_up_is_a_zero_day_again():
    earned = HomeSettings(daily_minutes=0, weekly_minutes=0)
    s = _status(
        earned,
        {"2026-11-10": 60},
        {"2026-11-10": 60 * 60},
        "2026-11-11",
    )
    assert s.carried_seconds == 0
    assert s.daily_remaining == 0
    assert s.weekly_remaining == 0
    assert s.reason == REASON_HOME_WEEKLY


def test_balance_survives_the_new_week():
    """Sunday's leftover is still there on Monday, and it raises the new week.

    Without that, a weekly allowance of 0 would block the TV on Monday even
    though 8 minutes were never watched.
    """
    earned = HomeSettings(daily_minutes=0, weekly_minutes=0, week_start="monday")
    # 2026-11-08 is a Sunday; the week starts Monday the 9th.
    s = _status(
        earned,
        {"2026-11-08": 60},
        {"2026-11-08": 52 * 60},
        "2026-11-09",
        week_seconds=0,
    )
    assert s.carried_seconds == 8 * 60
    assert s.carried_week_seconds == 8 * 60
    assert s.daily_remaining == 8 * 60
    assert s.weekly_remaining == 8 * 60


def test_normal_daily_allowance_is_used_before_the_balance():
    """A normal day is watched first. The reward stays until a day goes past it."""
    settings = HomeSettings(daily_minutes=90, weekly_minutes=600)
    # Watched 40 of a 90 minute day, so none of the 30 minute reward was used.
    kept = _status(
        settings,
        {"2026-11-10": 30},
        {"2026-11-10": 40 * 60},
        "2026-11-11",
    )
    assert kept.carried_seconds == 30 * 60
    assert kept.daily_remaining == 120 * 60  # today's 90 plus the reward
    assert kept.weekly_remaining == (600 + 30) * 60 - 40 * 60

    # Watched 80 of a 60 minute day after a 30 minute reward: 10 of it is left.
    spent = carried_extra(
        {"2026-11-10": 30},
        {"2026-11-10": 80 * 60},
        today="2026-11-11",
        daily_minutes=60,
        week_start="2026-11-09",
        weekly_minutes=600,
    )
    assert spent == (10 * 60, 0)
    day = home_status(
        HomeSettings(daily_minutes=60, weekly_minutes=600),
        today_seconds=0,
        week_seconds=80 * 60,
        extension_week=30,
        carried_today_seconds=spent[0],
        carried_week_seconds=spent[1],
    )
    assert day.daily_remaining == 70 * 60
    assert day.weekly_remaining == (600 + 30 - 80) * 60


def test_normal_weekly_allowance_does_not_shelter_extra_forever():
    """No daily limit: watching uses the extra, and the weekly allowance resets.

    Last week a 60 minute reward and 52 minutes watched. This week still has
    its own 10 hours, plus the 8 minutes that were never watched. The reward
    is not added again in full.
    """
    settings = HomeSettings(daily_minutes=None, weekly_minutes=600)
    s = _status(
        settings,
        {"2026-11-08": 60},
        {"2026-11-08": 52 * 60},
        "2026-11-09",
        week_seconds=0,
    )
    assert s.carried_seconds == 0  # no daily limit to add it to
    assert s.carried_week_seconds == 8 * 60
    assert s.daily_remaining is None
    assert s.weekly_remaining == (600 * 60) + (8 * 60)


def test_same_week_extra_is_not_counted_twice():
    """Monday's grant is in this week's total. It is not also carried in."""
    settings = HomeSettings(daily_minutes=120, weekly_minutes=600)
    carried_today, carried_week = carried_extra(
        {"2026-11-09": 60},
        {"2026-11-09": 30 * 60},
        today="2026-11-10",
        daily_minutes=120,
        week_start="2026-11-09",
        weekly_minutes=600,
    )
    # 30 watched is inside the 120 minute day, so the reward is intact.
    assert carried_today == 60 * 60
    assert carried_week == 0
    s = home_status(
        settings,
        today_seconds=0,
        week_seconds=30 * 60,
        extension_week=60,
        carried_today_seconds=carried_today,
        carried_week_seconds=carried_week,
    )
    assert s.daily_remaining == (120 + 60) * 60
    assert s.weekly_remaining == (600 + 60 - 30) * 60


def test_taking_time_away_does_not_leave_a_debt():
    carried_today, _week = carried_extra(
        {"2026-11-10": -15},
        {},
        today="2026-11-11",
        daily_minutes=60,
        week_start="2026-11-09",
        weekly_minutes=None,
    )
    assert carried_today == 0
    # A penalty does shrink a balance that's already there.
    carried_today, _week = carried_extra(
        {"2026-11-09": 60, "2026-11-10": -15},
        {},
        today="2026-11-11",
        daily_minutes=0,
        week_start="2026-11-09",
        weekly_minutes=0,
    )
    assert carried_today == 45 * 60


def test_no_limits_carry_nothing():
    assert carried_extra(
        {"2026-11-10": 60},
        {"2026-11-10": 10 * 60},
        today="2026-11-11",
        daily_minutes=None,
        weekly_minutes=None,
    ) == (0, 0)


def test_per_tv_days_use_the_allowance_that_day_had():
    """Yesterday the TV's own limit was 0. Today it still is. The reward remains.

    A day with no limit doesn't spend the balance.
    """
    carried_today, _week = carried_extra(
        {"2026-11-09": 30, "2026-11-10": 60},
        {"2026-11-09": 30 * 60, "2026-11-10": 52 * 60},
        today="2026-11-11",
        daily_minutes=0,
        allowances={"2026-11-09": None, "2026-11-10": 0},
    )
    # The 9th had no limit, so its 30 minute grant is still in the balance,
    # and the 10th (limit 0) spent 52 of the 90.
    assert carried_today == (30 + 60 - 52) * 60
