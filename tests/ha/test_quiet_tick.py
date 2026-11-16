"""1.22.1: a tick that changes nothing (the TV is off) tells nobody; one
where the TV is on and time is counted does."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.tv_mgmt.const import SIGNAL_ANY_UPDATED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .test_integration import set_tv, setup


async def test_quiet_tick_tells_nobody(hass: HomeAssistant, calls, freezer) -> None:
    set_tv(hass, state="off")
    await setup(hass, budget=60)
    told: list[str] = []
    unsub = async_dispatcher_connect(
        hass, SIGNAL_ANY_UPDATED, callback(lambda entry_id: told.append(entry_id))
    )
    for _ in range(4):
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert told == []  # two minutes with the TV off: nothing to tell

    set_tv(hass, state="on")
    await hass.async_block_till_done()
    told.clear()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert told  # the TV is on: time used changed, so the dashboards hear
    unsub()
