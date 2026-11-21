"""1.22.3: an update from the TV or Apple TV that changes nothing TV Mgmt
shows tells nobody, and entities are written only when what they show
changed."""

from custom_components.tv_mgmt.const import SIGNAL_ANY_UPDATED
from homeassistant.const import EVENT_STATE_CHANGED, EVENT_STATE_REPORTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .test_apple_tv import play, setup_box, tick
from .test_integration import SOURCES, TV, set_tv


async def test_play_position_alone_tells_nobody(
    hass: HomeAssistant, freezer, calls, box_calls
) -> None:
    set_tv(hass)
    play(hass, media_title="The Show", media_position=10)
    await setup_box(hass)
    play(hass, media_title="The Show", media_position=11)  # app picked up
    await hass.async_block_till_done()
    told: list[str] = []
    unsub = async_dispatcher_connect(
        hass, SIGNAL_ANY_UPDATED, callback(lambda entry_id: told.append(entry_id))
    )
    for position in (12, 14, 16):  # the Apple TV reports where it's at
        play(hass, media_title="The Show", media_position=position)
        await hass.async_block_till_done()
    hass.states.async_set(
        TV,
        "on",
        {
            "friendly_name": "Family Room TV",
            "source": "HDMI 2",
            "source_list": SOURCES,
            "volume_level": 0.4,  # the TV's volume isn't shown
        },
    )
    await hass.async_block_till_done()
    assert told == []
    play(hass, media_title="Another Show")
    await hass.async_block_till_done()
    assert told  # what's being watched is shown, so it's told at once
    told.clear()
    play(hass, state="standby")
    await hass.async_block_till_done()
    assert told  # so is the Apple TV going to sleep
    unsub()


async def test_entities_written_only_when_changed(
    hass: HomeAssistant, freezer, calls, box_calls
) -> None:
    set_tv(hass)
    play(hass, media_title="The Show")
    entry = await setup_box(hass)
    registry = er.async_get(hass)
    ours = {
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert ours
    changed: list[str] = []
    reported: list[str] = []

    @callback
    def _ours(event_data) -> bool:
        return event_data["entity_id"] in ours

    hass.bus.async_listen(
        EVENT_STATE_CHANGED,
        callback(lambda event: changed.append(event.data["entity_id"])),
        event_filter=_ours,
    )
    hass.bus.async_listen(
        EVENT_STATE_REPORTED,
        callback(lambda event: reported.append(event.data["entity_id"])),
        event_filter=_ours,
    )
    await tick(hass, freezer, 31)  # each entity's first update writes once
    changed.clear()
    reported.clear()
    for _ in range(4):  # two minutes of TV: today's minutes move
        await tick(hass, freezer, 31)
    assert changed  # the time used went up
    assert reported == []  # nothing was written again unchanged
    assert len(set(changed)) < len(ours)  # the mode and the switches weren't
