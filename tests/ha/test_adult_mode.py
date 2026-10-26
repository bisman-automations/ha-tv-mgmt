"""Adult mode is tracked as its own total, and by default doesn't use up limits.

With "Count adult mode time toward limits" off (the default) adult mode time
goes only into adult_seconds: not the TV's time, the home's, or any app's, and
the apps and titles opened in it aren't recorded. With it on, the time counts
like any other, but Activity still doesn't show what was watched in it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from tests.ha.test_apple_tv import BOX, LOCK, YT, set_box, tick
from tests.ha.test_integration import TV, set_tv, setup

DOMAIN = "tv_mgmt"
ADULT = "sensor.tv_mgmt_family_room_tv_adult_mode_time_today"
USED = "sensor.tv_mgmt_family_room_tv_time_used_today"
STATE = "sensor.tv_mgmt_family_room_tv_enforcement_state"
PRIME = ("com.amazon.aiv.AIVApp", "Prime Video")


def screen(**changes: Any) -> dict[str, Any]:
    return {"daily_budget": 0, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120, **changes}


async def setup_tv(hass, *, box=False, apps: dict[str, Any] | None = None, **screen_time):
    options: dict[str, Any] = {"input_lock": LOCK, "screen_time": screen(**screen_time)}
    if box:
        options |= {
            "apple_tv": {"streaming_player": BOX, "app_mode": "block", "apps": [], "app_limits": {},
                         "app_action": "home", "sleep_on_block": True, **(apps or {})},
            "announcements": {}, "lights": {}, "sync": {},
        }
    return await setup(hass, options=options)


def play(hass, title: str, app=PRIME, state="playing"):
    hass.states.async_set(BOX, state, {"friendly_name": "Living Room Apple TV", "app_id": app[0],
                                       "app_name": app[1], "media_title": title})


async def watch(hass, freezer, minutes: float) -> None:
    for _ in range(int(minutes * 2)):
        await tick(hass, freezer, 30)


async def ws(hass_ws_client, hass: HomeAssistant, **msg: Any) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(msg)
    res = await client.receive_json()
    await hass.async_block_till_done()
    assert res["success"], res
    return res["result"]


def kinds(manager) -> list[str]:
    return [e["type"] for e in manager.activity.events]


# ---- option off (the default) ---------------------------------------------------


async def test_not_counted_by_default(hass: HomeAssistant, freezer, calls) -> None:
    set_tv(hass)
    entry = await setup_tv(hass, daily_budget=30)
    manager = entry.runtime_data
    assert manager.adult_mode_counts is False
    manager.set_adult_mode(True)
    await watch(hass, freezer, 40)  # longer than the 30 minute limit
    assert manager.state.adult_seconds == 40 * 60
    assert manager.state.adult_counted_seconds == 0
    assert manager.state.used_seconds == 0
    assert float(hass.states.get(ADULT).state) == pytest.approx(40)
    assert hass.states.get(ADULT).attributes["counts_toward_limits"] is False
    assert float(hass.states.get(USED).state) == 0
    assert calls["turn_off"] == []

    manager.set_adult_mode(False)
    await hass.async_block_till_done()
    assert hass.states.get(STATE).state == "ok"  # nothing of the 30 minutes was used
    await watch(hass, freezer, 5)
    assert manager.state.used_seconds == 5 * 60
    assert manager.state.adult_seconds == 40 * 60


async def test_switching_counts_up_to_that_moment(hass: HomeAssistant, freezer, calls) -> None:
    """The time before a switch books to the mode it was watched in, not the next count."""
    set_tv(hass)
    entry = await setup_tv(hass)
    manager = entry.runtime_data
    freezer.tick(timedelta(seconds=20))  # 20 s before the next 30 s count
    manager.set_adult_mode(True)
    assert manager.state.used_seconds == 20 and manager.state.adult_seconds == 0
    freezer.tick(timedelta(seconds=25))
    manager.set_adult_mode(False)
    assert manager.state.adult_seconds == 25 and manager.state.used_seconds == 20
    events = [e for e in manager.activity.events if e["type"] == "adult_mode"]
    assert [e["on"] for e in events] == [True, False]


async def test_no_app_or_title_recorded(hass: HomeAssistant, freezer, hass_ws_client, calls, box_calls) -> None:
    set_tv(hass)
    play(hass, "Moana")
    entry = await setup_tv(hass, box=True)
    manager = entry.runtime_data
    await watch(hass, freezer, 1)
    manager.set_adult_mode(True)
    on_at = dt_util.utcnow()
    play(hass, "Late Night Film")
    await hass.async_block_till_done()
    await watch(hass, freezer, 10)
    play(hass, "Another Late Film", app=YT)
    await hass.async_block_till_done()
    await watch(hass, freezer, 5)

    # Nothing about it is kept: not the time per app or title, nor the events.
    assert manager.state.app_seconds == {PRIME[0]: 60}
    assert manager.state.media_seconds == {"Moana": 60}
    titles = [e.get("title") for e in manager.activity.events if e["type"] == "media"]
    assert titles == ["Moana"]
    assert not [e for e in manager.activity.events
                if e["type"] in ("app", "media") and datetime.fromisoformat(e["t"]) > on_at]
    attrs = hass.states.get("sensor.tv_mgmt_family_room_tv_app_time_today").attributes
    assert list(attrs["apps"]) == ["Prime Video"] and list(attrs["shows"]) == ["Moana"]
    assert manager.state.adult_seconds == 15 * 60

    # Turning it off logs the open app again, so the app strip picks up from there.
    manager.set_adult_mode(False)
    await hass.async_block_till_done()
    relogged = [e for e in manager.activity.events if e["type"] == "app"][-1]
    assert relogged["name"] == "YouTube"
    assert datetime.fromisoformat(relogged["t"]) == on_at + timedelta(minutes=15)
    await watch(hass, freezer, 2)
    assert manager.state.app_seconds[YT[0]] == 120

    day = await ws(hass_ws_client, hass, type=f"{DOMAIN}/activity", entry_id=entry.entry_id)
    assert [(p["seconds"], p["live"]) for p in day["adult_periods"]] == [(15 * 60, False)]
    assert day["adult_viewing_seconds"] == 15 * 60
    starts = [datetime.fromisoformat(s["start"]) for s in day["app_segments"]]
    assert [s["name"] for s in day["app_segments"]] == ["Prime Video", "YouTube"]
    assert starts[1] == on_at + timedelta(minutes=15)

    summary = (await ws(hass_ws_client, hass, type=f"{DOMAIN}/analytics", entry_id=entry.entry_id))[
        "profiles"][0]["summary"]
    assert summary["adult_seconds"] == 15 * 60 and summary["adult_counted_seconds"] == 0
    assert [a["app"] for a in summary["top_apps"]] == [YT[0], PRIME[0]]
    # Still playing after adult mode ended, so those two minutes count as usual.
    assert [(m["show"], m["seconds"]) for m in summary["top_media"]] == [("Another Late Film", 120), ("Moana", 60)]
    assert "Late Night Film" not in str(summary)


async def test_per_app_limit_not_used(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    entry = await setup_tv(hass, box=True, apps={"app_limits": {"YouTube": 2}})
    manager = entry.runtime_data
    stopped = []
    hass.bus.async_listen("tv_mgmt_app_blocked", stopped.append)
    manager.set_adult_mode(True)
    await watch(hass, freezer, 10)
    assert manager.state.app_seconds.get(YT[0], 0) == 0
    manager.set_adult_mode(False)
    await watch(hass, freezer, 1)
    assert stopped == []  # still a minute left
    await watch(hass, freezer, 2)
    assert len(stopped) == 1 and stopped[0].data["reason"] == "limit"


async def test_home_limits_not_used(hass: HomeAssistant, freezer, calls) -> None:
    from custom_components.tv_mgmt.home import HomeSettings

    from tests.ha.test_home import FAMILY, PLAYROOM, add_tv, home
    from tests.ha.test_home import set_tv as set_named_tv

    # A Tuesday evening, so yesterday and today are in the same week.
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 20, 23, 30, tzinfo=dt_util.get_default_time_zone())))
    set_named_tv(hass, FAMILY, "Family Room TV")
    set_named_tv(hass, PLAYROOM, "Playroom TV")
    family = await add_tv(hass, FAMILY, "Family Room TV")
    playroom = await add_tv(hass, PLAYROOM, "Playroom TV")
    home(hass).set_settings(HomeSettings(daily_minutes=60, weekly_minutes=120))
    family.runtime_data.set_adult_mode(True)
    await watch(hass, freezer, 20)
    status = home(hass).status()
    assert status.today_seconds == 20 * 60  # the playroom only
    assert status.week_seconds == 20 * 60
    await watch(hass, freezer, 20)  # to ten past midnight
    status = home(hass).status()
    assert status.today_seconds == 10 * 60
    assert status.week_seconds == 40 * 60  # yesterday's 30 and today's 10, the playroom's
    assert family.runtime_data.state.used_seconds == 0
    assert family.runtime_data.state.adult_seconds == 10 * 60
    assert playroom.runtime_data.state.used_seconds == 10 * 60
    record = family.runtime_data.activity.daily["2026-10-20"]
    assert record["used_seconds"] == 0 and record["adult_seconds"] == 30 * 60


# ---- option on ---------------------------------------------------------------------


async def test_counted_when_turned_on(hass: HomeAssistant, freezer, hass_ws_client, calls, box_calls) -> None:
    set_tv(hass)
    play(hass, "Moana")
    entry = await setup_tv(hass, box=True, daily_budget=10, adult_mode_counts=True)
    manager = entry.runtime_data
    manager.set_adult_mode(True)
    on_at = dt_util.utcnow()
    play(hass, "Late Night Film")
    await hass.async_block_till_done()
    await watch(hass, freezer, 15)
    # Counted like any other time, but adult mode keeps the TV on past the limit.
    assert manager.state.used_seconds == 15 * 60
    assert manager.state.adult_seconds == manager.state.adult_counted_seconds == 15 * 60
    assert manager.state.app_seconds[PRIME[0]] == 15 * 60
    assert manager.state.media_seconds["Late Night Film"] == 15 * 60
    assert calls["turn_off"] == []
    manager.set_adult_mode(False)
    await hass.async_block_till_done()
    assert hass.states.get(STATE).state == "enforcing"

    # Activity still doesn't show what was watched in it.
    day = await ws(hass_ws_client, hass, type=f"{DOMAIN}/activity", entry_id=entry.entry_id)
    titles = [e.get("title") for e in day["events"] if e["type"] == "media"]
    assert "Late Night Film" not in titles
    assert all(datetime.fromisoformat(s["end"]) <= on_at for s in day["app_segments"])
    summary = (await ws(hass_ws_client, hass, type=f"{DOMAIN}/analytics", entry_id=entry.entry_id))[
        "profiles"][0]["summary"]
    assert summary["adult_seconds"] == summary["adult_counted_seconds"] == 15 * 60


async def test_option_applies_live(hass: HomeAssistant, freezer, hass_ws_client, calls, box_calls) -> None:
    set_tv(hass)
    play(hass, "Moana")
    entry = await setup_tv(hass, box=True)
    manager = entry.runtime_data
    manager.set_adult_mode(True)
    await watch(hass, freezer, 5)
    with patch.object(hass.config_entries, "async_reload") as reload:
        await ws(hass_ws_client, hass, type=f"{DOMAIN}/limits/set", entry_id=entry.entry_id,
                 adult_mode_counts=True)
    reload.assert_not_called()
    assert entry.runtime_data is manager and manager.adult_mode_counts is True
    assert entry.options["screen_time"]["adult_mode_counts"] is True
    # The open app is logged again, now that adult mode time is recorded.
    assert [e["type"] for e in manager.activity.events].count("app") == 2
    await watch(hass, freezer, 5)
    assert manager.state.adult_seconds == 10 * 60
    assert manager.state.adult_counted_seconds == manager.state.used_seconds == 5 * 60
    assert manager.state.app_seconds[PRIME[0]] == 5 * 60
    assert hass.states.get(ADULT).attributes["counts_toward_limits"] is True
    limits = await ws(hass_ws_client, hass, type=f"{DOMAIN}/limits/get", entry_id=entry.entry_id)
    assert limits["adult_mode_counts"] is True

    # And back off.
    await ws(hass_ws_client, hass, type=f"{DOMAIN}/limits/set", entry_id=entry.entry_id,
             adult_mode_counts=False)
    await watch(hass, freezer, 5)
    assert manager.state.used_seconds == 5 * 60
    assert manager.state.adult_seconds == 15 * 60


# ---- boundaries ----------------------------------------------------------------------


async def test_running_out(hass: HomeAssistant, freezer, calls) -> None:
    """Only the time before it ran out is adult mode, and an off event marks when."""
    set_tv(hass)
    entry = await setup_tv(hass)
    manager = entry.runtime_data
    freezer.tick(timedelta(seconds=10))  # so it runs out between two counts
    manager.set_adult_mode(True, 10)
    until = manager.state.adult_mode_until_dt
    await watch(hass, freezer, 15)
    assert not manager.adult_mode_active
    assert manager.state.adult_seconds == 10 * 60
    assert manager.state.used_seconds == 15 * 60 - 10 * 60 + 10
    off = [e for e in manager.activity.events if e["type"] == "adult_mode"][-1]
    assert off["type"] == "adult_mode" and off["on"] is False and off["reason"] == "expired"
    assert datetime.fromisoformat(off["t"]) == until
    assert sum(1 for k in kinds(manager) if k == "adult_mode") == 2


async def test_split_at_midnight(hass: HomeAssistant, freezer, calls) -> None:
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 20, 23, 50, tzinfo=dt_util.get_default_time_zone())))
    set_tv(hass)
    entry = await setup_tv(hass)
    manager = entry.runtime_data
    manager.set_adult_mode(True)
    await watch(hass, freezer, 20)
    record = manager.activity.daily["2026-10-20"]
    assert record["adult_seconds"] == 10 * 60 and record["used_seconds"] == 0
    assert "adult_counted_seconds" not in record
    assert manager.state.day == "2026-10-21"
    assert manager.state.adult_seconds == 10 * 60 and manager.state.used_seconds == 0
    assert manager.adult_mode_active  # still on after midnight


async def test_survives_a_restart(hass: HomeAssistant, freezer, calls) -> None:
    set_tv(hass)
    entry = await setup_tv(hass)
    entry.runtime_data.set_adult_mode(True)
    await watch(hass, freezer, 6)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    manager = entry.runtime_data
    assert manager.state.adult_seconds == 6 * 60
    assert manager.adult_mode_active
    assert float(hass.states.get(ADULT).state) == pytest.approx(6)
    await watch(hass, freezer, 2)
    assert manager.state.adult_seconds == 8 * 60 and manager.state.used_seconds == 0


async def test_paused_keeps_counting(hass: HomeAssistant, freezer, calls) -> None:
    """Paused (the TV mode) still counts as adult mode time while adult mode is on."""
    set_tv(hass)
    entry = await setup_tv(hass)
    manager = entry.runtime_data
    manager.set_adult_mode(True)
    manager.set_mode("paused")
    await watch(hass, freezer, 3)
    assert manager.state.adult_seconds == 3 * 60


# ---- reports -------------------------------------------------------------------------


async def test_reports(hass: HomeAssistant, freezer, hass_ws_client, calls) -> None:
    from custom_components.tv_mgmt.diagnostics import async_get_config_entry_diagnostics

    set_tv(hass)
    entry = await setup_tv(hass)
    manager = entry.runtime_data
    # A day recorded before 1.16.0 has no adult mode total.
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    manager.activity.daily[yesterday] = {"used_seconds": 600, "budget_minutes": 0,
                                         "extension_minutes": 0, "blocked": 0}
    manager.set_adult_mode(True)
    await watch(hass, freezer, 4)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["adult_mode"] == {"active": True, "counts_toward_limits": False,
                                  "seconds_today": 240, "counted_seconds_today": 0}
    profile = (await ws(hass_ws_client, hass, type=f"{DOMAIN}/profiles"))["profiles"][0]
    assert profile["adult_seconds"] == 240 and profile["adult_mode_counts"] is False
    result = (await ws(hass_ws_client, hass, type=f"{DOMAIN}/analytics", entry_id=entry.entry_id, days=2))
    days = result["profiles"][0]["days"]
    assert [(d["adult_tracked"], d["adult_seconds"]) for d in days] == [(False, 0), (True, 240)]
    summary = result["profiles"][0]["summary"]
    assert summary["adult_untracked_days"] == 1 and summary["adult_seconds"] == 240
    assert summary["total_seconds"] == 600  # adult time that didn't count isn't screen time


async def test_setup_form_has_the_option(hass: HomeAssistant) -> None:
    from homeassistant import config_entries

    set_tv(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"media_player": TV})
    assert "adult_mode_counts" in str(result["data_schema"].schema["screen_time"].schema.schema)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"input_lock": LOCK, "screen_time": screen(), "apple_tv": {}, "announcements": {}, "lights": {}, "sync": {}},
    )
    assert result["options"]["screen_time"]["adult_mode_counts"] is False  # off unless chosen
    assert result["options"]["lights"]["tv_lights_follow"] is True  # also new in 1.16.0
    await hass.async_block_till_done()
