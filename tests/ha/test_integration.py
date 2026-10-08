from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

DOMAIN = "tv_mgmt"
TV = "media_player.family_room_tv"
SOURCES = ["HDMI 1", "HDMI 2", "Live TV", "YouTube"]


def set_tv(hass, state="on", source="HDMI 2"):
    hass.states.async_set(
        TV, state, {"friendly_name": "Family Room TV", "source": source, "source_list": SOURCES}
    )


async def setup(hass, *, budget=0, quiet="", version=2, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Family Room TV",
        version=version,
        unique_id=TV,
        data={"media_player": TV},
        options=options
        or {
            "input_lock": {
                "allowed_sources": ["HDMI 2"],
                "revert_delay": 1,
                "enforce_on_power_on": True,
                "max_attempts": 5,
            },
            "screen_time": {
                "daily_budget": budget,
                "warn_minutes": 5,
                "quiet_windows": quiet,
                "adult_mode_duration": 120,
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def calls(hass):
    """Record TV service calls.

    Load media_player first so its real services don't replace the mocks.
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "media_player", {})
    return {
        "select_source": async_mock_service(hass, "media_player", "select_source"),
        "turn_off": async_mock_service(hass, "media_player", "turn_off"),
    }


async def test_entities_created(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    await setup(hass)
    for entity_id in (
        "select.family_room_tv_mode",
        "switch.family_room_tv_input_lock",
        "switch.family_room_tv_adult_mode",
        "sensor.family_room_tv_enforcement_state",
        "sensor.family_room_tv_time_used_today",
        "sensor.family_room_tv_current_input",
        "sensor.family_room_tv_blocked_switches_today",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get("sensor.family_room_tv_enforcement_state").state == "ok"
    assert hass.states.get("sensor.family_room_tv_current_input").state == "HDMI 2"


async def test_input_lock_switches_back(hass: HomeAssistant, freezer: FrozenDateTimeFactory, calls) -> None:
    set_tv(hass)
    await setup(hass)
    events = []
    hass.bus.async_listen("tv_mgmt_input_blocked", events.append)

    set_tv(hass, source="YouTube")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(calls["select_source"]) == 1
    assert calls["select_source"][0].data == {"entity_id": TV, "source": "HDMI 2"}
    assert events[0].data["blocked_source"] == "YouTube"
    assert events[0].data["reverted"] is True
    assert hass.states.get("sensor.family_room_tv_blocked_switches_today").state == "1"


async def test_input_lock_off_does_nothing(hass: HomeAssistant, freezer, calls) -> None:
    set_tv(hass)
    await setup(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.family_room_tv_input_lock"}, blocking=True)
    set_tv(hass, source="YouTube")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls["select_source"] == []


async def test_monitor_only_reports_without_acting(hass: HomeAssistant, freezer, calls) -> None:
    set_tv(hass)
    await setup(hass)
    events = []
    hass.bus.async_listen("tv_mgmt_input_blocked", events.append)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.family_room_tv_mode", "option": "monitor_only"}, blocking=True
    )
    set_tv(hass, source="YouTube")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls["select_source"] == []
    assert len(events) == 1 and events[0].data["reverted"] is False


async def test_force_block_turns_tv_off_and_unblock(hass: HomeAssistant, freezer, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    await hass.services.async_call(DOMAIN, "force_block", {"profile_id": entry.entry_id}, blocking=True)
    assert hass.states.get("sensor.family_room_tv_enforcement_state").state == "enforcing"
    freezer.tick(timedelta(seconds=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls["turn_off"]) == 1

    await hass.services.async_call(DOMAIN, "unblock", {"profile_id": entry.entry_id}, blocking=True)
    assert hass.states.get("sensor.family_room_tv_enforcement_state").state == "ok"


async def test_budget_counts_and_enforces(hass: HomeAssistant, freezer, calls) -> None:
    freezer.move_to("2026-10-08 15:00:00-05:00")
    set_tv(hass)
    entry = await setup(hass, budget=1)
    for _ in range(5):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get("sensor.family_room_tv_enforcement_state").state == "enforcing"
    assert len(calls["turn_off"]) >= 1

    # Extra time unblocks.
    await hass.services.async_call(DOMAIN, "grant_extension", {"profile_id": entry.entry_id, "minutes": 30}, blocking=True)
    assert hass.states.get("sensor.family_room_tv_enforcement_state").state == "ok"


async def test_adult_mode_lifts_block(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    await hass.services.async_call(DOMAIN, "force_block", {"profile_id": entry.entry_id}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.family_room_tv_adult_mode"}, blocking=True)
    assert hass.states.get("sensor.family_room_tv_enforcement_state").state == "adult_mode"


async def test_bad_profile_id(hass: HomeAssistant, calls) -> None:
    from homeassistant.exceptions import ServiceValidationError

    set_tv(hass)
    await setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "force_block", {"profile_id": "nope"}, blocking=True)


async def test_migrate_from_0_1(hass: HomeAssistant, entity_registry: er.EntityRegistry, calls) -> None:
    set_tv(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Family Room TV",
        version=1,
        unique_id=TV,
        data={"media_player": TV},
        options={"allowed_sources": ["HDMI 2"], "revert_delay": 2, "enforce_on_power_on": True, "max_attempts": 5},
    )
    entry.add_to_hass(hass)
    old_switch = entity_registry.async_get_or_create(
        "switch", DOMAIN, f"{entry.entry_id}_lock", config_entry=entry,
        suggested_object_id="family_room_tv_input_lock",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.options["input_lock"]["allowed_sources"] == ["HDMI 2"]
    migrated = entity_registry.async_get(old_switch.entity_id)
    assert migrated.unique_id == f"{entry.entry_id}_input_lock"
    assert hass.states.get(old_switch.entity_id).state == "on"


async def test_options_flow(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "input_lock": {"allowed_sources": ["HDMI 1"], "revert_delay": 2, "enforce_on_power_on": True, "max_attempts": 5},
            "screen_time": {"daily_budget": 90, "warn_minutes": 10, "quiet_windows": "bad", "adult_mode_duration": 60},
        },
    )
    assert result["errors"] == {"base": "bad_quiet_windows"}
