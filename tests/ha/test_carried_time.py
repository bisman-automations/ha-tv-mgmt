"""Extra time is a balance: what's left after a reward stays until it's watched.

A reward (or the extra-time buttons) used to count for that day only, and
the same minutes were added to the week. The next day a limit of 0 showed
"0 min of 0 min, no time left" while the week still had the minutes. These
tests cover midnight, using part of what's left, the whole home and one TV,
a new week, and a normal daily or weekly allowance.
"""

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.tv_mgmt.home import HomeSettings
from homeassistant.core import HomeAssistant
from tests.ha.test_earned_time import _tv
from tests.ha.test_home import FAMILY, PLAYROOM, home, set_tv

DOMAIN = "tv_mgmt"


def _settings(**kw) -> HomeSettings:
    defaults = {"daily_minutes": 0, "weekly_minutes": 0, "warn_minutes": 5}
    return HomeSettings(**{**defaults, **kw})


async def _grant(hass, entry, minutes, *, whole_home=True):
    await hass.services.async_call(
        DOMAIN,
        "grant_extension",
        {
            "profile_id": entry.entry_id,
            "minutes": minutes,
            "whole_home": whole_home,
            "reason": "Alex's reward: 60 Minutes of TV",
        },
        blocking=True,
    )


async def _next_morning(hass) -> None:
    """Let every TV roll the day over after the clock has been moved."""
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_leftover_reward_survives_midnight(
    hass: HomeAssistant, freezer, calls
) -> None:
    """60 minutes granted, 52 watched. The next day the 8 minutes are there."""
    freezer.move_to("2026-11-10 16:00:00-07:00")  # a Tuesday
    family = await _tv(hass, FAMILY, "Family Room TV", 0)
    set_tv(hass, PLAYROOM, "Playroom TV", "off")
    playroom = await _tv(hass, PLAYROOM, "Playroom TV", None)
    home(hass).set_settings(_settings())

    await _grant(hass, family, 60)
    family.runtime_data.state.used_seconds = 52 * 60
    family.runtime_data._record_today()
    family.runtime_data._recompute()
    # Still Tuesday: 8 minutes of today's grant are left. Nothing to carry.
    assert home(hass).status().daily_remaining == 8 * 60
    assert family.runtime_data.decision.state == "ok"

    freezer.move_to("2026-11-11 08:00:00-07:00")
    await _next_morning(hass)

    status = home(hass).status()
    summary = home(hass).summary()
    # The earned-time setting is still 0. The day is not 0 of 0: the 8 remain.
    assert summary["daily_minutes"] == 0
    assert summary["extension_minutes"] == 0
    assert status.carried_seconds == 8 * 60
    assert status.daily_remaining == 8 * 60
    assert status.weekly_remaining == 8 * 60
    assert summary["carried_seconds"] == 8 * 60
    assert (
        summary["daily_minutes"]
        + summary["extension_minutes"]
        + summary["carried_seconds"] / 60
        == 8
    )

    # Family Room was the TV on the reward. Its own limit of 0 carries too.
    assert family.runtime_data.state.day == "2026-11-11"
    assert family.runtime_data.state.extension_minutes == 0
    assert family.runtime_data.carried_seconds == 8 * 60
    assert family.runtime_data.decision.state == "ok"
    assert family.runtime_data.decision.remaining_seconds == 8 * 60
    # Playroom has no daily limit, so the home's 8 minutes are what it has.
    assert playroom.runtime_data.decision.state == "ok"
    assert playroom.runtime_data.decision.remaining_seconds == 8 * 60
    assert playroom.runtime_data.carried_seconds == 0

    # Watching 3 of the 8 leaves 5, and those 5 are there again the next day.
    family.runtime_data.state.used_seconds = 3 * 60
    family.runtime_data._record_today()
    family.runtime_data._recompute()
    assert home(hass).status().daily_remaining == 5 * 60
    assert family.runtime_data.decision.remaining_seconds == 5 * 60

    freezer.move_to("2026-11-12 08:00:00-07:00")
    await _next_morning(hass)
    status = home(hass).status()
    assert status.carried_seconds == 5 * 60
    assert status.daily_remaining == 5 * 60
    assert status.weekly_remaining == 5 * 60
    assert family.runtime_data.carried_seconds == 5 * 60
    # 5 minutes is this TV's warning time, so it warns and stays on.
    assert family.runtime_data.decision.state == "warning"
    assert family.runtime_data.decision.remaining_seconds == 5 * 60


async def test_leftover_survives_into_the_next_week(
    hass: HomeAssistant, freezer, calls
) -> None:
    freezer.move_to("2026-11-08 16:00:00-07:00")  # a Sunday
    family = await _tv(hass, FAMILY, "Family Room TV", 0)
    home(hass).set_settings(_settings(week_start="monday"))
    await _grant(hass, family, 60)
    family.runtime_data.state.used_seconds = 52 * 60
    family.runtime_data._record_today()

    freezer.move_to("2026-11-09 08:00:00-07:00")  # Monday, a new week
    await _next_morning(hass)

    status = home(hass).status()
    # The new week has no grant of its own. The 8 minutes are carried in,
    # or a weekly limit of 0 would say there was nothing left.
    assert status.carried_seconds == 8 * 60
    assert status.carried_week_seconds == 8 * 60
    assert status.daily_remaining == 8 * 60
    assert status.weekly_remaining == 8 * 60
    assert family.runtime_data.decision.state == "ok"
    assert family.runtime_data.decision.remaining_seconds == 8 * 60


async def test_normal_allowances_around_a_reward(
    hass: HomeAssistant, freezer, calls
) -> None:
    """A normal day is used first. The reward stays on top of the next day.

    The weekly allowance still counts the reward this week, once.
    """
    freezer.move_to("2026-11-10 16:00:00-07:00")
    family = await _tv(hass, FAMILY, "Family Room TV", 90)
    home(hass).set_settings(
        HomeSettings(daily_minutes=90, weekly_minutes=600, warn_minutes=5)
    )
    await _grant(hass, family, 30)
    family.runtime_data.state.used_seconds = 40 * 60
    family.runtime_data._record_today()
    family.runtime_data._recompute()
    # 40 of today's 90, so the reward is untouched and the TV is not blocked.
    assert family.runtime_data.decision.state == "ok"

    freezer.move_to("2026-11-11 08:00:00-07:00")
    await _next_morning(hass)

    status = home(hass).status()
    assert status.carried_seconds == 30 * 60
    assert status.carried_week_seconds == 0
    assert status.daily_remaining == 120 * 60
    assert status.weekly_remaining == (600 + 30) * 60 - 40 * 60
    # The TV's own 90 plus the same 30.
    assert family.runtime_data.carried_seconds == 30 * 60
    assert family.runtime_data.decision.remaining_seconds == 120 * 60
    assert family.runtime_data.decision.state == "ok"


async def test_weekly_allowance_keeps_only_what_was_not_watched(
    hass: HomeAssistant, freezer, calls
) -> None:
    """No daily limit. Watching uses the extra. Next week keeps the leftover."""
    freezer.move_to("2026-11-08 16:00:00-07:00")
    family = await _tv(hass, FAMILY, "Family Room TV", None)
    home(hass).set_settings(
        HomeSettings(daily_minutes=None, weekly_minutes=600, warn_minutes=5)
    )
    await _grant(hass, family, 60)
    family.runtime_data.state.used_seconds = 52 * 60
    family.runtime_data._record_today()

    freezer.move_to("2026-11-09 08:00:00-07:00")
    await _next_morning(hass)

    status = home(hass).status()
    assert status.daily_remaining is None
    assert status.carried_week_seconds == 8 * 60
    assert status.weekly_remaining == 600 * 60 + 8 * 60
    # The TV itself has no daily limit, so it follows the home.
    assert family.runtime_data.carried_seconds == 0
    assert family.runtime_data.decision.remaining_seconds == status.weekly_remaining
    assert family.runtime_data.decision.state == "ok"


async def test_upgrade_restores_the_eight_minutes(
    hass: HomeAssistant, freezer, hass_storage: dict, calls
) -> None:
    """Donavan's saved state: 75 minutes granted this week, 67 watched.

    Nothing is rewritten. Loading the profile makes the 8 minutes available
    and the day is not a 0-minute limit any more.
    """
    freezer.move_to("2026-11-11 08:00:00-07:00")
    set_tv(hass, FAMILY, "Family Room TV", "off")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Family Room TV",
        version=2,
        minor_version=2,
        unique_id=FAMILY,
        data={"media_player": FAMILY},
        options={
            "input_lock": {"allowed_sources": ["HDMI 2"], "revert_delay": 1},
            "screen_time": {
                "daily_budget": 0,
                "warn_minutes": 5,
                "quiet_windows": "",
                "adult_mode_duration": 120,
            },
        },
    )
    day = {
        "blocked": 0,
        "adult_seconds": 0,
        "budget_minutes": 0,
        "limited": True,
    }
    hass_storage[f"tv_mgmt.{entry.entry_id}"] = {
        "version": 1,
        "key": f"tv_mgmt.{entry.entry_id}",
        "data": {"day": "2026-11-11", "used_seconds": 0, "extension_minutes": 0},
    }
    hass_storage[f"tv_mgmt.{entry.entry_id}.activity"] = {
        "version": 1,
        "key": f"tv_mgmt.{entry.entry_id}.activity",
        "data": {
            "events": [],
            "daily": {
                "2026-11-09": {**day, "used_seconds": 15 * 60, "extension_minutes": 15},
                "2026-11-10": {**day, "used_seconds": 52 * 60, "extension_minutes": 60},
            },
        },
    }
    hass_storage["tv_mgmt.home"] = {
        "version": 1,
        "key": "tv_mgmt.home",
        "data": {
            "settings": _settings().as_dict(),
            "extensions": {"2026-11-09": 15, "2026-11-10": 60},
            "notified": {},
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    summary = home(hass).summary()
    assert summary["daily_minutes"] == 0
    assert summary["weekly_minutes"] == 0
    assert summary["extension_minutes"] == 0
    assert summary["today_seconds"] == 0
    assert summary["week_seconds"] == 67 * 60
    assert summary["carried_seconds"] == 8 * 60
    assert summary["daily_remaining"] == 8 * 60
    assert summary["weekly_remaining"] == 8 * 60
    # Dashboard total for today is 8 minutes, not 0 of 0.
    assert summary["daily_minutes"] + summary["carried_seconds"] / 60 == 8
    manager = entry.runtime_data
    assert manager.carried_seconds == 8 * 60
    assert manager.decision.state == "ok"
    assert manager.decision.remaining_seconds == 8 * 60

    # Using the last of it says 8 minutes, not "used its 0 min".
    manager.state.used_seconds = 8 * 60
    manager._recompute()
    await hass.async_block_till_done()
    assert manager.decision.state == "enforcing"
    assert home(hass).status().daily_remaining == 0


async def test_other_tv_with_its_own_zero_does_not_get_the_reward(
    hass: HomeAssistant, freezer, calls
) -> None:
    """Whole home shares the home's balance. A TV's own limit of 0 does not.

    The reward was for Family Room. Playroom's own limit is still 0, so
    Playroom stays off. Family Room and the home have the leftover.
    """
    freezer.move_to("2026-11-10 16:00:00-07:00")
    family = await _tv(hass, FAMILY, "Family Room TV", 0)
    set_tv(hass, PLAYROOM, "Playroom TV", "off")
    playroom = await _tv(hass, PLAYROOM, "Playroom TV", 0)
    home(hass).set_settings(_settings())
    await _grant(hass, family, 60)
    family.runtime_data.state.used_seconds = 52 * 60
    family.runtime_data._record_today()

    freezer.move_to("2026-11-11 08:00:00-07:00")
    await _next_morning(hass)

    assert family.runtime_data.decision.state == "ok"
    assert family.runtime_data.decision.remaining_seconds == 8 * 60
    assert playroom.runtime_data.carried_seconds == 0
    assert playroom.runtime_data.decision.state == "enforcing"
    assert playroom.runtime_data.decision.reason == "budget"
    assert home(hass).status().daily_remaining == 8 * 60


async def test_family_reward_and_undo_use_the_same_call(
    hass: HomeAssistant, freezer, calls
) -> None:
    """Family sends profile_id, minutes, whole_home and reason. Nothing else.

    Undo is the same call with negative minutes. Taking the reward back
    removes the balance, including after some of it was watched.
    """
    freezer.move_to("2026-11-10 16:00:00-07:00")
    family = await _tv(hass, FAMILY, "Family Room TV", 0)
    home(hass).set_settings(_settings())

    await hass.services.async_call(
        DOMAIN,
        "grant_extension",
        {
            "profile_id": family.entry_id,
            "minutes": 60,
            "whole_home": True,
            "reason": "Alex's reward: 60 Minutes of TV",
        },
        blocking=True,
    )
    events = family.runtime_data.activity.events
    extension = next(e for e in reversed(events) if e["type"] == "extension")
    home_extension = next(e for e in reversed(events) if e["type"] == "home_extension")
    assert extension["minutes"] == 60
    assert extension["reason"] == "Alex's reward: 60 Minutes of TV"
    assert home_extension["reason"] == "Alex's reward: 60 Minutes of TV"

    family.runtime_data.state.used_seconds = 52 * 60
    family.runtime_data._record_today()
    # Taking the reward back, the way Family undoes it.
    await hass.services.async_call(
        DOMAIN,
        "grant_extension",
        {
            "profile_id": family.entry_id,
            "minutes": -60,
            "whole_home": True,
            "reason": "Undone: 60 Minutes of TV",
        },
        blocking=True,
    )
    assert family.runtime_data.state.extension_minutes == 0
    assert family.runtime_data.decision.state == "enforcing"
    assert home(hass).extensions.get("2026-11-10", 0) == 0

    freezer.move_to("2026-11-11 08:00:00-07:00")
    await _next_morning(hass)
    # The reward is gone, so the 8 minutes are not carried. The 52 minutes
    # that were already watched stay on this week's total.
    status = home(hass).status()
    assert status.carried_seconds == 0
    assert status.daily_remaining == 0
    assert status.weekly_remaining == -52 * 60
    assert family.runtime_data.carried_seconds == 0
    assert family.runtime_data.decision.state == "enforcing"
    assert family.runtime_data.decision.remaining_seconds == 0
