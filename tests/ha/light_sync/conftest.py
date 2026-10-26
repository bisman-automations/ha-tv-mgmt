"""Fixtures for light sync: a TV with one light zone, set up through TV Mgmt."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

DOMAIN = "tv_mgmt"
ENTRY_ID = "tvlivingroom"
ZONE_ID = "zone1"
TV = "media_player.living_room_tv"
TV_TITLE = "Living Room TV"
# The zone follows this player (like an Apple TV's Music Assistant player).
MEDIA_PLAYER = "media_player.living_room_player"
LIGHT_COLOR = "light.tv_backlight"
LIGHT_WHITE = "light.floor_lamp"

# "TV Mgmt — Living Room TV · Lights" + the entity's name.
PREFIX = "tv_mgmt_living_room_tv_lights"
SWITCH = f"switch.{PREFIX}_light_sync"
SYNCING = f"binary_sensor.{PREFIX}_coloring_lights"
COLOR_SENSOR = f"sensor.{PREFIX}_current_color"
SOURCE_SENSOR = f"sensor.{PREFIX}_color_source"
RESYNC_BUTTON = f"button.{PREFIX}_look_again"
SAVE_BUTTON = f"button.{PREFIX}_keep_this_color_for_this_title"
BRIGHTNESS = f"number.{PREFIX}_brightness"
TRANSITION = f"number.{PREFIX}_transition"
COLOR_MODE = f"select.{PREFIX}_color_mode"
PRESET = f"select.{PREFIX}_where_colors_come_from"
METHOD = f"select.{PREFIX}_artwork_color"
STRATEGY = f"select.{PREFIX}_how_colors_are_spread"


def set_tv(hass: HomeAssistant, state: str = "on") -> None:
    """The TV (TV Mgmt's media player)."""
    hass.states.async_set(
        TV,
        state,
        {"friendly_name": TV_TITLE, "source": "HDMI 2" if state == "on" else None,
         "source_list": ["HDMI 1", "HDMI 2"]},
    )


def zone_config(**overrides: Any) -> dict[str, Any]:
    """A zone as the Lights tab saves it (no wait, no transition)."""
    return {
        "id": ZONE_ID,
        "name": "Lights",
        "lights": [LIGHT_COLOR, LIGHT_WHITE],
        "use_tv_player": False,
        "use_apple_tv": False,
        "extra_players": [MEDIA_PLAYER],
        "restore_delay": 0,
        "transition": 0,
        "persist_snapshot": False,
        **overrides,
    }


def tv_options_with(**sections: Any) -> dict[str, Any]:
    """TV options with input lock and screen time off, plus ``sections``."""
    return {
        "input_lock": {"allowed_sources": [], "revert_delay": 1,
                       "enforce_on_power_on": False, "max_attempts": 5},
        "screen_time": {"daily_budget": 0, "warn_minutes": 5,
                        "quiet_windows": "", "adult_mode_duration": 120},
        "apple_tv": {}, "announcements": {}, "lights": {}, "sync": {},
        **sections,
    }


def tv_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title=TV_TITLE,
        version=2,
        unique_id=TV,
        data={"media_player": TV},
        options=options or tv_options_with(),
    )


async def settle(hass: HomeAssistant) -> None:
    """Let the evaluate debouncer (and zero-delay idle timers) fire."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
def setup_lights(hass: HomeAssistant) -> None:
    """Fake light states: one colour light, one dimmable white light."""
    hass.states.async_set(
        LIGHT_COLOR,
        "on",
        {
            "supported_color_modes": ["hs", "color_temp"],
            "color_mode": "color_temp",
            "brightness": 120,
            "color_temp_kelvin": 2700,
            "supported_features": 32,
        },
    )
    hass.states.async_set(
        LIGHT_WHITE,
        "off",
        {"supported_color_modes": ["brightness"], "supported_features": 0},
    )


@pytest.fixture
def zone() -> dict[str, Any]:
    """The zone to set up (override in a test module to change it)."""
    return zone_config()


@pytest.fixture
def tv_options() -> dict[str, Any] | None:
    """The TV's own options (None: input lock and screen time off)."""
    return None


@pytest.fixture
def light_sync_settings() -> dict[str, Any]:
    """Home-wide settings to start with (TMDB / Trakt)."""
    return {}


@pytest.fixture
async def mocked_lights(hass: HomeAssistant) -> dict[str, list]:
    """Record light calls. Light is loaded first so its services don't replace the mocks."""
    assert await async_setup_component(hass, "media_player", {})
    assert await async_setup_component(hass, "light", {})
    calls = {
        "turn_on": async_mock_service(hass, "light", "turn_on"),
        "turn_off": async_mock_service(hass, "light", "turn_off"),
    }
    hass.data["turn_on_calls"] = calls["turn_on"]
    hass.data["turn_off_calls"] = calls["turn_off"]
    return calls


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_storage: dict[str, Any],
    mocked_lights: dict[str, list],
    setup_lights: None,
    zone: dict[str, Any],
    light_sync_settings: dict[str, Any],
    tv_options: dict[str, Any] | None,
) -> MockConfigEntry:
    """A TV with one light zone, with mocked light services and no real HTTP."""
    hass_storage["tv_mgmt.light_sync"] = {
        "version": 1,
        "key": "tv_mgmt.light_sync",
        "data": {"settings": light_sync_settings, "zones": {ENTRY_ID: [zone]}},
    }
    set_tv(hass)
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    entry = tv_entry(tv_options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def light_zone(entry: MockConfigEntry):
    """The entry's (only) light zone."""
    return entry.runtime_data.light_sync.zones[ZONE_ID]
