"""Remote buttons on the sidebar app's dashboard."""

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.tv_mgmt.remote_keys import available_keys, presses
from tests.ha.test_apple_tv import BOX, REMOTE, set_box, setup_box
from tests.ha.test_integration import TV, set_tv, setup

# TURN_ON | TURN_OFF | VOLUME_STEP | VOLUME_MUTE
TV_FEATURES = 128 | 256 | 1024 | 8


def add_tv(hass, platform, *, remote=True, features=TV_FEATURES, state="on", muted=False):
    """A TV media player from `platform`, with a remote entity on the same device."""
    entry = MockConfigEntry(domain=platform)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(platform, "tv1")}, name="TV"
    )
    registry = er.async_get(hass)
    player = registry.async_get_or_create(
        "media_player", platform, "tv1", config_entry=entry, device_id=device.id, suggested_object_id="tv"
    )
    remote_id = None
    if remote:
        remote_id = registry.async_get_or_create(
            "remote", platform, "tv1", config_entry=entry, device_id=device.id, suggested_object_id="tv"
        ).entity_id
    hass.states.async_set(
        player.entity_id, state, {"supported_features": features, "is_volume_muted": muted}
    )
    return player.entity_id, remote_id


@pytest.mark.parametrize(
    ("platform", "up", "select", "back"),
    [
        ("androidtv_remote", "DPAD_UP", "DPAD_CENTER", "BACK"),
        ("roku", "up", "select", "back"),
        ("samsungtv", "KEY_UP", "KEY_ENTER", "KEY_RETURN"),
        ("braviatv", "Up", "Confirm", "Return"),
    ],
)
async def test_remote_entity_platforms(hass: HomeAssistant, platform, up, select, back) -> None:
    player, remote = add_tv(hass, platform)
    keys = presses(hass, player)
    assert keys["up"].domain == "remote" and keys["up"].service == "send_command"
    assert keys["up"].data == {"entity_id": remote, "command": up}
    assert keys["select"].data["command"] == select
    assert keys["back"].data["command"] == back
    assert keys["power"].service == "turn_off"
    assert keys["volume_up"].service == "volume_up"
    assert keys["mute"].data == {"entity_id": player, "is_volume_muted": True}


async def test_webos_uses_button_service(hass: HomeAssistant) -> None:
    player, _ = add_tv(hass, "webostv", remote=False)
    keys = presses(hass, player)
    assert (keys["select"].domain, keys["select"].service) == ("webostv", "button")
    assert keys["select"].data == {"entity_id": player, "button": "ENTER"}
    assert keys["home"].data["button"] == "HOME"


async def test_adb_uses_adb_command(hass: HomeAssistant) -> None:
    player, _ = add_tv(hass, "androidtv", remote=False)
    keys = presses(hass, player)
    assert (keys["left"].domain, keys["left"].service) == ("androidtv", "adb_command")
    assert keys["left"].data == {"entity_id": player, "command": "LEFT"}


async def test_only_supported_buttons(hass: HomeAssistant) -> None:
    # No remote entity, no volume, only turn off; a generic TV has no d-pad.
    player, _ = add_tv(hass, "vizio", remote=False, features=256)
    assert available_keys(hass, player) == ["power"]
    # Off, and it can't be turned on: power is still offered (it was on).
    hass.states.async_set(player, "off", {"supported_features": 256})
    assert presses(hass, player) == {}
    assert available_keys(hass, player) == ["power"]
    # Off, and it can be turned on.
    hass.states.async_set(player, "off", {"supported_features": 128 | 256})
    assert presses(hass, player)["power"].service == "turn_on"
    # The remote integration without its remote entity has no d-pad.
    player, _ = add_tv(hass, "roku", remote=False, features=0)
    assert available_keys(hass, player) == []
    assert available_keys(hass, None) == []


async def test_apple_tv_keys(hass: HomeAssistant, calls, box_calls) -> None:
    hass.states.async_set(BOX, "playing", {"supported_features": 128 | 256 | 1024})
    keys = presses(hass, BOX)
    # Volume and Menu go through the Apple TV's remote.
    assert keys["volume_up"].data == {"entity_id": REMOTE, "command": "volume_up"}
    assert keys["back"].data == {"entity_id": REMOTE, "command": "menu"}
    assert "mute" not in keys
    assert keys["power"].service == "turn_off"


async def test_ws_remote(hass: HomeAssistant, hass_ws_client, calls, box_calls) -> None:
    set_tv(hass)
    set_box(hass)
    hass.states.async_set(BOX, "playing", {"supported_features": 128 | 256})
    entry = await setup_box(hass)
    volume = async_mock_service(hass, "media_player", "volume_up")
    client = await hass_ws_client(hass)

    async def call(**msg):
        await client.send_json_auto_id({"type": "tv_mgmt/remote", "entry_id": entry.entry_id, **msg})
        return await client.receive_json()

    await client.send_json_auto_id({"type": "tv_mgmt/profiles"})
    profile = (await client.receive_json())["result"]["profiles"][0]
    assert profile["remote_keys"] == []  # the test TV supports nothing
    assert "select" in profile["apple_tv"]["remote_keys"]
    assert "power" in profile["apple_tv"]["remote_keys"]
    keys = {e["key"]: e["entity_id"] for e in profile["entities"]}
    assert keys["mode"] == "select.tv_mgmt_family_room_tv_mode"
    assert keys["input_lock"] == "switch.tv_mgmt_family_room_tv_input_lock"
    assert "current_app" in keys

    res = await call(device="apple_tv", key="select")
    assert res["success"], res
    assert box_calls["remote"][-1].data == {"entity_id": REMOTE, "command": "select"}

    res = await call(device="apple_tv", key="power")
    assert res["success"]
    assert calls["turn_off"][-1].data == {"entity_id": BOX}

    res = await call(device="tv", key="volume_up")
    assert not res["success"] and res["error"]["code"] == "not_supported"

    hass.states.async_set(TV, "on", {"source": "HDMI 2", "supported_features": 1024})
    res = await call(device="tv", key="volume_up")
    assert res["success"]
    assert volume[-1].data == {"entity_id": TV}

    res = await call(device="fridge", key="up")
    assert not res["success"]


async def test_ws_remote_needs_admin(hass: HomeAssistant, hass_ws_client, hass_read_only_access_token, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "tv_mgmt/remote", "entry_id": entry.entry_id, "device": "tv", "key": "power"})
    res = await client.receive_json()
    assert not res["success"] and res["error"]["code"] == "unauthorized"
