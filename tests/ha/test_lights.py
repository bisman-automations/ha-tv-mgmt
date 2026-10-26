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
    assert (await client.receive_json())["result"] == {"lights": ["light.lamp"], "follow": True}
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


# ---- turning them off by hand, and following the TV (1.16.0) ------------------------

BUTTON = "button.tv_mgmt_family_room_tv_turn_off_tv_lights"
FOLLOW = "switch.tv_mgmt_family_room_tv_tv_lights_follow_the_tv"


async def test_turn_off_now(hass: HomeAssistant, hass_ws_client, calls) -> None:
    off = async_mock_service(hass, "light", "turn_off")
    set_tv(hass)
    entry = await setup(hass, options=options())
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/lights/off", "entry_id": entry.entry_id})
    res = (await client.receive_json())["result"]
    assert res["lights"] == LIGHTS and res["failed"] == {}
    assert res["profile"]["tv_lights_held_off"] is True
    # One call per light, so one that doesn't answer doesn't hold up the others.
    assert sorted(c.data["entity_id"] for c in off) == sorted(LIGHTS)
    assert entry.runtime_data.activity.events[-1]["type"] == "tv_lights_off"

    # The button and the service do the same.
    off.clear()
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    assert len(off) == 2
    off.clear()
    result = await hass.services.async_call(
        "tv_mgmt", "turn_off_tv_lights", {"profile_id": entry.entry_id}, blocking=True, return_response=True
    )
    assert result == {"lights": LIGHTS, "failed": {}} and len(off) == 2

    # The hold ends when the TV next turns off (or on).
    set_tv(hass, state="off", source=None)
    await hass.async_block_till_done()
    assert entry.runtime_data.tv_lights_held_off is False


async def test_turn_off_reports_a_light_that_failed(hass: HomeAssistant, hass_ws_client, calls) -> None:
    from homeassistant.exceptions import HomeAssistantError

    done = []

    async def turn_off(call):
        if call.data["entity_id"] == "light.lamp":
            raise HomeAssistantError("Lamp is offline")
        done.append(call.data["entity_id"])

    hass.services.async_register("light", "turn_off", turn_off)
    set_tv(hass)
    entry = await setup(hass, options=options())
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/lights/off", "entry_id": entry.entry_id})
    res = (await client.receive_json())["result"]
    assert done == ["light.tv_backlight"]  # the other light still went off
    assert res["failed"] == {"light.lamp": "Lamp is offline"}
    assert entry.runtime_data.activity.events[-1] == {**entry.runtime_data.activity.events[-1], "failed": 1}


async def test_nothing_to_turn_off(hass: HomeAssistant, hass_ws_client, calls) -> None:
    off = async_mock_service(hass, "light", "turn_off")
    set_tv(hass)
    entry = await setup(hass, options=options(lights=[]))
    assert hass.states.get(BUTTON).state == "unavailable"
    assert hass.states.get(FOLLOW).state == "unavailable"
    assert await entry.runtime_data.async_turn_off_tv_lights() == {"lights": [], "failed": {}}
    assert off == []


async def test_follow_can_be_turned_off_live(hass: HomeAssistant, hass_ws_client, calls) -> None:
    from unittest.mock import patch

    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    set_tv(hass, state="off", source=None)
    entry = await setup(hass, options=options())
    manager = entry.runtime_data
    assert hass.states.get(FOLLOW).state == "on"
    with patch.object(hass.config_entries, "async_reload") as reload:
        await hass.services.async_call("switch", "turn_off", {"entity_id": FOLLOW}, blocking=True)
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert entry.runtime_data is manager
    assert entry.options["lights"] == {"tv_lights": LIGHTS, "tv_lights_follow": False}
    assert hass.states.get(FOLLOW).state == "off"

    # The TV turning on and off leaves the lights alone now.
    set_tv(hass)
    await hass.async_block_till_done()
    set_tv(hass, state="off", source=None)
    await hass.async_block_till_done()
    assert on == [] and off == []

    # And back on from the sidebar app.
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/lights/set", "entry_id": entry.entry_id, "follow": True})
    assert (await client.receive_json())["result"] == {"lights": LIGHTS, "follow": True}
    await hass.async_block_till_done()
    assert entry.options["lights"]["tv_lights"] == LIGHTS  # the lights are kept
    set_tv(hass)
    await hass.async_block_till_done()
    assert [c.data for c in on] == [{"entity_id": LIGHTS}]
    await client.send_json_auto_id({"type": "tv_mgmt/profiles"})
    assert (await client.receive_json())["result"]["profiles"][0]["tv_lights_follow"] is True
