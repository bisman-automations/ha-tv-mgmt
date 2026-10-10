"""Warnings said on speakers and shown on the TV screen."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from homeassistant.core import HomeAssistant

from tests.ha.test_apple_tv import BOX, YT, set_box
from tests.ha.test_integration import TV, set_tv, setup

LOCK = {"allowed_sources": ["HDMI 2"], "revert_delay": 1, "enforce_on_power_on": True, "max_attempts": 5}


async def tick(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_warns_then_announces_turning_off(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    speak = async_mock_service(hass, "tts", "speak")
    screen = async_mock_service(hass, "notify", "family_room_tv")
    box_off = calls["turn_off"]
    set_tv(hass)
    set_box(hass, YT)
    await setup(hass, options={
        "input_lock": LOCK,
        "screen_time": {"daily_budget": 10, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120},
        "apple_tv": {"streaming_player": BOX, "sleep_on_block": True},
        "announcements": {"announce_tts": "tts.piper", "announce_players": ["media_player.homepod"],
                          "announce_screen": "notify.family_room_tv"},
        "sync": {},
    })

    for _ in range(10):  # 5 minutes: 5 left
        await tick(hass, freezer, 30)
    assert [c.data["message"] for c in speak] == ["5 minutes of TV time left."]
    assert speak[0].data["entity_id"] == "tts.piper"
    assert speak[0].data["media_player_entity_id"] == ["media_player.homepod"]
    assert screen[0].data == {"message": "5 minutes of TV time left.", "title": "TV Mgmt"}

    for _ in range(10):  # time's up
        await tick(hass, freezer, 30)
    assert speak[-1].data["message"] == "TV time is up for today. The TV is turning off."
    assert screen[-1].data["message"] == "TV time is up for today. The TV is turning off."
    # Nothing is turned off while the message plays...
    await tick(hass, freezer, 5)
    assert box_off == []
    # ...then the Apple TV and TV go off.
    await tick(hass, freezer, 6)
    assert {c.data["entity_id"] for c in box_off} == {BOX, TV}


async def test_no_announcements_set(hass: HomeAssistant, freezer, calls) -> None:
    speak = async_mock_service(hass, "tts", "speak")
    set_tv(hass)
    await setup(hass, budget=1)
    for _ in range(4):
        await tick(hass, freezer, 30)
    assert speak == []
    assert calls["turn_off"]  # turned off right away, without waiting


async def test_airplays_message_to_apple_tv(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    hass.config.internal_url = "http://homeassistant.local:8123"
    play = async_mock_service(hass, "media_player", "play_media")
    set_tv(hass)
    set_box(hass, YT)
    await setup(hass, options={
        "input_lock": LOCK,
        "screen_time": {"daily_budget": 10, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120},
        "apple_tv": {"streaming_player": BOX, "sleep_on_block": True},
        "announcements": {"announce_airplay": True},
        "sync": {},
    })
    for _ in range(10):
        await tick(hass, freezer, 30)
    assert play[0].data == {
        "entity_id": BOX,
        "media_content_id": "http://homeassistant.local:8123/tv_mgmt_static/airplay/left-5.mp4",
        "media_content_type": "video",
    }
    for _ in range(10):
        await tick(hass, freezer, 30)
    assert play[-1].data["media_content_id"].endswith("/airplay/time-up.mp4")
    assert calls["turn_off"] == []  # waits for it to play
    await tick(hass, freezer, 11)
    assert calls["turn_off"]


def test_slides_exist() -> None:
    import pathlib

    folder = pathlib.Path(__file__).parents[2] / "custom_components/tv_mgmt/frontend/airplay"
    from custom_components.tv_mgmt.announce import WARN_SLIDE_MINUTES, warning_slide

    names = {"almost-up", "time-up", "quiet-time", *(warning_slide(m) for m in WARN_SLIDE_MINUTES)}
    for name in names:
        assert (folder / f"{name}.mp4").stat().st_size > 10_000
    assert warning_slide(7) == "left-7" and warning_slide(17) == "almost-up"
