"""Who may use TV Mgmt: admins, plus the parents an admin allowed."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from tests.ha.test_integration import set_tv, setup

LOCK = "switch.tv_mgmt_family_room_tv_input_lock"
ADULT = "switch.tv_mgmt_family_room_tv_adult_mode"
MODE = "select.tv_mgmt_family_room_tv_mode"


async def test_websocket_access(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, hass_read_only_user, hass_read_only_access_token, calls
) -> None:
    set_tv(hass)
    entry = await setup(hass)
    admin = await hass_ws_client(hass)
    kid = await hass_ws_client(hass, hass_read_only_access_token)

    async def call(client, **msg):
        await client.send_json_auto_id(msg)
        return await client.receive_json()

    me = (await call(kid, type="tv_mgmt/access/me"))["result"]
    assert me == {"allowed": False, "is_admin": False, "user_id": hass_read_only_user.id, "name": hass_read_only_user.name}
    assert (await call(admin, type="tv_mgmt/access/me"))["result"]["allowed"] is True

    for msg in (
        {"type": "tv_mgmt/profiles"},
        {"type": "tv_mgmt/activity", "entry_id": entry.entry_id},
        {"type": "tv_mgmt/action", "entry_id": entry.entry_id, "action": "unblock"},
        {"type": "tv_mgmt/subscribe"},
        {"type": "tv_mgmt/access/get"},
        {"type": "tv_mgmt/access/set", "user_ids": [hass_read_only_user.id]},
    ):
        res = await call(kid, **msg)
        assert not res["success"] and res["error"]["code"] == "unauthorized", msg

    res = await call(admin, type="tv_mgmt/access/get")
    assert res["result"]["user_ids"] == []
    people = {p["id"]: p for p in res["result"]["people"]}
    assert people[hass_admin_user.id]["is_admin"] is True
    assert people[hass_read_only_user.id]["is_admin"] is False

    # Unknown IDs are dropped.
    res = await call(admin, type="tv_mgmt/access/set", user_ids=[hass_read_only_user.id, "nobody"])
    assert res["result"]["user_ids"] == [hass_read_only_user.id]

    assert (await call(kid, type="tv_mgmt/access/me"))["result"]["allowed"] is True
    assert (await call(kid, type="tv_mgmt/profiles"))["success"]
    res = await call(kid, type="tv_mgmt/action", entry_id=entry.entry_id, action="grant_extension", minutes=20)
    assert res["success"] and res["result"]["profile"]["extension_minutes"] == 20
    # Still only admins manage access.
    assert not (await call(kid, type="tv_mgmt/access/get"))["success"]


async def test_entities_and_services(hass: HomeAssistant, hass_admin_user, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    # A regular, non-admin Home Assistant user, who can control entities.
    user = await hass.auth.async_create_user("Kid", group_ids=["system-users"])
    kid = Context(user_id=user.id)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("switch", "turn_on", {"entity_id": ADULT}, blocking=True, context=kid)
    assert err.value.translation_key == "not_allowed"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_off", {"entity_id": LOCK}, blocking=True, context=kid)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": MODE, "option": "paused"}, blocking=True, context=kid
        )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "tv_mgmt", "grant_extension", {"profile_id": entry.entry_id, "minutes": 60}, blocking=True, context=kid
        )
    assert hass.states.get(ADULT).state == "off"
    assert hass.states.get(LOCK).state == "on"
    assert entry.runtime_data.state.extension_minutes == 0

    # Admins and automations (no user) can.
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": LOCK}, blocking=True, context=Context(user_id=hass_admin_user.id)
    )
    await hass.services.async_call("tv_mgmt", "grant_extension", {"profile_id": entry.entry_id, "minutes": 10}, blocking=True)
    assert hass.states.get(LOCK).state == "off"
    assert entry.runtime_data.state.extension_minutes == 10

    # Once allowed, the parent can too.
    from custom_components.tv_mgmt.access import get_access

    await get_access(hass).async_set([user.id])
    await hass.services.async_call("switch", "turn_on", {"entity_id": LOCK}, blocking=True, context=kid)
    assert hass.states.get(LOCK).state == "on"


async def test_sidebar_shows_to_everyone_only_for_non_admin_parents(
    hass: HomeAssistant, hass_admin_user, hass_read_only_user
) -> None:
    from custom_components.tv_mgmt import panel
    from custom_components.tv_mgmt.access import async_get_access

    access = await async_get_access(hass)
    hass.config.components.add("frontend")
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with (
        patch("homeassistant.components.panel_custom.async_register_panel", AsyncMock()) as register,
        patch("homeassistant.components.frontend.async_remove_panel") as remove,
    ):
        await panel.async_register_panel(hass)
        assert register.call_args.kwargs["require_admin"] is True

        await access.async_set([hass_admin_user.id])
        await panel.async_register_panel(hass, update=True)
        assert register.call_count == 1  # Still admin-only: nothing to change.

        await access.async_set([hass_read_only_user.id])
        await panel.async_register_panel(hass, update=True)
        assert remove.call_count == 1
        assert register.call_args.kwargs["require_admin"] is False
        assert hass.http.async_register_static_paths.await_count == 1

        await access.async_set([])
        await panel.async_register_panel(hass, update=True)
        assert register.call_args.kwargs["require_admin"] is True
