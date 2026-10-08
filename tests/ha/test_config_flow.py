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
