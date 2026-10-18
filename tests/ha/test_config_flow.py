from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

DOMAIN = "tv_mgmt"


async def test_flow_tv_on(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "media_player.family_room_tv",
        "on",
        {"friendly_name": "Family Room TV", "source": "HDMI 2", "source_list": ["HDMI 1", "HDMI 2", "Live TV"]},
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"media_player": "media_player.family_room_tv"}
    )
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "input_lock": {"allowed_sources": ["HDMI 2"], "revert_delay": 2, "enforce_on_power_on": True, "max_attempts": 5},
            "screen_time": {"daily_budget": 60, "warn_minutes": 5, "quiet_windows": "20:30-07:00 Bedtime", "adult_mode_duration": 120},
            "apple_tv": {}, "announcements": {}, "lights": {}, "sync": {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()


async def test_flow_tv_off(hass: HomeAssistant) -> None:
    hass.states.async_set("media_player.family_room_tv", "off", {"friendly_name": "Family Room TV"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"media_player": "media_player.family_room_tv"}
    )
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == "settings"


async def test_half_done_setup_doesnt_block_starting_over(hass: HomeAssistant) -> None:
    """The app closed partway through adding a TV: adding it again works."""
    hass.states.async_set("media_player.family_room_tv", "on", {"source": "HDMI 2", "source_list": ["HDMI 1", "HDMI 2"]})
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    first = await hass.config_entries.flow.async_configure(first["flow_id"], {"media_player": "media_player.family_room_tv"})
    assert first["step_id"] == "settings"

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    again = await hass.config_entries.flow.async_configure(again["flow_id"], {"media_player": "media_player.family_room_tv"})
    assert again["type"] is FlowResultType.FORM and again["step_id"] == "settings"
    done = await hass.config_entries.flow.async_configure(
        again["flow_id"],
        {
            "input_lock": {"allowed_sources": ["HDMI 2"], "revert_delay": 2, "enforce_on_power_on": True, "max_attempts": 5},
            "screen_time": {"daily_budget": 60, "warn_minutes": 5, "quiet_windows": "", "adult_mode_duration": 120},
            "apple_tv": {}, "announcements": {}, "lights": {}, "sync": {},
        },
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # The abandoned setup is cleared away, so it can't add the TV twice.
    assert not [f for f in hass.config_entries.flow.async_progress() if f["flow_id"] == first["flow_id"]]
