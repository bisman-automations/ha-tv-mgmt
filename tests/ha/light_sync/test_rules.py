"""How light sync and TV Mgmt share the TV's lights.

- The TV turning off turns its lights off; their pending restore is dropped.
- The snapshot is taken after TV Mgmt has turned the TV's lights on.
- Nothing is colored while TV Mgmt is keeping the TV off.
- When TV Mgmt stops a blocked app or show, the lights are restored.
- Zones follow the TV, the linked Apple TV, and any extra players.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant, ServiceCall

from .conftest import (
    LIGHT_COLOR,
    LIGHT_WHITE,
    MEDIA_PLAYER,
    SYNCING,
    TV,
    light_zone,
    set_tv,
    settle,
    tv_options_with,
    zone_config,
)

TITLE = "Blade Runner 2049"
RED = [255, 40, 0]
BOX = "media_player.living_room_apple_tv"
MUSIC = "media_player.living_room_apple_tv_music_assistant"
TV_LIGHTS = tv_options_with(lights={"tv_lights": [LIGHT_COLOR]})


def _to(calls: list[ServiceCall], entity_id: str) -> list[ServiceCall]:
    """Calls that include ``entity_id`` (alone or in a list)."""
    out = []
    for call in calls:
        target = call.data.get("entity_id")
        if target == entity_id or (isinstance(target, list) and entity_id in target):
            out.append(call)
    return out


async def _play(hass: HomeAssistant, player: str = MEDIA_PLAYER) -> None:
    hass.states.async_set(player, "playing", {"media_title": TITLE, "media_content_type": "movie"})
    await settle(hass)


@pytest.fixture
async def mapped(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> MockConfigEntry:
    await hass.services.async_call(
        "tv_mgmt", "set_title_color", {"title": TITLE, "rgb_color": RED}, blocking=True
    )
    return loaded_entry


@pytest.mark.parametrize("tv_options", [TV_LIGHTS])
async def test_tv_off_turns_tv_lights_off_and_drops_their_restore(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    on, off = mocked_lights["turn_on"], mocked_lights["turn_off"]
    await _play(hass)
    assert _to(on, LIGHT_COLOR)[-1].data["rgb_color"] == RED
    zone = light_zone(mapped)
    assert set(zone.snapshot) == {LIGHT_COLOR, LIGHT_WHITE}

    on.clear()
    off.clear()
    set_tv(hass, "off")
    await settle(hass)
    # TV Mgmt turned the TV light off, and light sync didn't turn it back on.
    assert _to(off, LIGHT_COLOR)
    assert not _to(on, LIGHT_COLOR)
    # The other light went back to how it was (off).
    assert _to(off, LIGHT_WHITE)
    assert zone.snapshot == {}
    assert hass.states.get(SYNCING).state == "off"
    assert hass.states.get(SYNCING).attributes["held_reason"] == "tv_off"

    # The player stopping later doesn't restore anything either.
    on.clear()
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await settle(hass)
    await settle(hass)
    assert not _to(on, LIGHT_COLOR)


async def test_no_coloring_while_the_tv_is_off(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    set_tv(hass, "off")
    await settle(hass)
    await _play(hass)
    assert not [c for c in mocked_lights["turn_on"] if "rgb_color" in c.data]
    assert hass.states.get(SYNCING).attributes["held_reason"] == "tv_off"

    # The TV coming on colors the lights for what's already playing.
    set_tv(hass)
    await settle(hass)
    assert _to(mocked_lights["turn_on"], LIGHT_COLOR)[-1].data["rgb_color"] == RED
    assert hass.states.get(SYNCING).attributes["held_reason"] is None


@pytest.mark.parametrize("zone", [zone_config(require_tv_on=False)])
async def test_tv_off_can_be_ignored(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    set_tv(hass, "off")
    await settle(hass)
    await _play(hass)
    assert _to(mocked_lights["turn_on"], LIGHT_COLOR)[-1].data["rgb_color"] == RED


async def test_no_coloring_while_blocked(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    mapped.runtime_data.force_block()
    await settle(hass)
    await _play(hass)
    assert not [c for c in mocked_lights["turn_on"] if "rgb_color" in c.data]
    assert hass.states.get(SYNCING).attributes["held_reason"] == "blocked"

    mapped.runtime_data.unblock()
    await settle(hass)
    assert _to(mocked_lights["turn_on"], LIGHT_COLOR)[-1].data["rgb_color"] == RED


async def test_blocked_app_restores_the_lights(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    on = mocked_lights["turn_on"]
    await _play(hass)
    assert hass.states.get(SYNCING).state == "on"
    on.clear()

    mapped.runtime_data._on_app_stopped("com.example.app", "Example", "blocked", "home", True)
    await hass.async_block_till_done(wait_background_tasks=True)
    restore = _to(on, LIGHT_COLOR)
    assert restore and restore[-1].data["color_temp_kelvin"] == 2700
    assert hass.states.get(SYNCING).state == "off"
    assert hass.states.get(SYNCING).attributes["held_reason"] == "stopped"

    # The player still reporting the title a moment later doesn't recolor.
    on.clear()
    hass.states.async_set(MEDIA_PLAYER, "playing", {"media_title": TITLE, "media_position": 3})
    await settle(hass)
    assert not [c for c in on if "rgb_color" in c.data]


async def test_stop_that_didnt_act_leaves_lights_alone(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    await _play(hass)
    mocked_lights["turn_on"].clear()
    mapped.runtime_data._on_app_stopped("com.example.app", "Example", "blocked", "none", False)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mocked_lights["turn_on"] == []
    assert hass.states.get(SYNCING).state == "on"


@pytest.mark.parametrize("tv_options", [TV_LIGHTS])
async def test_snapshot_waits_for_the_tv_lights(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    """A TV light still off right after the TV turns on is saved as on."""
    set_tv(hass, "off")
    hass.states.async_set(LIGHT_COLOR, "off", {"supported_color_modes": ["hs"]})
    await settle(hass)
    with patch("custom_components.tv_mgmt.light_sync.TV_LIGHTS_SETTLE", 0.05):
        set_tv(hass)
        await hass.async_block_till_done()
        # TV Mgmt asked for the TV light to turn on.
        assert _to(mocked_lights["turn_on"], LIGHT_COLOR)[-1].data == {"entity_id": [LIGHT_COLOR]}
        await _play(hass)
    zone = light_zone(mapped)
    assert zone.snapshot[LIGHT_COLOR]["state"] == "on"

    mocked_lights["turn_on"].clear()
    mocked_lights["turn_off"].clear()
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await settle(hass)
    await settle(hass)
    # Restored to on (TV Mgmt's job), never off under a TV that's on.
    assert _to(mocked_lights["turn_on"], LIGHT_COLOR)
    assert not _to(mocked_lights["turn_off"], LIGHT_COLOR)


@pytest.mark.parametrize(
    "tv_options",
    [tv_options_with(apple_tv={"streaming_player": BOX, "app_mode": "block", "apps": [], "app_limits": {}})],
)
@pytest.mark.parametrize("zone", [zone_config(use_tv_player=True, use_apple_tv=True, extra_players=[MUSIC])])
async def test_zone_follows_tv_apple_tv_and_extra_players(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    zone = light_zone(mapped)
    assert zone.media_players == [MUSIC, BOX, TV]

    # The Apple TV reports off while its Music Assistant player is playing.
    hass.states.async_set(BOX, "off", {})
    await _play(hass, MUSIC)
    assert _to(mocked_lights["turn_on"], LIGHT_COLOR)[-1].data["rgb_color"] == RED
    assert hass.states.get(SYNCING).attributes.get("media_player") == MUSIC


async def test_tv_lights_change_live(
    hass: HomeAssistant, mapped: MockConfigEntry, hass_ws_client
) -> None:
    """Changing the TV's lights doesn't reload the TV (zones keep running)."""
    zone = light_zone(mapped)
    client = await hass_ws_client(hass)
    with patch.object(hass.config_entries, "async_reload") as reload:
        await client.send_json_auto_id(
            {"type": "tv_mgmt/lights/set", "entry_id": mapped.entry_id, "lights": [LIGHT_COLOR]}
        )
        assert (await client.receive_json())["success"]
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert mapped.runtime_data.tv_lights == [LIGHT_COLOR]
    assert light_zone(mapped) is zone


async def test_limit_change_keeps_zones(
    hass: HomeAssistant, mapped: MockConfigEntry
) -> None:
    """Other settings apply live too: the zones keep running."""
    zone = light_zone(mapped)
    options: dict[str, Any] = dict(mapped.options)
    options["screen_time"] = {**options["screen_time"], "daily_budget": 60}
    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(mapped, options=options)
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert mapped.runtime_data.daily_budget == 60
    assert light_zone(mapped) is zone


async def test_linking_another_apple_tv_reloads(
    hass: HomeAssistant, mapped: MockConfigEntry
) -> None:
    options: dict[str, Any] = dict(mapped.options)
    options["apple_tv"] = {**options.get("apple_tv", {}), "streaming_player": "media_player.other_apple_tv"}
    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(mapped, options=options)
        await hass.async_block_till_done()
    reload.assert_called_once_with(mapped.entry_id)
