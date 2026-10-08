from datetime import timedelta
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant

from tests.ha.test_integration import set_tv, setup

DOMAIN = "tv_mgmt"


async def ws(hass_ws_client, hass, **msg):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(msg)
    return await client.receive_json()


async def test_profiles(hass: HomeAssistant, hass_ws_client, calls) -> None:
    set_tv(hass)
    entry = await setup(hass, budget=60)
    res = await ws(hass_ws_client, hass, type="tv_mgmt/profiles")
    assert res["success"]
    profile = res["result"]["profiles"][0]
    assert profile["entry_id"] == entry.entry_id
    assert profile["name"] == "Family Room TV"
    assert profile["is_on"] is True
    assert profile["current_input"] == "HDMI 2"
    assert profile["input_allowed"] is True
    assert profile["budget_minutes"] == 60
    assert profile["state"] == "ok"


async def test_actions_and_activity(hass: HomeAssistant, hass_ws_client, freezer, calls) -> None:
    set_tv(hass)
    entry = await setup(hass, budget=60)
    client = await hass_ws_client(hass)

    async def call(**msg):
        await client.send_json_auto_id(msg)
        return await client.receive_json()

    res = await call(type="tv_mgmt/action", entry_id=entry.entry_id, action="grant_extension", minutes=15)
    assert res["success"] and res["result"]["profile"]["extension_minutes"] == 15
    res = await call(type="tv_mgmt/action", entry_id=entry.entry_id, action="set_mode", mode="monitor_only")
    assert res["result"]["profile"]["mode"] == "monitor_only"
    res = await call(type="tv_mgmt/action", entry_id=entry.entry_id, action="set_mode")
    assert not res["success"]

    # After a while on HDMI 2, someone switches to YouTube, then turns the TV off.
    freezer.tick(timedelta(minutes=10))
    set_tv(hass, source="YouTube")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    set_tv(hass, state="off", source=None)
    await hass.async_block_till_done()

    res = await call(type="tv_mgmt/activity", entry_id=entry.entry_id)
    assert res["success"]
    types = [e["type"] for e in res["result"]["events"]]
    # Newest first.
    assert types[0] == "tv_off"
    for expected in ("tv_on", "input", "input_blocked", "extension", "mode"):
        assert expected in types
    sources = [s["source"] for s in res["result"]["segments"]]
    assert sources == ["HDMI 2", "YouTube"]

    res = await call(type="tv_mgmt/activity", entry_id=entry.entry_id, date="nope")
    assert not res["success"]


async def test_analytics(hass: HomeAssistant, hass_ws_client, calls) -> None:
    set_tv(hass)
    entry = await setup(hass, budget=60)
    res = await ws(hass_ws_client, hass, type="tv_mgmt/analytics", days=7)
    assert res["success"]
    profile = res["result"]["profiles"][0]
    assert profile["entry_id"] == entry.entry_id
    assert len(profile["days"]) == 7
    assert profile["days"][-1]["recorded"] is True
    assert profile["days"][-1]["budget_minutes"] == 60


async def test_limits_get_and_set(hass: HomeAssistant, hass_ws_client, calls) -> None:
    set_tv(hass)
    entry = await setup(hass, budget=60)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/limits/get", "entry_id": entry.entry_id})
    res = await client.receive_json()
    assert res["result"]["daily_budget"] == 60

    await client.send_json_auto_id(
        {"type": "tv_mgmt/limits/set", "entry_id": entry.entry_id, "quiet_windows": "bad"}
    )
    assert not (await client.receive_json())["success"]

    await client.send_json_auto_id(
        {"type": "tv_mgmt/limits/set", "entry_id": entry.entry_id, "daily_budget": 90,
         "quiet_windows": "20:30-07:00 Bedtime"}
    )
    res = await client.receive_json()
    assert res["success"]
    await hass.async_block_till_done()
    assert entry.options["screen_time"]["daily_budget"] == 90
    assert entry.options["screen_time"]["quiet_windows"] == "20:30-07:00 Bedtime"
    assert entry.options["input_lock"]["allowed_sources"] == ["HDMI 2"]
    assert entry.runtime_data.daily_budget == 90


async def test_writes_need_admin(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        {"type": "tv_mgmt/action", "entry_id": entry.entry_id, "action": "force_block"}
    )
    res = await client.receive_json()
    assert not res["success"] and res["error"]["code"] == "unauthorized"


async def test_subscribe_pushes_updates(hass: HomeAssistant, hass_ws_client, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "tv_mgmt/subscribe"})
    assert (await client.receive_json())["success"]
    entry.runtime_data.force_block()
    msg = await client.receive_json()
    assert msg["type"] == "event" and msg["event"]["entry_id"] == entry.entry_id


async def test_panel_registered_when_frontend_loaded(hass: HomeAssistant) -> None:
    from custom_components.tv_mgmt import panel

    hass.config.components.add("frontend")
    hass.http = AsyncMock()
    with patch(
        "homeassistant.components.panel_custom.async_register_panel", AsyncMock()
    ) as register:
        await panel.async_register_panel(hass)
        await panel.async_register_panel(hass)  # only once
    register.assert_awaited_once()
    kwargs = register.await_args.kwargs
    assert kwargs["frontend_url_path"] == "tv-mgmt"
    assert kwargs["require_admin"] is True
    assert kwargs["module_url"].startswith("/tv_mgmt_static/tv-mgmt-panel.js?v=")
