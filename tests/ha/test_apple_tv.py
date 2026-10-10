from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed
import pytest

from homeassistant.core import HomeAssistant

from tests.ha.test_integration import set_tv, setup

BOX = "media_player.living_room_apple_tv"
REMOTE = "remote.living_room_apple_tv"
YT = ("com.google.ios.youtube", "YouTube")
DISNEY = ("com.disney.disneyplus", "Disney+")
HOME = ("com.apple.HeadBoard", "Home")

LOCK = {"allowed_sources": ["HDMI 2"], "revert_delay": 1, "enforce_on_power_on": True, "max_attempts": 5}
SCREEN = {"daily_budget": 0, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120}


def set_box(hass, app=None, state="playing"):
    attrs = {"friendly_name": "Living Room Apple TV", "source_list": ["Disney+", "Netflix", "YouTube"]}
    if app:
        attrs["app_id"], attrs["app_name"] = app
    hass.states.async_set(BOX, state, attrs)


async def setup_box(hass, **apple_tv):
    options = {
        "input_lock": LOCK,
        "screen_time": SCREEN,
        "apple_tv": {"streaming_player": BOX, "app_mode": "block", "apps": [], "app_limits": {},
                     "app_action": "home", "sleep_on_block": True, **apple_tv},
        "announcements": {}, "sync": {},
    }
    return await setup(hass, options=options)


async def tick(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_tracks_time_per_app(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    entry = await setup_box(hass)
    assert hass.states.get("sensor.tv_mgmt_family_room_tv_current_app").state == "YouTube"

    for _ in range(20):  # 10 minutes
        await tick(hass, freezer, 30)
    set_box(hass, HOME, state="idle")
    await hass.async_block_till_done()
    for _ in range(10):  # home screen isn't app time
        await tick(hass, freezer, 30)

    assert hass.states.get("sensor.tv_mgmt_family_room_tv_current_app").state == "Home screen"
    app_time = hass.states.get("sensor.tv_mgmt_family_room_tv_app_time_today")
    assert float(app_time.state) == pytest.approx(10, abs=0.6)
    assert app_time.attributes["apps"]["YouTube"] == pytest.approx(10, abs=0.6)
    assert entry.runtime_data.state.known_apps[YT[0]] == "YouTube"

    set_box(hass, state="standby")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tv_mgmt_family_room_tv_current_app").state == "Asleep"


async def test_blocked_app_goes_home(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, HOME, state="idle")
    entry = await setup_box(hass, apps=["YouTube"])
    events = []
    hass.bus.async_listen("tv_mgmt_app_blocked", events.append)

    set_box(hass, DISNEY)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert box_calls["remote"] == []

    set_box(hass, YT)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert [c.data for c in box_calls["remote"]] == [{"entity_id": REMOTE, "command": ["home"]}]
    assert events[0].data["app_name"] == "YouTube"
    assert events[0].data["reason"] == "blocked" and events[0].data["acted"] is True
    assert entry.runtime_data.state.apps_stopped == 1
    types = [e["type"] for e in entry.runtime_data.activity.events]
    assert "app_stopped" in types


async def test_allow_only_mode(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, DISNEY)
    await setup_box(hass, app_mode="allow", apps=["Disney+"])
    await tick(hass, freezer, 3)
    assert box_calls["remote"] == []
    set_box(hass, YT)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3)
    assert len(box_calls["remote"]) == 1


async def test_per_app_limit(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    events = []
    hass.bus.async_listen("tv_mgmt_app_blocked", events.append)
    await setup_box(hass, app_limits={"YouTube": 1})
    await tick(hass, freezer, 30)
    assert box_calls["remote"] == []
    await tick(hass, freezer, 30)
    await tick(hass, freezer, 3)
    assert len(box_calls["remote"]) == 1
    assert events[0].data["reason"] == "limit"
    attrs = hass.states.get("sensor.tv_mgmt_family_room_tv_current_app").attributes
    assert attrs["limit_minutes"] == 1 and attrs["allowed"] is False


async def test_sleep_action(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    await setup_box(hass, apps=["YouTube"], app_action="sleep")
    await tick(hass, freezer, 3)
    assert {"entity_id": BOX} in [c.data for c in calls["turn_off"]]
    assert box_calls["remote"] == []


async def test_monitor_only_reports_without_acting(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    events = []
    hass.bus.async_listen("tv_mgmt_app_blocked", events.append)
    entry = await setup_box(hass, apps=["YouTube"])
    entry.runtime_data.set_mode("monitor_only")
    await tick(hass, freezer, 3)
    assert box_calls["remote"] == []
    assert len(events) == 1 and events[0].data["acted"] is False


async def test_adult_mode_allows_everything(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    entry = await setup_box(hass, apps=["YouTube"])
    entry.runtime_data.set_adult_mode(True)
    await tick(hass, freezer, 5)
    assert box_calls["remote"] == []


async def test_block_puts_apple_tv_to_sleep(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, DISNEY)
    entry = await setup_box(hass)
    entry.runtime_data.force_block()
    await hass.async_block_till_done()
    assert {"entity_id": BOX} in [c.data for c in calls["turn_off"]]
    assert any(e["type"] == "box_sleep" for e in entry.runtime_data.activity.events)


async def test_no_sleep_when_turned_off(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, DISNEY)
    entry = await setup_box(hass, sleep_on_block=False)
    entry.runtime_data.force_block()
    await hass.async_block_till_done()
    assert {"entity_id": BOX} not in [c.data for c in calls["turn_off"]]


async def test_without_remote_falls_back_to_sleep(hass: HomeAssistant, freezer, calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    await setup_box(hass, apps=["YouTube"])
    await tick(hass, freezer, 3)
    assert {"entity_id": BOX} in [c.data for c in calls["turn_off"]]


async def test_no_apple_tv_no_app_sensors(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    await setup(hass)
    assert hass.states.get("sensor.tv_mgmt_family_room_tv_current_app") is None


async def test_options_flow_apple_tv_section(hass: HomeAssistant, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    form = {"input_lock": LOCK, "screen_time": SCREEN, "announcements": {}, "sync": {},
            "apple_tv": {"streaming_player": BOX, "app_mode": "allow", "apps": ["Disney+"],
                         "app_limits": "Disney+ = nope", "app_action": "home", "sleep_on_block": True}}
    bad = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert bad["errors"] == {"base": "bad_app_limits"}

    form["apple_tv"]["app_limits"] = "Disney+ = 45"
    done = await hass.config_entries.options.async_configure(bad["flow_id"], form)
    assert done["type"] == "create_entry"
    await hass.async_block_till_done()
    saved = entry.options["apple_tv"]
    assert saved["streaming_player"] == BOX
    assert saved["app_limits"] == {"Disney+": 45}
    assert entry.runtime_data.box is not None
    assert entry.runtime_data.box.rules.mode == "allow"


async def test_panel_api(hass: HomeAssistant, hass_ws_client, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    entry = await setup_box(hass, app_limits={"YouTube": 30})
    for _ in range(4):
        await tick(hass, freezer, 30)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "tv_mgmt/profiles"})
    atv = (await client.receive_json())["result"]["profiles"][0]["apple_tv"]
    assert atv["app_name"] == "YouTube" and atv["is_on"] is True
    assert atv["apps_today"][0]["name"] == "YouTube"
    assert atv["apps_today"][0]["limit_minutes"] == 30
    assert "Disney+" in atv["known_apps"].values()
    assert atv["has_remote"] is True
    assert atv["remote_entity"] == REMOTE

    await client.send_json_auto_id({"type": "tv_mgmt/activity", "entry_id": entry.entry_id})
    activity = (await client.receive_json())["result"]
    assert activity["app_segments"][0]["name"] == "YouTube"

    await client.send_json_auto_id({"type": "tv_mgmt/analytics", "entry_id": entry.entry_id, "days": 7})
    summary = (await client.receive_json())["result"]["profiles"][0]["summary"]
    assert summary["top_apps"][0]["name"] == "YouTube"

    await client.send_json_auto_id(
        {"type": "tv_mgmt/apple_tv/set", "entry_id": entry.entry_id, "mode": "allow",
         "apps": ["Disney+", " "], "limits": {"Disney+": 45, "YouTube": 0}, "action": "sleep"}
    )
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    saved = entry.options["apple_tv"]
    assert saved["app_mode"] == "allow" and saved["apps"] == ["Disney+"]
    assert saved["app_limits"] == {"Disney+": 45}
    assert saved["app_action"] == "sleep"
    assert saved["streaming_player"] == BOX
    assert entry.runtime_data.box.rules.mode == "allow"

    await client.send_json_auto_id(
        {"type": "tv_mgmt/apple_tv/set", "entry_id": entry.entry_id, "action": "grant_extension"}
    )
    assert not (await client.receive_json())["success"]


async def test_panel_api_without_apple_tv(hass: HomeAssistant, hass_ws_client, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/apple_tv/set", "entry_id": entry.entry_id, "mode": "allow"})
    res = await client.receive_json()
    assert not res["success"] and res["error"]["code"] == "not_supported"


async def test_diagnostics(hass: HomeAssistant, calls, box_calls) -> None:
    from custom_components.tv_mgmt.diagnostics import async_get_config_entry_diagnostics

    set_tv(hass)
    set_box(hass, YT)
    entry = await setup_box(hass, apps=["Roblox"])
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["apple_tv"]["app_name"] == "YouTube"
    assert diag["apple_tv"]["rules"]["apps"] == ["Roblox"]
    assert diag["apple_tv"]["box_state"]["entity_id"] == BOX


PRIME = ("com.amazon.aiv.AIVApp", "Prime Video")


def play(hass, state="playing", **media):
    attrs = {"friendly_name": "Living Room Apple TV", "app_id": PRIME[0], "app_name": PRIME[1], **media}
    hass.states.async_set(BOX, state, attrs)


async def test_tracks_what_is_watched(hass: HomeAssistant, freezer, hass_ws_client, calls, box_calls) -> None:
    set_tv(hass)
    play(hass, media_title="Genevieve's Playhouse", media_artist="Season 2, Ep. 14 Learn Vehicle Names")
    entry = await setup_box(hass)
    manager = entry.runtime_data
    for _ in range(20):  # 10 minutes playing
        await tick(hass, freezer, 30)
    play(hass, state="paused", media_title="Genevieve's Playhouse", media_artist="Season 2, Ep. 14 Learn Vehicle Names")
    await hass.async_block_till_done()
    for _ in range(10):  # paused doesn't count
        await tick(hass, freezer, 30)
    play(hass, media_title="Genevieve's Playhouse", media_artist="Season 2, Ep. 15 Colors")
    await hass.async_block_till_done()
    for _ in range(10):
        await tick(hass, freezer, 30)
    play(hass, media_title="Moana")
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)

    assert manager.state.media_seconds["Genevieve's Playhouse"] == pytest.approx(15 * 60, abs=40)
    assert manager.state.media_seconds["Moana"] == pytest.approx(60, abs=5)
    media = [e for e in manager.activity.events if e["type"] == "media"]
    assert [(e.get("series"), e.get("episode"), e["title"]) for e in media] == [
        ("Genevieve's Playhouse", 14, "Learn Vehicle Names"),
        ("Genevieve's Playhouse", 15, "Colors"),
        (None, None, "Moana"),
    ]
    assert media[0]["name"] == "Prime Video"

    current = hass.states.get("sensor.tv_mgmt_family_room_tv_current_app")
    assert current.attributes["now_watching"] == "Moana"
    shows = hass.states.get("sensor.tv_mgmt_family_room_tv_app_time_today").attributes["shows"]
    assert list(shows) == ["Genevieve's Playhouse", "Moana"]

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/profiles"})
    atv = (await client.receive_json())["result"]["profiles"][0]["apple_tv"]
    assert atv["media_today"][0]["show"] == "Genevieve's Playhouse"
    assert atv["media_today"][0]["app"] == "Prime Video"
    await client.send_json_auto_id({"type": "tv_mgmt/analytics", "entry_id": entry.entry_id, "days": 7})
    summary = (await client.receive_json())["result"]["profiles"][0]["summary"]
    assert summary["top_media"][0]["show"] == "Genevieve's Playhouse"
    assert summary["top_media"][0]["app"] == "Prime Video"
