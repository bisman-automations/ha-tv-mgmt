"""TV lights: on with the TV, off with it."""

from pytest_homeassistant_custom_component.common import async_mock_service

from homeassistant.core import HomeAssistant

from tests.ha.test_integration import set_tv, setup

LIGHTS = ["light.tv_backlight", "light.lamp"]
LOCK = {"allowed_sources": ["HDMI 2"], "revert_delay": 1, "enforce_on_power_on": True, "max_attempts": 5}


def options(lights=LIGHTS, budget=0):
    return {
        "input_lock": LOCK,
        "screen_time": {"daily_budget": budget, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120},
        "apple_tv": {},
        "announcements": {},
        "lights": {"tv_lights": lights},
        "sync": {},
    }


async def test_lights_follow_the_tv(hass: HomeAssistant, calls) -> None:
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    set_tv(hass, state="off", source=None)
    await setup(hass, options=options())
    assert on == [] and off == []

    set_tv(hass)
    await hass.async_block_till_done()
    assert [c.data for c in on] == [{"entity_id": LIGHTS}]

    # Changing input isn't turning on.
    set_tv(hass, source="HDMI 1")
    await hass.async_block_till_done()
    assert len(on) == 1

    set_tv(hass, state="off", source=None)
    await hass.async_block_till_done()
    assert [c.data for c in off] == [{"entity_id": LIGHTS}]

    # Unreachable isn't off.
    set_tv(hass, state="unavailable", source=None)
    await hass.async_block_till_done()
    assert len(off) == 1


async def test_blocked_tv_doesnt_turn_lights_on(hass: HomeAssistant, calls) -> None:
    on = async_mock_service(hass, "light", "turn_on")
    set_tv(hass, state="off", source=None)
    entry = await setup(hass, options=options())
    entry.runtime_data.force_block()
    set_tv(hass)
    await hass.async_block_till_done()
    assert on == []


async def test_set_lights_from_the_sidebar_app(hass: HomeAssistant, hass_ws_client, calls) -> None:
    set_tv(hass)
    entry = await setup(hass, options=options(lights=[]))
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "tv_mgmt/lights/set", "entry_id": entry.entry_id, "lights": ["light.lamp", "light.lamp"]}
    )
    assert (await client.receive_json())["result"] == {"lights": ["light.lamp"]}
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.options["lights"] == {"tv_lights": ["light.lamp"]}
    assert entry.runtime_data.tv_lights == ["light.lamp"]
    await client.send_json_auto_id({"type": "tv_mgmt/profiles"})
    assert (await client.receive_json())["result"]["profiles"][0]["tv_lights"] == ["light.lamp"]
    # Only lights.
    await client.send_json_auto_id(
        {"type": "tv_mgmt/lights/set", "entry_id": entry.entry_id, "lights": ["switch.heater"]}
    )
    assert not (await client.receive_json())["success"]
