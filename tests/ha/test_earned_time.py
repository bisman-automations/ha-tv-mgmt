"""Empty means no limit; 0 means no time until extra time is given (1.20.0).

Kids earn their TV time: a TV or the whole home can start the day at zero,
and extra time (for a TV, the whole home, or both at once) is what lets the
TVs on.
"""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tv_mgmt.home import HomeSettings
from homeassistant.core import HomeAssistant
from tests.ha.test_home import FAMILY, PLAYROOM, home, set_tv

DOMAIN = "tv_mgmt"


async def _tv(hass, entity_id, name, budget, *, minor_version=2):
    set_tv(hass, entity_id, name)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        version=2,
        minor_version=minor_version,
        unique_id=entity_id,
        data={"media_player": entity_id},
        options={
            "input_lock": {"allowed_sources": ["HDMI 2"], "revert_delay": 1},
            "screen_time": {
                "daily_budget": budget,
                "warn_minutes": 5,
                "quiet_windows": "",
                "adult_mode_duration": 120,
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_old_zero_limit_still_means_no_limit(hass: HomeAssistant, calls) -> None:
    """Profiles saved before 1.20.0 with 0 keep having no limit."""
    entry = await _tv(hass, FAMILY, "Family Room TV", 0, minor_version=1)
    assert entry.minor_version == 2
    assert entry.options["screen_time"]["daily_budget"] is None
    assert entry.runtime_data.daily_budget is None
    assert entry.runtime_data.decision.state == "ok"


async def test_old_limit_is_kept(hass: HomeAssistant, calls) -> None:
    entry = await _tv(hass, FAMILY, "Family Room TV", 90, minor_version=1)
    assert entry.runtime_data.daily_budget == 90


async def test_zero_limit_needs_earned_time(
    hass: HomeAssistant, hass_ws_client, calls
) -> None:
    entry = await _tv(hass, FAMILY, "Family Room TV", 0)
    manager = entry.runtime_data
    assert manager.daily_budget == 0
    assert manager.decision.state == "enforcing"
    assert manager.decision.remaining_seconds == 0

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "tv_mgmt/action",
            "entry_id": entry.entry_id,
            "action": "grant_extension",
            "minutes": 30,
        }
    )
    res = await client.receive_json()
    assert res["success"]
    assert res["result"]["profile"]["budget_minutes"] == 0
    assert manager.decision.state == "ok"
    assert 29 * 60 < manager.decision.remaining_seconds <= 30 * 60

    # Empty in the app: no limit at all.
    await client.send_json_auto_id(
        {
            "type": "tv_mgmt/limits/set",
            "entry_id": entry.entry_id,
            "daily_budget": None,
        }
    )
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert manager.daily_budget is None
    assert manager.decision.remaining_seconds is None


async def test_home_zero_limit_and_extra_time_for_both(
    hass: HomeAssistant, hass_ws_client, calls
) -> None:
    family = await _tv(hass, FAMILY, "Family Room TV", 0)
    playroom = await _tv(hass, PLAYROOM, "Playroom TV", None)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "tv_mgmt/home/set", "daily_minutes": 0, "weekly_minutes": None}
    )
    res = await client.receive_json()
    assert res["success"]
    assert res["result"]["home"]["daily_minutes"] == 0
    assert res["result"]["home"]["weekly_minutes"] is None
    await hass.async_block_till_done()
    # No TV time for the home yet: both are off, the playroom by the home.
    assert family.runtime_data.decision.state == "enforcing"
    assert playroom.runtime_data.decision.state == "enforcing"
    assert playroom.runtime_data.decision.reason == "home_daily"

    # Extra time for the family room TV and the whole home at once.
    await client.send_json_auto_id(
        {
            "type": "tv_mgmt/action",
            "entry_id": family.entry_id,
            "action": "grant_extension",
            "minutes": 45,
            "whole_home": True,
        }
    )
    res = await client.receive_json()
    assert res["success"]
    assert res["result"]["home"]["extension_minutes"] == 45
    await hass.async_block_till_done()
    assert family.runtime_data.decision.state == "ok"
    assert playroom.runtime_data.decision.state == "ok"
    assert home(hass).settings.daily_minutes == 0

    # The action does the same.
    await hass.services.async_call(
        DOMAIN,
        "grant_extension",
        {"profile_id": family.entry_id, "minutes": 15, "whole_home": True},
        blocking=True,
    )
    assert family.runtime_data.state.extension_minutes == 60
    assert home(hass).status().extension_minutes == 60
    # Without it, only the TV gets the time.
    await hass.services.async_call(
        DOMAIN,
        "grant_extension",
        {"profile_id": family.entry_id, "minutes": 10},
        blocking=True,
    )
    assert family.runtime_data.state.extension_minutes == 70
    assert home(hass).status().extension_minutes == 60

    # A reason (from a chores reward) shows in the Activity, on the TV and the home.
    await hass.services.async_call(
        DOMAIN,
        "grant_extension",
        {
            "profile_id": family.entry_id,
            "minutes": 30,
            "whole_home": True,
            "reason": "Alex's reward: 30 minutes of TV",
        },
        blocking=True,
    )
    events = family.runtime_data.activity.events
    assert events[-2]["type"] == "extension"
    assert events[-2]["reason"] == "Alex's reward: 30 minutes of TV"
    assert events[-1]["type"] == "home_extension"
    assert events[-1]["reason"] == "Alex's reward: 30 minutes of TV"
    assert events[-1]["by"] is None


def test_home_settings_saved_before_zero_limits() -> None:
    # Before 1.20.0, 0 meant no limit; those settings are kept that way.
    old = HomeSettings.from_dict({"daily_minutes": 0, "weekly_minutes": 600})
    assert old.daily_minutes is None and old.weekly_minutes == 600
    assert old.active
    assert not HomeSettings.from_dict({"daily_minutes": 0}).active
    # Saved since: 0 is a limit of no time, empty is no limit.
    new = HomeSettings.from_dict(
        {**old.as_dict(), "daily_minutes": 0, "weekly_minutes": None}
    )
    assert new.daily_minutes == 0 and new.weekly_minutes is None
    assert new.active
    again = HomeSettings.from_dict(new.as_dict())
    assert again.daily_minutes == 0
