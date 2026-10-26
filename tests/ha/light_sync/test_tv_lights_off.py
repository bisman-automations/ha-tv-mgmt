"""Turning the TV lights off by hand, and the TV lights not following the TV.

- Turned off from the panel, the button or the service, a zone colouring them
  forgets how they were (so the movie ending doesn't turn them back on) and
  leaves them off until the TV next turns on or off. Its other lights carry on.
- With "TV lights follow the TV" off they're like the zone's other lights:
  restored to how they were, not dropped when the TV turns off.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from .conftest import (
    LIGHT_COLOR,
    LIGHT_WHITE,
    MEDIA_PLAYER,
    light_zone,
    set_tv,
    settle,
    tv_options_with,
)
from .test_rules import RED, TITLE, TV_LIGHTS, _play, _to

BUTTON = "button.tv_mgmt_living_room_tv_turn_off_tv_lights"
OTHER = "Arrival"
NOT_FOLLOWING = tv_options_with(
    lights={"tv_lights": [LIGHT_COLOR], "tv_lights_follow": False}
)


@pytest.fixture
async def mapped(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> MockConfigEntry:
    for title in (TITLE, OTHER):
        await hass.services.async_call(
            "tv_mgmt",
            "set_title_color",
            {"title": title, "rgb_color": RED},
            blocking=True,
        )
    return loaded_entry


@pytest.mark.parametrize("tv_options", [TV_LIGHTS])
async def test_turned_off_stay_off(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    on, off = mocked_lights["turn_on"], mocked_lights["turn_off"]
    await _play(hass)
    zone = light_zone(mapped)
    assert set(zone.snapshot) == {LIGHT_COLOR, LIGHT_WHITE}

    on.clear()
    await hass.services.async_call(
        "button", "press", {"entity_id": BUTTON}, blocking=True
    )
    await settle(hass)
    assert _to(off, LIGHT_COLOR)
    assert set(zone.snapshot) == {LIGHT_WHITE}  # its restore is dropped

    # Something else playing colours the other light, not the TV light.
    hass.states.async_set(
        MEDIA_PLAYER, "playing", {"media_title": OTHER, "media_content_type": "movie"}
    )
    await settle(hass)
    assert _to(on, LIGHT_WHITE)
    assert not _to(on, LIGHT_COLOR)

    # Stopping restores the other light only.
    off.clear()
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await settle(hass)
    await settle(hass)
    assert not _to(on, LIGHT_COLOR)
    assert _to(off, LIGHT_WHITE)  # back to off, as it was

    # The TV turning off and on again ends the hold: the TV light is coloured again.
    set_tv(hass, "off")
    await settle(hass)
    set_tv(hass)
    await settle(hass)
    assert mapped.runtime_data.tv_lights_held_off is False
    on.clear()
    await _play(hass)
    assert _to(on, LIGHT_COLOR)[-1].data["rgb_color"] == RED


@pytest.mark.parametrize("tv_options", [NOT_FOLLOWING])
async def test_not_following_restores_them(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    on, off = mocked_lights["turn_on"], mocked_lights["turn_off"]
    await _play(hass)
    zone = light_zone(mapped)
    assert LIGHT_COLOR in zone.snapshot

    on.clear()
    off.clear()
    set_tv(hass, "off")
    await settle(hass)
    # TV Mgmt left the TV light alone, and the zone put it back how it was (on).
    assert not [c for c in off if c.data.get("entity_id") == [LIGHT_COLOR]]
    restored = _to(on, LIGHT_COLOR)
    assert restored and restored[-1].data.get("color_temp_kelvin") == 2700
    assert zone.snapshot == {}


@pytest.mark.parametrize("tv_options", [TV_LIGHTS])
async def test_follow_switch_applies_live(
    hass: HomeAssistant, mapped: MockConfigEntry, mocked_lights: dict[str, list]
) -> None:
    zone = light_zone(mapped)
    follow = "switch.tv_mgmt_living_room_tv_tv_lights_follow_the_tv"
    with patch.object(hass.config_entries, "async_reload") as reload:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": follow}, blocking=True
        )
        await settle(hass)
    reload.assert_not_called()
    assert light_zone(mapped) is zone
    assert mapped.runtime_data.light_sync.tv_lights() == []
    assert mapped.options["lights"]["tv_lights_follow"] is False
