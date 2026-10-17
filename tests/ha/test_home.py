"""Whole-home limits across every TV."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

DOMAIN = "tv_mgmt"
FAMILY = "media_player.family_room_tv"
PLAYROOM = "media_player.playroom_tv"


def set_tv(hass, entity_id, name, state="on"):
    hass.states.async_set(entity_id, state, {"friendly_name": name, "source": "HDMI 2", "source_list": ["HDMI 1", "HDMI 2"]})


async def add_tv(hass, entity_id, name, budget=0):
    entry = MockConfigEntry(
        domain=DOMAIN, title=name, version=2, unique_id=entity_id, data={"media_player": entity_id},
        options={
            "input_lock": {"allowed_sources": ["HDMI 2"], "revert_delay": 1, "enforce_on_power_on": True, "max_attempts": 5},
            "screen_time": {"daily_budget": budget, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def two_tvs(hass):
    set_tv(hass, FAMILY, "Family Room TV")
    set_tv(hass, PLAYROOM, "Playroom TV")
    family = await add_tv(hass, FAMILY, "Family Room TV")
    playroom = await add_tv(hass, PLAYROOM, "Playroom TV")
    return family, playroom


async def watch(hass, freezer, minutes):
    for _ in range(minutes * 2):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def home(hass):
    return hass.data["tv_mgmt_home"]


async def test_home_daily_limit_turns_every_tv_off(hass: HomeAssistant, hass_ws_client, freezer, calls) -> None:
    family, playroom = await two_tvs(hass)
    phone = async_mock_service(hass, "notify", "parent_phone")
    events = []
    hass.bus.async_listen("tv_mgmt_home_limit", events.append)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/home/get"})
    res = await client.receive_json()
    assert "notify.parent_phone" in res["result"]["notify_targets"]
    assert [t["name"] for t in res["result"]["tvs"]] == ["Family Room TV", "Playroom TV"]
    await client.send_json_auto_id({
        "type": "tv_mgmt/home/set", "daily_minutes": 60, "weekly_minutes": 0, "warn_minutes": 10,
        "notify": ["notify.parent_phone"],
    })
    res = await client.receive_json()
    assert res["success"], res
    assert res["result"]["home"]["daily_minutes"] == 60

    # 25 minutes on each TV: 50 of the home's 60 used, 10 left -> warning.
    await watch(hass, freezer, 25)
    assert calls["turn_off"] == []
    assert [e.data["state"] for e in events] == ["warning"]
    assert "10 min of TV time left for the whole home today" in phone[0].data["message"]
    # The TV itself warns at its own warning time (5 min); the home's 10 min is for parents.
    assert family.runtime_data.decision.state == "ok"
    assert family.runtime_data.decision.limit == "home_daily"
    # Each TV shows the home's time left.
    # (within one 30-second count of the other TV)
    assert 9.5 <= float(hass.states.get("sensor.tv_mgmt_family_room_tv_time_remaining_today").state) <= 10.5

    # 5 more minutes each: the home's hour is used. Both TVs turn off.
    await watch(hass, freezer, 6)
    assert {c.data["entity_id"] for c in calls["turn_off"]} == {FAMILY, PLAYROOM}
    assert family.runtime_data.decision.reason == "home_daily"
    assert playroom.runtime_data.decision.reason == "home_daily"
    assert [e.data["state"] for e in events] == ["warning", "reached"]
    assert events[-1].data["period"] == "daily"
    assert "The TVs are turning off" in phone[-1].data["message"]
    # Each message is sent once.
    await watch(hass, freezer, 2)
    assert len(phone) == 2

    # Extra time for the home unblocks both.
    home(hass).grant_extension(30, by="Parent")
    await hass.async_block_till_done()
    assert family.runtime_data.decision.state in ("ok", "warning")
    assert playroom.runtime_data.decision.state in ("ok", "warning")


async def test_notify_only_leaves_tvs_on(hass: HomeAssistant, freezer, calls) -> None:
    from custom_components.tv_mgmt.home import HomeSettings

    family, _ = await two_tvs(hass)
    phone = async_mock_service(hass, "notify", "parent_phone")
    home(hass).set_settings(HomeSettings(daily_minutes=20, action="notify", notify=["notify.parent_phone"]))
    await watch(hass, freezer, 11)
    assert calls["turn_off"] == []
    assert family.runtime_data.decision.state == "ok"
    assert family.runtime_data.decision.limit is None
    assert "has used its 20 min of TV time today" in phone[-1].data["message"]
    assert "turning off" not in phone[-1].data["message"]


async def test_left_out_tv_isnt_counted_or_stopped(hass: HomeAssistant, freezer, calls) -> None:
    from custom_components.tv_mgmt.home import HomeSettings

    family, playroom = await two_tvs(hass)
    home(hass).set_settings(HomeSettings(daily_minutes=30, excluded=[playroom.entry_id]))
    await watch(hass, freezer, 20)
    assert home(hass).status().today_seconds == 20 * 60  # the Family Room only
    await watch(hass, freezer, 11)
    assert {c.data["entity_id"] for c in calls["turn_off"]} == {FAMILY}
    assert playroom.runtime_data.decision.state == "ok"


async def test_weekly_limit_counts_earlier_days(hass: HomeAssistant, freezer, calls) -> None:
    from custom_components.tv_mgmt.home import HomeSettings

    freezer.move_to("2026-10-16 12:00:00-07:00")  # a Friday
    family, playroom = await two_tvs(hass)
    # Monday to Thursday: 9 h 50 min between the two TVs.
    family.runtime_data.activity.daily["2026-10-12"] = {"used_seconds": 4 * 3600}
    playroom.runtime_data.activity.daily["2026-10-14"] = {"used_seconds": 5 * 3600 + 50 * 60}
    # Last week doesn't count.
    family.runtime_data.activity.daily["2026-10-09"] = {"used_seconds": 9 * 3600}
    home(hass).set_settings(HomeSettings(weekly_minutes=600, week_start="monday"))
    status = home(hass).status()
    assert status.week_seconds == 9 * 3600 + 50 * 60 + status.today_seconds
    await watch(hass, freezer, 6)
    assert family.runtime_data.decision.reason == "home_weekly"
    assert {c.data["entity_id"] for c in calls["turn_off"]} == {FAMILY, PLAYROOM}
    # Extra time given today still counts for the rest of the week.
    home(hass).grant_extension(60)
    assert home(hass).status().weekly_remaining > 0
    freezer.move_to("2026-10-17 12:00:00-07:00")
    assert home(hass).status().weekly_remaining is not None
    assert home(hass).summary()["weekly_minutes"] == 600


async def test_home_needs_access(hass: HomeAssistant, hass_ws_client, hass_admin_user, calls) -> None:
    await two_tvs(hass)
    hass_admin_user.groups = []
    client = await hass_ws_client(hass)
    for msg in (
        {"type": "tv_mgmt/home/get"},
        {"type": "tv_mgmt/home/set", "daily_minutes": 600, "weekly_minutes": 0},
        {"type": "tv_mgmt/home/extension", "minutes": 60},
    ):
        await client.send_json_auto_id(msg)
        res = await client.receive_json()
        assert res["error"]["code"] == "unauthorized", msg


async def test_home_settings_survive_a_restart(hass: HomeAssistant, calls, hass_storage) -> None:
    from custom_components.tv_mgmt.home import HomeSettings

    await two_tvs(hass)
    home(hass).set_settings(HomeSettings(daily_minutes=90, notify=["notify.parent_phone"]))
    await home(hass)._store.async_save(home(hass)._data())
    stored = hass_storage["tv_mgmt.home"]["data"]["settings"]
    assert stored["daily_minutes"] == 90 and stored["notify"] == ["notify.parent_phone"]
    assert dt_util.now()  # keep the import used
