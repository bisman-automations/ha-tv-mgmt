"""The Lights tab: websocket commands, access, devices and the home-wide settings."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tv_mgmt.light_sync.metadata import MetadataAuthError
from custom_components.tv_mgmt.light_sync.storage import DATA_LIGHT_SYNC
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    DOMAIN,
    ENTRY_ID,
    LIGHT_COLOR,
    LIGHT_WHITE,
    MEDIA_PLAYER,
    SWITCH,
    TV,
    ZONE_ID,
    light_zone,
    settle,
)

VALIDATE = "custom_components.tv_mgmt.light_sync.websocket.async_validate_credentials"
V3_KEY = "0123456789abcdef0123456789abcdef"


def _zone_device(hass: HomeAssistant, zone_id: str) -> dr.DeviceEntry | None:
    devices = dr.async_get(hass)
    identifier = (DOMAIN, f"{ENTRY_ID}_zone_{zone_id}")
    if hasattr(devices, "async_get_device_by_identifier"):
        return devices.async_get_device_by_identifier(identifier, ENTRY_ID)
    return devices.async_get_device(identifiers={identifier})


@pytest.fixture
async def ws(hass: HomeAssistant, hass_ws_client, loaded_entry: MockConfigEntry):
    client = await hass_ws_client(hass)

    async def call(**msg: Any) -> dict[str, Any]:
        await client.send_json_auto_id(msg)
        return await client.receive_json()

    return call


async def test_get(hass: HomeAssistant, ws) -> None:
    res = await ws(type="tv_mgmt/light_sync/get")
    assert res["success"]
    result = res["result"]
    (tv,) = result["tvs"]
    assert tv["entry_id"] == ENTRY_ID
    assert tv["tv_lights"] == []
    (zone,) = tv["zones"]
    assert zone["config"]["id"] == ZONE_ID
    assert zone["config"]["lights"] == [LIGHT_COLOR, LIGHT_WHITE]
    assert zone["entities"]["sync_enabled"] == SWITCH
    assert result["settings"] == {
        "metadata_providers": ["tmdb", "trakt"],
        "tmdb_key_set": False,
        "trakt_key_set": False,
    }
    assert result["max_zones"] == 8
    assert "balanced" in result["choices"]["presets"]


async def test_add_change_and_remove_a_zone(
    hass: HomeAssistant, ws, loaded_entry
) -> None:
    manager = loaded_entry.runtime_data
    with patch.object(hass.config_entries, "async_reload") as reload:
        res = await ws(
            type="tv_mgmt/light_sync/zone/save",
            entry_id=ENTRY_ID,
            zone={"name": "Couch", "lights": [LIGHT_WHITE], "brightness_pct": 40},
        )
        assert res["success"], res
        new = res["result"]["zone"]
        assert new["id"] != ZONE_ID and new["name"] == "Couch"
        assert new["brightness_pct"] == 40
        await hass.async_block_till_done()
        assert set(manager.light_sync.zones) == {ZONE_ID, new["id"]}
        switch = "switch.tv_mgmt_living_room_tv_couch_light_sync"
        assert hass.states.get(switch).state == "on"

        # A zone device per zone, linked to the TV's device.
        devices = dr.async_get(hass)
        device = _zone_device(hass, new["id"])
        assert device is not None
        assert device.name == "TV Mgmt — Living Room TV · Couch"
        assert device.via_device_id is not None

        # Change it: applied live.
        res = await ws(
            type="tv_mgmt/light_sync/zone/save",
            entry_id=ENTRY_ID,
            zone={
                "id": new["id"],
                "name": "Sofa",
                "lights": [LIGHT_WHITE],
                "enabled": False,
            },
        )
        assert res["success"], res
        await hass.async_block_till_done()
        assert res["result"]["zone"]["brightness_pct"] == 40  # kept
        assert hass.states.get(switch).state == "off"
        assert devices.async_get(device.id).name == "TV Mgmt — Living Room TV · Sofa"

        res = await ws(
            type="tv_mgmt/light_sync/zone/remove", entry_id=ENTRY_ID, zone_id=new["id"]
        )
        assert res["success"]
        await hass.async_block_till_done()
    reload.assert_not_called()
    assert set(manager.light_sync.zones) == {ZONE_ID}
    assert hass.states.get(switch) is None
    assert er.async_get(hass).async_get(switch) is None
    assert dr.async_get(hass).async_get(device.id) is None
    assert hass.data[DATA_LIGHT_SYNC].config.zone(ENTRY_ID, new["id"]) is None


async def test_zone_errors(hass: HomeAssistant, ws) -> None:
    for zone in (
        {"lights": []},  # needs a light
        {"lights": ["switch.heater"]},  # only lights
        {"lights": [LIGHT_COLOR], "brightness_pct": 0},
        {"lights": [LIGHT_COLOR], "extra_players": ["light.lamp"]},
    ):
        res = await ws(
            type="tv_mgmt/light_sync/zone/save", entry_id=ENTRY_ID, zone=zone
        )
        assert not res["success"], zone
    res = await ws(
        type="tv_mgmt/light_sync/zone/save",
        entry_id=ENTRY_ID,
        zone={"id": "nope", "lights": [LIGHT_COLOR]},
    )
    assert res["error"]["code"] == "not_found"
    res = await ws(
        type="tv_mgmt/light_sync/zone/save",
        entry_id="nope",
        zone={"lights": [LIGHT_COLOR]},
    )
    assert res["error"]["code"] == "not_found"
    res = await ws(
        type="tv_mgmt/light_sync/zone/remove", entry_id=ENTRY_ID, zone_id="nope"
    )
    assert res["error"]["code"] == "not_found"

    for _ in range(7):
        res = await ws(
            type="tv_mgmt/light_sync/zone/save",
            entry_id=ENTRY_ID,
            zone={"lights": [LIGHT_COLOR]},
        )
        assert res["success"]
    res = await ws(
        type="tv_mgmt/light_sync/zone/save",
        entry_id=ENTRY_ID,
        zone={"lights": [LIGHT_COLOR]},
    )
    assert res["error"]["code"] == "too_many"


async def test_zone_actions(hass: HomeAssistant, ws, mocked_lights) -> None:
    res = await ws(
        type="tv_mgmt/light_sync/zone/action",
        entry_id=ENTRY_ID,
        zone_id=ZONE_ID,
        action="save_title_color",
    )
    assert res["error"]["code"] == "failed"  # nothing playing

    res = await ws(
        type="tv_mgmt/light_sync/titles/set", title="The Matrix", rgb_color=[0, 255, 0]
    )
    assert res["result"]["key"] == "matrix"
    hass.states.async_set(MEDIA_PLAYER, "playing", {"media_title": "The Matrix"})
    await settle(hass)
    res = await ws(
        type="tv_mgmt/light_sync/zone/action",
        entry_id=ENTRY_ID,
        zone_id=ZONE_ID,
        action="resync",
    )
    assert res["success"] and res["result"]["outcome"] == "ok"
    res = await ws(
        type="tv_mgmt/light_sync/zone/action",
        entry_id=ENTRY_ID,
        zone_id=ZONE_ID,
        action="save_title_color",
    )
    assert res["result"]["title"] == "matrix"

    titles = (await ws(type="tv_mgmt/light_sync/get"))["result"]["titles"]
    assert [t["key"] for t in titles] == ["matrix"]
    assert (await ws(type="tv_mgmt/light_sync/titles/remove", title="matrix"))[
        "success"
    ]
    assert (await ws(type="tv_mgmt/light_sync/titles/remove", title="matrix"))["error"][
        "code"
    ] == "not_found"
    assert (
        await ws(type="tv_mgmt/light_sync/titles/set", title="!!!", rgb_color=[1, 2, 3])
    )["error"]["code"] == "invalid_title"


async def test_settings(hass: HomeAssistant, ws, loaded_entry) -> None:
    data = hass.data[DATA_LIGHT_SYNC]
    with patch(VALIDATE, side_effect=MetadataAuthError("no")):
        res = await ws(type="tv_mgmt/light_sync/settings/set", tmdb_api_key=V3_KEY)
    assert res["error"]["code"] == "invalid_key"
    assert data.metadata_service is None

    with patch(VALIDATE) as validate:
        res = await ws(
            type="tv_mgmt/light_sync/settings/set",
            tmdb_api_key=V3_KEY,
            metadata_providers=["tmdb"],
        )
    assert res["success"] and validate.call_count == 1
    assert res["result"]["settings"]["tmdb_key_set"] is True
    assert V3_KEY not in repr(res)
    assert [p.name for p in data.metadata_service.providers] == ["tmdb"]
    # Every zone uses the shared lookups, without a reload.
    assert light_zone(loaded_entry).metadata is data.metadata_service

    # Left out: kept (and not checked again). Empty: forgotten.
    with patch(VALIDATE) as validate:
        res = await ws(
            type="tv_mgmt/light_sync/settings/set", metadata_providers=["tmdb", "trakt"]
        )
        assert validate.call_count == 0
    assert res["result"]["settings"]["tmdb_key_set"] is True
    res = await ws(type="tv_mgmt/light_sync/settings/set", tmdb_api_key="")
    assert res["result"]["settings"]["tmdb_key_set"] is False
    assert data.metadata_service is None


async def test_only_allowed_people(
    hass: HomeAssistant, hass_ws_client, hass_read_only_access_token, loaded_entry
) -> None:
    kid = await hass_ws_client(hass, hass_read_only_access_token)
    for msg in (
        {"type": "tv_mgmt/light_sync/get"},
        {
            "type": "tv_mgmt/light_sync/zone/save",
            "entry_id": ENTRY_ID,
            "zone": {"lights": [LIGHT_COLOR]},
        },
        {
            "type": "tv_mgmt/light_sync/zone/remove",
            "entry_id": ENTRY_ID,
            "zone_id": ZONE_ID,
        },
        {
            "type": "tv_mgmt/light_sync/zone/action",
            "entry_id": ENTRY_ID,
            "zone_id": ZONE_ID,
            "action": "resync",
        },
        {"type": "tv_mgmt/light_sync/settings/set", "metadata_providers": []},
        {
            "type": "tv_mgmt/light_sync/titles/set",
            "title": "Up",
            "rgb_color": [1, 2, 3],
        },
        {"type": "tv_mgmt/light_sync/titles/remove", "title": "Up"},
    ):
        await kid.send_json_auto_id(msg)
        res = await kid.receive_json()
        assert not res["success"] and res["error"]["code"] == "unauthorized", msg

    # A regular, non-admin Home Assistant user, who can control entities.
    user = await hass.auth.async_create_user("Kid", group_ids=["system-users"])
    context = Context(user_id=user.id)
    for domain, service, data in (
        (DOMAIN, "set_title_color", {"title": "Up", "rgb_color": [1, 2, 3]}),
        (DOMAIN, "remove_title_color", {"title": "Up"}),
        (DOMAIN, "resync_lights", {}),
        (DOMAIN, "clear_artwork_cache", {}),
        ("switch", "turn_off", {"entity_id": SWITCH}),
        (
            "button",
            "press",
            {"entity_id": "button.tv_mgmt_living_room_tv_lights_look_again"},
        ),
        (
            "number",
            "set_value",
            {
                "entity_id": "number.tv_mgmt_living_room_tv_lights_brightness",
                "value": 10,
            },
        ),
        (
            "select",
            "select_option",
            {
                "entity_id": "select.tv_mgmt_living_room_tv_lights_color_mode",
                "option": "palette",
            },
        ),
    ):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                domain, service, data, blocking=True, context=context
            )
        assert err.value.translation_key == "not_allowed", service
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "list_title_colors",
            {},
            blocking=True,
            context=context,
            return_response=True,
        )
    assert hass.states.get(SWITCH).state == "on"


async def test_zones_survive_a_reload(hass: HomeAssistant, loaded_entry) -> None:
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "on"
    device = _zone_device(hass, ZONE_ID)
    assert device is not None


async def test_removing_the_tv_forgets_its_zones(
    hass: HomeAssistant, loaded_entry, mocked_lights
) -> None:
    hass.states.async_set(
        MEDIA_PLAYER, "playing", {"media_title": "Night", "media_genre": "Horror"}
    )
    await settle(hass)
    assert light_zone(loaded_entry).snapshot
    mocked_lights["turn_on"].clear()
    assert await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DATA_LIGHT_SYNC].config.zones_for(ENTRY_ID) == []
    # The lights went back to how they were.
    assert any(c.data.get("entity_id") == LIGHT_COLOR for c in mocked_lights["turn_on"])
    assert hass.states.get(TV) is not None
