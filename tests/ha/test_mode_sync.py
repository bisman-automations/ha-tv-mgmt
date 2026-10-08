from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from tests.ha.test_integration import DOMAIN, TV, set_tv

ATV_MODE = "select.apple_tv_mgmt_living_room_mode"
OUR_MODE = "select.family_room_tv_mode"
OPTIONS = ["enforced", "monitor_only", "paused"]


def set_atv(hass, mode):
    hass.states.async_set(ATV_MODE, mode, {"options": OPTIONS})


async def setup_synced(hass, *, atv_mode="enforced"):
    assert await async_setup_component(hass, "select", {})
    select_calls = async_mock_service(hass, "select", "select_option")
    set_tv(hass)
    if atv_mode is not None:
        set_atv(hass, atv_mode)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Family Room TV",
        version=2,
        unique_id=TV,
        data={"media_player": TV},
        options={
            "input_lock": {"allowed_sources": ["HDMI 2"], "revert_delay": 1,
                           "enforce_on_power_on": True, "max_attempts": 5},
            "screen_time": {"daily_budget": 0, "warn_minutes": 5,
                            "quiet_windows": "", "adult_mode_duration": 120},
            "sync": {"mode_sync_entity": ATV_MODE},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, select_calls


async def test_follows_apple_tv_mgmt_at_startup(hass: HomeAssistant) -> None:
    _, calls = await setup_synced(hass, atv_mode="paused")
    assert hass.states.get(OUR_MODE).state == "paused"
    assert calls == []


async def test_follows_apple_tv_mgmt_changes(hass: HomeAssistant) -> None:
    _, calls = await setup_synced(hass)
    set_atv(hass, "monitor_only")
    await hass.async_block_till_done()
    assert hass.states.get(OUR_MODE).state == "monitor_only"
    assert calls == []  # no echo back


async def test_pushes_our_changes(hass: HomeAssistant) -> None:
    entry, calls = await setup_synced(hass)
    entry.runtime_data.set_mode("paused")
    await hass.async_block_till_done()
    assert [c.data for c in calls] == [{"entity_id": ATV_MODE, "option": "paused"}]


async def test_pushes_when_apple_tv_mgmt_comes_back(hass: HomeAssistant) -> None:
    entry, calls = await setup_synced(hass, atv_mode=None)
    entry.runtime_data.set_mode("monitor_only")
    await hass.async_block_till_done()
    assert calls == []
    # Apple TV Mgmt loads with its stored mode; ours changed meanwhile, so ours wins.
    set_atv(hass, "enforced")
    await hass.async_block_till_done()
    assert [c.data for c in calls] == [{"entity_id": ATV_MODE, "option": "monitor_only"}]
    assert hass.states.get(OUR_MODE).state == "monitor_only"


async def test_suggests_matching_profile(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    from custom_components.tv_mgmt.mode_sync import suggest_mode_select

    other = MockConfigEntry(domain="appletv_mgmt", options={"tv_entity_id": "media_player.bedroom_tv"})
    mine = MockConfigEntry(domain="appletv_mgmt", options={"tv_entity_id": TV})
    for entry, name in ((other, "bedroom"), (mine, "living_room")):
        entry.add_to_hass(hass)
        entity_registry.async_get_or_create(
            "select", "appletv_mgmt", f"profile_{name}_mode", config_entry=entry,
            suggested_object_id=f"apple_tv_mgmt_{name}_mode",
        )
    assert suggest_mode_select(hass, TV) == ATV_MODE
    assert suggest_mode_select(hass, "media_player.unknown") is None
