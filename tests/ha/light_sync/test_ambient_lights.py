"""The lights the dashboard draws around each TV."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tv_mgmt.websocket import ambient_lights, profile_summary
from homeassistant.core import HomeAssistant

from .conftest import LIGHT_COLOR, LIGHT_WHITE, light_zone, settle
from .test_rules import TV_LIGHTS


@pytest.mark.parametrize("tv_options", [TV_LIGHTS])
async def test_tv_lights_and_zone_lights_once_each(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    manager = loaded_entry.runtime_data
    # The TV light is also in the zone; it's listed once, first.
    assert ambient_lights(manager) == [LIGHT_COLOR, LIGHT_WHITE]
    assert profile_summary(manager)["ambient_lights"] == [LIGHT_COLOR, LIGHT_WHITE]

    # A turned-off zone's lights aren't part of the TV's glow.
    await light_zone(loaded_entry).async_set_enabled(False)
    await settle(hass)
    assert ambient_lights(manager) == [LIGHT_COLOR]
