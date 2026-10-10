from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant

from tests.ha.test_apple_tv import BOX, DISNEY, SCREEN, YT, set_box
from tests.ha.test_integration import TV, set_tv, setup


def lock(**extra):
    return {"allowed_sources": [], "revert_delay": 1, "enforce_on_power_on": True, "max_attempts": 5, **extra}


async def setup_pinned(hass, *, allowed=None, apple_tv_input="HDMI 2", player=BOX):
    return await setup(
        hass,
        options={
            "input_lock": lock(allowed_sources=allowed or []),
            "screen_time": SCREEN,
            "apple_tv": {"streaming_player": player, "apple_tv_input": apple_tv_input,
                         "app_mode": "block", "apps": [], "app_limits": {}, "app_action": "home",
                         "sleep_on_block": True},
            "announcements": {}, "sync": {},
        },
    )


async def tick(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_apple_tv_input_is_the_lock(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, DISNEY)
    entry = await setup_pinned(hass)
    guard = entry.runtime_data.guard
    assert guard.allowed_sources == ["HDMI 2"]
    assert guard.target_source == "HDMI 2"

    # The TV's own YouTube app gets switched straight back to the Apple TV.
    set_tv(hass, source="YouTube")
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert calls["select_source"][-1].data == {"entity_id": TV, "source": "HDMI 2"}


async def test_pin_wins_over_other_target(hass: HomeAssistant, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, DISNEY)
    entry = await setup(
        hass,
        options={
            "input_lock": lock(allowed_sources=["HDMI 1"], target_source="HDMI 1"),
            "screen_time": SCREEN,
            "apple_tv": {"streaming_player": BOX, "apple_tv_input": "HDMI 2"},
            "announcements": {}, "sync": {},
        },
    )
    guard = entry.runtime_data.guard
    assert guard.allowed_sources == ["HDMI 2", "HDMI 1"]
    assert guard.target_source == "HDMI 2"


async def test_no_pin_without_apple_tv(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup_pinned(hass, allowed=["HDMI 1"], player=None)
    assert entry.runtime_data.guard.allowed_sources == ["HDMI 1"]


async def test_switches_to_apple_tv_when_it_wakes(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass, source="HDMI 1")
    set_box(hass, state="standby")
    entry = await setup_pinned(hass, allowed=["HDMI 1"])

    set_box(hass, YT)  # someone picks up the Apple TV remote
    await hass.async_block_till_done()
    assert calls["select_source"] == []
    await tick(hass, freezer, 4)
    assert calls["select_source"][-1].data == {"entity_id": TV, "source": "HDMI 2"}
    assert any(e["type"] == "follow" for e in entry.runtime_data.activity.events)


async def test_no_switch_if_cec_already_did(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass, source="HDMI 1")
    set_box(hass, state="standby")
    await setup_pinned(hass, allowed=["HDMI 1"])
    set_box(hass, YT)
    set_tv(hass, source="HDMI 2")
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)
    assert calls["select_source"] == []


async def test_no_switch_in_monitor_only_or_when_tv_off(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    set_tv(hass, source="HDMI 1")
    set_box(hass, state="standby")
    entry = await setup_pinned(hass, allowed=["HDMI 1"])
    entry.runtime_data.set_mode("monitor_only")
    set_box(hass, YT)
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)
    assert calls["select_source"] == []

    entry.runtime_data.set_mode("enforced")
    set_box(hass, state="standby")
    set_tv(hass, state="off", source=None)
    await hass.async_block_till_done()
    set_box(hass, YT)
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)
    assert calls["select_source"] == []


async def test_settings_allow_apple_tv_input_alone(hass: HomeAssistant, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass, DISNEY)
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    form = {
        "input_lock": lock(),
        "screen_time": SCREEN,
        "apple_tv": {"streaming_player": BOX, "apple_tv_input": "HDMI 2", "app_mode": "block",
                     "apps": [], "app_action": "home", "sleep_on_block": True},
        "announcements": {}, "sync": {},
    }
    done = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert done["type"] == "create_entry", done
    await hass.async_block_till_done()
    assert entry.options["apple_tv"]["apple_tv_input"] == "HDMI 2"
    assert entry.runtime_data.guard.allowed_sources == ["HDMI 2"]

    # Without the Apple TV, an empty allowed list is still an error.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    form["apple_tv"] = {"apple_tv_input": "HDMI 2", "app_mode": "block", "app_action": "home", "sleep_on_block": True}
    bad = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert bad["errors"] == {"base": "no_sources"}


async def test_android_tv_reports_app_but_switches_by_hdmi(
    hass: HomeAssistant, freezer, calls, box_calls, entity_registry
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.helpers import device_registry as dr

    android_entry = MockConfigEntry(domain="androidtv_remote")
    android_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=android_entry.entry_id, identifiers={("androidtv_remote", "tcl")}, name="Family Room TV"
    )
    entity_registry.async_get_or_create(
        "media_player", "androidtv_remote", "tcl", config_entry=android_entry, device_id=device.id,
        suggested_object_id="family_room_tv",
    )
    entity_registry.async_get_or_create(
        "remote", "androidtv_remote", "tcl", config_entry=android_entry, device_id=device.id,
        suggested_object_id="family_room_tv",
    )
    remote_calls = box_calls["remote"]
    hass.states.async_set(TV, "on", {"friendly_name": "Family Room TV", "app_id": "com.tcl.tv"})
    set_box(hass, DISNEY)
    entry = await setup(
        hass,
        options={
            "input_lock": lock(target_source="HDMI 2"),
            "screen_time": SCREEN,
            "apple_tv": {"streaming_player": BOX, "apple_tv_input": "com.tcl.tv"},
            "announcements": {}, "sync": {},
        },
    )
    guard = entry.runtime_data.guard
    assert guard.allowed_sources == ["com.tcl.tv"]
    assert guard.target_source == "HDMI 2"

    # Kids open the TV's own YouTube app: the TV is sent back with the HDMI 2 key.
    hass.states.async_set(TV, "on", {"friendly_name": "Family Room TV", "app_id": "com.google.android.youtube.tv"})
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert remote_calls[-1].data == {"entity_id": "remote.family_room_tv", "command": ["KEYCODE_TV_INPUT_HDMI_2"]}


def box_asleep(hass, features=128 | 256):
    hass.states.async_set(BOX, "standby", {"friendly_name": "Living Room Apple TV", "supported_features": features})


async def test_tv_on_wakes_apple_tv(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    from pytest_homeassistant_custom_component.common import async_mock_service

    turn_on = async_mock_service(hass, "media_player", "turn_on")
    set_tv(hass, state="off", source=None)
    box_asleep(hass)
    entry = await setup_pinned(hass)

    set_tv(hass, source="Live TV")
    await hass.async_block_till_done()
    assert [c.data for c in turn_on] == [{"entity_id": BOX}]
    assert entry.runtime_data.activity.events[-1]["type"] == "box_wake"

    # Already on: TV input changes don't wake it again.
    set_tv(hass, source="HDMI 2")
    await hass.async_block_till_done()
    assert len(turn_on) == 1

    # The Apple TV waking then brings the TV to its input if CEC didn't.
    set_tv(hass, source="Live TV")
    set_box(hass, DISNEY)
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)
    assert calls["select_source"][-1].data == {"entity_id": TV, "source": "HDMI 2"}


async def test_tv_on_doesnt_wake_when_off_or_not_enforced(hass: HomeAssistant, freezer, calls, box_calls) -> None:
    from pytest_homeassistant_custom_component.common import async_mock_service

    turn_on = async_mock_service(hass, "media_player", "turn_on")
    set_tv(hass, state="off", source=None)
    box_asleep(hass)
    entry = await setup_pinned(hass)
    manager = entry.runtime_data

    # Monitor only: leave it.
    manager.set_mode("monitor_only")
    set_tv(hass, source="Live TV")
    await hass.async_block_till_done()
    assert turn_on == []

    # Turned off in settings.
    manager.set_mode("enforced")
    set_tv(hass, state="off", source=None)
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "apple_tv": {**entry.options["apple_tv"], "wake_with_tv": False}}
    )
    await hass.async_block_till_done()
    set_tv(hass, source="Live TV")
    await hass.async_block_till_done()
    assert turn_on == []

    # Can't be woken (no turn-on support).
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "apple_tv": {**entry.options["apple_tv"], "wake_with_tv": True}}
    )
    await hass.async_block_till_done()
    set_tv(hass, state="off", source=None)
    box_asleep(hass, features=256)
    await hass.async_block_till_done()
    set_tv(hass, source="Live TV")
    await hass.async_block_till_done()
    assert turn_on == []
