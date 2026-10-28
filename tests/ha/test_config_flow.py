from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from tests.ha.test_integration import TV, set_tv, setup

DOMAIN = "tv_mgmt"


async def test_flow_tv_on(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "media_player.family_room_tv",
        "on",
        {
            "friendly_name": "Family Room TV",
            "source": "HDMI 2",
            "source_list": ["HDMI 1", "HDMI 2", "Live TV"],
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"media_player": "media_player.family_room_tv"}
    )
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "input_lock": {
                "allowed_sources": ["HDMI 2"],
                "revert_delay": 2,
                "enforce_on_power_on": True,
                "max_attempts": 5,
            },
            "screen_time": {
                "daily_budget": 60,
                "warn_minutes": 5,
                "quiet_windows": "20:30-07:00 Bedtime",
                "adult_mode_duration": 120,
            },
            "apple_tv": {},
            "announcements": {},
            "lights": {},
            "sync": {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()


async def test_flow_tv_off(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "media_player.family_room_tv", "off", {"friendly_name": "Family Room TV"}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"media_player": "media_player.family_room_tv"}
    )
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == "settings"


async def test_half_done_setup_doesnt_block_starting_over(hass: HomeAssistant) -> None:
    """The app closed partway through adding a TV: adding it again works."""
    hass.states.async_set(
        "media_player.family_room_tv",
        "on",
        {"source": "HDMI 2", "source_list": ["HDMI 1", "HDMI 2"]},
    )
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    first = await hass.config_entries.flow.async_configure(
        first["flow_id"], {"media_player": "media_player.family_room_tv"}
    )
    assert first["step_id"] == "settings"

    again = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], {"media_player": "media_player.family_room_tv"}
    )
    assert again["type"] is FlowResultType.FORM and again["step_id"] == "settings"
    done = await hass.config_entries.flow.async_configure(
        again["flow_id"],
        {
            "input_lock": {
                "allowed_sources": ["HDMI 2"],
                "revert_delay": 2,
                "enforce_on_power_on": True,
                "max_attempts": 5,
            },
            "screen_time": {
                "daily_budget": 60,
                "warn_minutes": 5,
                "quiet_windows": "",
                "adult_mode_duration": 120,
            },
            "apple_tv": {},
            "announcements": {},
            "lights": {},
            "sync": {},
        },
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # The abandoned setup is cleared away, so it can't add the TV twice.
    assert not [
        f
        for f in hass.config_entries.flow.async_progress()
        if f["flow_id"] == first["flow_id"]
    ]


async def test_reconfigure_points_at_a_different_tv(
    hass: HomeAssistant, calls, caplog
) -> None:
    """Reconfigure changes the TV once, with the same one-profile check as setup."""
    set_tv(hass)
    entry = await setup(hass)
    hass.states.async_set(
        "media_player.den_tv",
        "on",
        {"friendly_name": "Den TV", "source": "HDMI 1", "source_list": ["HDMI 1"]},
    )
    caplog.clear()

    reloads: list[str] = []
    real_reload = hass.config_entries.async_reload

    async def _reload(entry_id: str) -> bool:
        reloads.append(entry_id)
        return await real_reload(entry_id)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["description_placeholders"]["profile"] == "Family Room TV"

    with patch.object(hass.config_entries, "async_reload", _reload):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"media_player": "media_player.den_tv"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.data["media_player"] == "media_player.den_tv"
    assert entry.unique_id == "media_player.den_tv"
    assert entry.title == "Den TV"
    assert entry.runtime_data.tv_entity_id == "media_player.den_tv"
    # The flow reloads once. The update listener is detached first, so it
    # does not reload as well (Home Assistant 2026.12).
    assert reloads == [entry.entry_id]
    assert "Detected that custom integration 'tv_mgmt'" not in caplog.text
    # The listener is back, so a later options change still applies.
    assert entry.update_listeners


async def test_reconfigure_unchanged_tv_does_not_reload(
    hass: HomeAssistant, calls, caplog
) -> None:
    set_tv(hass)
    entry = await setup(hass)
    caplog.clear()
    reloads: list[str] = []

    async def _reload(entry_id: str) -> bool:
        reloads.append(entry_id)
        return True

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch.object(hass.config_entries, "async_reload", _reload):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"media_player": TV}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert reloads == []
    assert entry.update_listeners
    assert "Detected that custom integration 'tv_mgmt'" not in caplog.text


async def test_reconfigure_rejects_a_tv_that_already_has_a_profile(
    hass: HomeAssistant, calls
) -> None:
    set_tv(hass)
    entry = await setup(hass)
    hass.states.async_set(
        "media_player.den_tv",
        "on",
        {"friendly_name": "Den TV", "source_list": ["HDMI 1"]},
    )
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    other = MockConfigEntry(
        domain=DOMAIN,
        title="Den TV",
        unique_id="media_player.den_tv",
        data={"media_player": "media_player.den_tv"},
    )
    other.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"media_player": "media_player.den_tv"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert (
        hass.config_entries.async_get_entry(entry.entry_id).data["media_player"] == TV
    )
