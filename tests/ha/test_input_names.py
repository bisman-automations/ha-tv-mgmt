from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant

from tests.ha.test_integration import TV, set_tv, setup

SENSOR = "sensor.tv_mgmt_family_room_tv_current_input"
LOCK = {"allowed_sources": ["HDMI 2"], "revert_delay": 1, "enforce_on_power_on": True, "max_attempts": 5}
SCREEN = {"daily_budget": 0, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120}


async def setup_named(hass, names):
    return await setup(
        hass,
        options={"input_lock": {**LOCK, "input_names": names}, "screen_time": SCREEN, "apple_tv": {}, "announcements": {}, "sync": {}},
    )


async def test_sensor_and_attributes_use_names(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    await setup_named(hass, {"HDMI 2": "Apple TV"})
    state = hass.states.get(SENSOR)
    assert state.state == "Apple TV"
    assert state.attributes["source"] == "HDMI 2"
    assert state.attributes["allowed"] is True
    lock = hass.states.get("switch.tv_mgmt_family_room_tv_input_lock")
    assert lock.attributes["allowed_inputs"] == ["Apple TV"]


async def test_known_android_names(hass: HomeAssistant, calls) -> None:
    set_tv(hass, source="com.google.android.youtube.tv")
    await setup_named(hass, {})
    assert hass.states.get(SENSOR).state == "YouTube"


async def test_lock_still_matches_raw_value(hass: HomeAssistant, freezer, calls) -> None:
    set_tv(hass)
    await setup_named(hass, {"HDMI 2": "Apple TV", "YouTube": "Kids YouTube"})
    events = []
    hass.bus.async_listen("tv_mgmt_input_blocked", events.append)
    set_tv(hass, source="YouTube")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls["select_source"][0].data == {"entity_id": TV, "source": "HDMI 2"}
    data = events[0].data
    assert data["blocked_source"] == "YouTube" and data["blocked_source_name"] == "Kids YouTube"
    assert data["target_source"] == "HDMI 2" and data["target_source_name"] == "Apple TV"


async def test_options_flow_edits_names_as_text(hass: HomeAssistant, calls) -> None:
    from tests.ha.form import fields as serialize

    set_tv(hass)
    entry = await setup_named(hass, {"HDMI 2": "Apple TV"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = serialize(result["data_schema"])
    lock = next(f for f in fields if f["name"] == "input_lock")["schema"]
    names_field = next(f for f in lock if f["name"] == "input_names")
    assert names_field["description"]["suggested_value"] == "HDMI 2 = Apple TV"
    allowed = next(f for f in lock if f["name"] == "allowed_sources")
    labels = {o["value"]: o["label"] for o in allowed["selector"]["select"]["options"]}
    assert labels["HDMI 2"] == "Apple TV (HDMI 2)"

    bad = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"input_lock": {**LOCK, "input_names": "HDMI 2"}, "screen_time": SCREEN, "apple_tv": {}, "announcements": {}, "sync": {}},
    )
    assert bad["errors"] == {"base": "bad_input_names"}

    done = await hass.config_entries.options.async_configure(
        bad["flow_id"],
        {"input_lock": {**LOCK, "input_names": "HDMI 2 = Apple TV\nHDMI 1 = Xbox"}, "screen_time": SCREEN, "apple_tv": {}, "announcements": {}, "sync": {}},
    )
    assert done["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["input_lock"]["input_names"] == {"HDMI 2": "Apple TV", "HDMI 1": "Xbox"}


async def test_panel_api_lists_and_saves_names(hass: HomeAssistant, hass_ws_client, calls) -> None:
    set_tv(hass)
    entry = await setup_named(hass, {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/profiles"})
    profile = (await client.receive_json())["result"]["profiles"][0]
    assert profile["input_names"]["HDMI 2"] == "HDMI 2"

    await client.send_json_auto_id(
        {"type": "tv_mgmt/input_names/set", "entry_id": entry.entry_id,
         "names": {"HDMI 2": "Apple TV", "HDMI 1": "  ", "Live TV": "Live TV"}}
    )
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.options["input_lock"]["input_names"] == {"HDMI 2": "Apple TV"}
    # Other settings are untouched.
    assert entry.options["input_lock"]["allowed_sources"] == ["HDMI 2"]
    assert hass.states.get(SENSOR).state == "Apple TV"

    await client.send_json_auto_id({"type": "tv_mgmt/profiles"})
    profile = (await client.receive_json())["result"]["profiles"][0]
    assert profile["input_names"]["HDMI 2"] == "Apple TV"
    assert profile["custom_input_names"] == {"HDMI 2": "Apple TV"}
