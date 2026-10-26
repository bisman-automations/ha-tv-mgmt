"""Saving settings applies them to the running TV, without reloading it.

A reload would drop the TV's timers (an input switch or turn-off that's
pending), re-create its entities and stop its light sync zones. Only linking
another Apple TV, or a changed TV or name, reloads.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from tests.ha.test_apple_tv import BOX, YT, set_box, setup_box, tick
from tests.ha.test_integration import TV, set_tv, setup

DOMAIN = "tv_mgmt"
STATE = "sensor.tv_mgmt_family_room_tv_enforcement_state"


def _options(entry: MockConfigEntry, section: str, **changes: Any) -> dict[str, Any]:
    options = {k: (dict(v) if isinstance(v, dict) else v) for k, v in entry.options.items()}
    options.setdefault(section, {}).update(changes)
    return options


async def _ws(hass_ws_client, hass: HomeAssistant, **msg: Any) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(msg)
    res = await client.receive_json()
    await hass.async_block_till_done()
    assert res["success"], res
    return res["result"]


async def test_limits_apply_live(hass: HomeAssistant, freezer, calls, hass_ws_client) -> None:
    """A new daily limit is used right away, by the same TV and entities."""
    set_tv(hass)
    entry = await setup(hass)
    manager = entry.runtime_data
    manager.state.used_seconds = 120  # 2 minutes watched today
    with patch.object(hass.config_entries, "async_reload") as reload:
        await _ws(hass_ws_client, hass, type=f"{DOMAIN}/limits/set", entry_id=entry.entry_id,
                  daily_budget=1, warn_minutes=0)
    reload.assert_not_called()
    assert entry.runtime_data is manager
    assert manager.daily_budget == 1
    assert manager.state.used_seconds >= 120  # today's time carried on
    assert hass.states.get(STATE).state == "enforcing"  # over the new limit at once
    await tick(hass, freezer, 4)
    assert len(calls["turn_off"]) == 1
    # Raising it again unblocks.
    hass.config_entries.async_update_entry(entry, options=_options(entry, "screen_time", daily_budget=120))
    await hass.async_block_till_done()
    assert hass.states.get(STATE).state == "ok"


async def test_quiet_window_applies_live(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    now = dt_util.now()
    window = f"{(now - timedelta(hours=1)):%H:%M}-{(now + timedelta(hours=1)):%H:%M}"  # around now
    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(
            entry, options=_options(entry, "screen_time", quiet_windows=window)
        )
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert [w.format() for w in entry.runtime_data.quiet_windows] == [window]
    assert hass.states.get(STATE).state == "enforcing"


async def test_pending_turn_off_survives_a_save(hass: HomeAssistant, freezer, calls, hass_ws_client) -> None:
    """Saving while the TV is about to be turned off doesn't cancel it."""
    set_tv(hass)
    entry = await setup(hass)
    await hass.services.async_call(DOMAIN, "force_block", {"profile_id": entry.entry_id}, blocking=True)
    pending = entry.runtime_data._unsub_turn_off
    assert pending is not None
    with patch.object(hass.config_entries, "async_reload") as reload:
        await _ws(hass_ws_client, hass, type=f"{DOMAIN}/input_names/set", entry_id=entry.entry_id,
                  names={"HDMI 2": "Apple TV"})
    reload.assert_not_called()
    assert entry.runtime_data._unsub_turn_off is pending
    assert entry.runtime_data.name_for("HDMI 2") == "Apple TV"
    await tick(hass, freezer, 4)
    assert len(calls["turn_off"]) == 1
    assert entry.runtime_data.state.force_block  # today's state carried on


async def test_pending_input_switch_survives_an_unrelated_save(
    hass: HomeAssistant, freezer, calls, hass_ws_client
) -> None:
    set_tv(hass)
    entry = await setup(hass)
    set_tv(hass, source="YouTube")
    await hass.async_block_till_done()
    timer = entry.runtime_data.guard._unsub_timer
    assert timer is not None
    await _ws(hass_ws_client, hass, type=f"{DOMAIN}/limits/set", entry_id=entry.entry_id, warn_minutes=10)
    assert entry.runtime_data.guard._unsub_timer is timer
    await tick(hass, freezer, 2)
    assert [c.data for c in calls["select_source"]] == [{"entity_id": TV, "source": "HDMI 2"}]


async def test_allowed_inputs_apply_live(hass: HomeAssistant, freezer, calls) -> None:
    """Changing the input lock (options flow or app) re-checks the TV's input."""
    set_tv(hass, source="HDMI 1")
    entry = await setup(hass, options={
        "input_lock": {"allowed_sources": ["HDMI 1", "HDMI 2"], "target_source": "HDMI 2",
                       "revert_delay": 1, "enforce_on_power_on": True, "max_attempts": 5},
        "screen_time": {"daily_budget": 0, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120},
    })
    await tick(hass, freezer, 2)
    assert calls["select_source"] == []
    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(
            entry, options=_options(entry, "input_lock", allowed_sources=["HDMI 2"])
        )
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert entry.runtime_data.guard.allowed_sources == ["HDMI 2"]
    await tick(hass, freezer, 2)
    assert [c.data for c in calls["select_source"]] == [{"entity_id": TV, "source": "HDMI 2"}]


async def test_app_rules_apply_live(hass: HomeAssistant, freezer, calls, box_calls, hass_ws_client) -> None:
    set_tv(hass)
    set_box(hass, YT)
    entry = await setup_box(hass)
    box = entry.runtime_data.box
    await tick(hass, freezer, 3)
    assert box_calls["remote"] == []
    with patch.object(hass.config_entries, "async_reload") as reload:
        await _ws(hass_ws_client, hass, type=f"{DOMAIN}/apple_tv/set", entry_id=entry.entry_id, apps=["YouTube"])
    reload.assert_not_called()
    assert entry.runtime_data.box is box
    await tick(hass, freezer, 3)
    assert len(box_calls["remote"]) == 1  # YouTube, open now, is stopped


async def test_announcements_apply_live(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    assert entry.runtime_data.announcer.screen is None
    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(
            entry, options=_options(entry, "announcements", announce_screen="notify.family_room_tv")
        )
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert entry.runtime_data.announcer.screen == "notify.family_room_tv"


async def test_linking_an_apple_tv_reloads(hass: HomeAssistant, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, YT)
    entry = await setup(hass)
    old = entry.runtime_data
    assert old.box is None
    hass.config_entries.async_update_entry(entry, options=_options(entry, "apple_tv", streaming_player=BOX))
    await hass.async_block_till_done()
    assert entry.runtime_data is not old
    assert entry.runtime_data.box is not None
    assert hass.states.get("sensor.tv_mgmt_family_room_tv_current_app") is not None


async def test_renaming_reloads(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(
            entry, title="Den TV", options=_options(entry, "screen_time", daily_budget=30)
        )
        await hass.async_block_till_done()
    reload.assert_called_once_with(entry.entry_id)
