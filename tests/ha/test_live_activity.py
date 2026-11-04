"""The TV's Live Activity on the parents' phones (1.21.0)."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from tests.ha.test_home import FAMILY, set_tv

DOMAIN = "tv_mgmt"
PHONE = "mobile_app_parent_phone"


async def _tv(hass, budget=60, phones=(PHONE,)):
    set_tv(hass, FAMILY, "Family Room TV")
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
                "daily_budget": budget,
                "warn_minutes": 5,
                "quiet_windows": "",
                "adult_mode_duration": 120,
            },
            "announcements": {"live_activity": list(phones)},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _settle(hass):
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_shows_time_left_while_the_tv_is_on(hass: HomeAssistant, calls) -> None:
    pushes = async_mock_service(hass, "notify", PHONE)
    entry = await _tv(hass)
    manager = entry.runtime_data

    # The TV is on: the activity starts, counting down to the end of the hour.
    assert len(pushes) == 1
    data = pushes[0].data
    assert data["title"] == "Family Room TV"
    assert data["data"]["tag"] == f"tv-mgmt-{entry.entry_id}"
    assert data["data"]["critical_text"] == "Time left today"
    assert data["data"]["chronometer"] is True
    first_end = data["data"]["when"]
    assert 58 * 60 <= first_end - dt_util.utcnow().timestamp() <= 60 * 60

    # Nothing visible changed: no push.
    manager.notify()
    await _settle(hass)
    assert len(pushes) == 1

    # Extra time (from the dashboard, or a Family reward) moves the end.
    manager.grant_extension(30)
    await _settle(hass)
    assert len(pushes) == 2
    assert pushes[1].data["data"]["when"] - first_end >= 28 * 60

    # The TV turns off: "TV off", then it's cleared a minute later.
    set_tv(hass, FAMILY, "Family Room TV", state="off")
    await _settle(hass)
    assert pushes[-1].data["data"]["critical_text"] == "TV off"
    assert "chronometer" not in pushes[-1].data["data"]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await _settle(hass)
    assert pushes[-1].data["message"] == "clear_notification"
    count = len(pushes)

    # On again: a new one starts.
    set_tv(hass, FAMILY, "Family Room TV")
    await _settle(hass)
    assert len(pushes) == count + 1
    assert pushes[-1].data["data"]["critical_text"] == "Time left today"


async def test_earned_time_on_a_zero_limit(hass: HomeAssistant, calls) -> None:
    pushes = async_mock_service(hass, "notify", PHONE)
    entry = await _tv(hass, budget=0)
    assert pushes[0].data["data"]["critical_text"] == "Time's up"
    assert "chronometer" not in pushes[0].data["data"]

    # Time earned with chores: the countdown starts.
    entry.runtime_data.grant_extension(20)
    await _settle(hass)
    assert pushes[-1].data["data"]["critical_text"] == "Time left today"
    assert pushes[-1].data["data"]["chronometer"] is True


async def test_no_phones_no_activity(hass: HomeAssistant, calls) -> None:
    pushes = async_mock_service(hass, "notify", PHONE)
    await _tv(hass, phones=())
    assert pushes == []


async def test_phones_set_from_the_sidebar(
    hass: HomeAssistant, hass_ws_client, calls
) -> None:
    pushes = async_mock_service(hass, "notify", PHONE)
    other = async_mock_service(hass, "notify", "mobile_app_other_phone")
    entry = await _tv(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "tv_mgmt/limits/get", "entry_id": entry.entry_id}
    )
    res = (await client.receive_json())["result"]
    assert res["live_activity"] == [PHONE]
    assert {p["service"] for p in res["phones"]} == {
        PHONE,
        "mobile_app_other_phone",
    }

    # Moved to the other phone, without reloading the TV.
    await client.send_json_auto_id(
        {
            "type": "tv_mgmt/limits/set",
            "entry_id": entry.entry_id,
            "live_activity": ["mobile_app_other_phone"],
        }
    )
    assert (await client.receive_json())["success"]
    await _settle(hass)
    assert entry.options["announcements"]["live_activity"] == ["mobile_app_other_phone"]
    assert entry.options["screen_time"]["daily_budget"] == 60
    assert pushes[-1].data["message"] == "clear_notification"
    assert other[-1].data["data"]["critical_text"] == "Time left today"
    assert entry.runtime_data is not None
