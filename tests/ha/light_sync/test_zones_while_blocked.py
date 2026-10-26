"""Light zones can always be changed, whatever the TV is doing.

1.16.0's Lights tab couldn't change or remove a zone in Home Assistant: it
looked for a zone's id under ``zone_id``, but zones carry it as ``id``. These
tests send what the panel sends (its keys read from the panel's own source)
while the TV is blocked, off, paused or in adult mode. Only the colouring is
held; the zone's settings never are.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tv_mgmt.light_sync.const import CONF_ZONE_ID
from custom_components.tv_mgmt.light_sync.websocket import ZONE_SCHEMA
from homeassistant.core import HomeAssistant

from .conftest import (
    ENTRY_ID,
    LIGHT_COLOR,
    LIGHT_WHITE,
    SYNCING,
    ZONE_ID,
    set_tv,
    settle,
    tv_options_with,
)

PANEL = (
    Path(__file__).parents[3] / "custom_components/tv_mgmt/frontend/tv-mgmt-panel.js"
)
PREVIEW = Path(__file__).parents[3] / "scripts/panel-preview/index.html"


def _panel_keys() -> list[str]:
    """The zone settings the panel's zone form sends (from its source)."""
    source = PANEL.read_text()
    listed = re.search(
        r"for \(const key of \[([^\]]+)\]\) \{\s*if \(c\[key\] !== undefined\) zone\[key\]",
        source,
    )
    assert listed, "the panel's zone form keys moved"
    numbers = re.search(r"const LS_NUMBERS = \[(.*?)\n\];", source, re.S)
    assert numbers
    return re.findall(r'"(\w+)"', listed.group(1)) + re.findall(
        r'\["(\w+)"', numbers.group(1)
    )


def _as_the_panel_saves(config: dict[str, Any], **changes: Any) -> dict[str, Any]:
    zone = {k: config[k] for k in _panel_keys() if k in config} | changes
    zone["id"] = config["id"]
    return zone


def test_the_panel_uses_the_backends_zone_keys() -> None:
    """Same names as the backend: a zone's id is ``id``, every key is allowed."""
    assert CONF_ZONE_ID == "id"
    source = PANEL.read_text()
    assert not re.search(r"(config|cfg|c)\.zone_id\b", source)
    assert "zone.id = c.id" in source and "zone: { id: d.zoneEnable" in source
    schema_keys = {str(k) for k in ZONE_SCHEMA.schema}
    assert set(_panel_keys()) <= schema_keys
    # The preview's sample backend refuses what the real one does.
    defaults = re.search(
        r"const LIGHT_DEFAULTS = \{(.*?)\};", PREVIEW.read_text(), re.S
    )
    assert defaults
    assert set(re.findall(r"(\w+):", defaults.group(1))) <= schema_keys


@pytest.fixture
async def ws(hass: HomeAssistant, hass_ws_client, loaded_entry: MockConfigEntry):
    client = await hass_ws_client(hass)

    async def call(**msg: Any) -> dict[str, Any]:
        await client.send_json_auto_id(msg)
        return await client.receive_json()

    return call


async def _hold(hass: HomeAssistant, entry: MockConfigEntry, how: str) -> None:
    manager = entry.runtime_data
    if how == "blocked":
        manager.force_block()
    elif how == "tv_off":
        set_tv(hass, "off")
    elif how == "paused":
        manager.set_mode("paused")
    elif how == "adult_mode":
        manager.set_adult_mode(True)
    elif how == "bedtime_and_off":
        manager.force_block()
        set_tv(hass, "off")
    await settle(hass)


@pytest.mark.parametrize(
    "how", ["blocked", "bedtime_and_off", "tv_off", "paused", "adult_mode"]
)
async def test_change_and_remove_zones_whatever_the_tv_is_doing(
    hass: HomeAssistant,
    ws,
    loaded_entry: MockConfigEntry,
    mocked_lights: dict[str, list],
    how: str,
) -> None:
    await _hold(hass, loaded_entry, how)
    light_sync = loaded_entry.runtime_data.light_sync
    if how in ("blocked", "bedtime_and_off"):
        assert hass.states.get(SYNCING).attributes["held_reason"] == "blocked"

    res = await ws(type="tv_mgmt/light_sync/get")
    (zone,) = res["result"]["tvs"][0]["zones"]
    config = zone["config"]

    # Change (the zone form's Save), as the panel sends it.
    res = await ws(
        type="tv_mgmt/light_sync/zone/save",
        entry_id=ENTRY_ID,
        zone=_as_the_panel_saves(config, name="Behind the set", brightness_pct=45),
    )
    assert res["success"], res
    assert res["result"]["zone"]["name"] == "Behind the set"
    assert res["result"]["zone"]["brightness_pct"] == 45
    await settle(hass)
    assert light_sync.data.config.zone(ENTRY_ID, ZONE_ID)["name"] == "Behind the set"

    # The zone's switch, as the panel sends it.
    res = await ws(
        type="tv_mgmt/light_sync/zone/save",
        entry_id=ENTRY_ID,
        zone={"id": ZONE_ID, "lights": config["lights"], "enabled": False},
    )
    assert res["success"], res
    assert res["result"]["zone"]["enabled"] is False

    # Look again: allowed, even if there's nothing to colour.
    res = await ws(
        type="tv_mgmt/light_sync/zone/action",
        entry_id=ENTRY_ID,
        zone_id=ZONE_ID,
        action="resync",
    )
    assert res["success"], res

    # Add one and remove both.
    res = await ws(
        type="tv_mgmt/light_sync/zone/save",
        entry_id=ENTRY_ID,
        zone={"name": "Couch", "lights": [LIGHT_WHITE]},
    )
    assert res["success"], res
    new = res["result"]["zone"]["id"]
    for zone_id in (new, ZONE_ID):
        res = await ws(
            type="tv_mgmt/light_sync/zone/remove", entry_id=ENTRY_ID, zone_id=zone_id
        )
        assert res["success"], res
    await settle(hass)
    assert light_sync.zones == {}
    res = await ws(type="tv_mgmt/light_sync/get")
    assert res["result"]["tvs"][0]["zones"] == []


async def test_the_old_panel_payload_is_refused(
    hass: HomeAssistant, ws, loaded_entry: MockConfigEntry
) -> None:
    """What 1.16.0's panel sent for a zone's switch: refused, so it must show an error."""
    res = await ws(
        type="tv_mgmt/light_sync/zone/save",
        entry_id=ENTRY_ID,
        zone={"zone_id": ZONE_ID, "lights": ["light.tv_backlight"], "enabled": False},
    )
    assert not res["success"]
    assert res["error"]["code"] == "invalid_format"
    res = await ws(type="tv_mgmt/light_sync/zone/remove", entry_id=ENTRY_ID, zone_id="")
    assert not res["success"]


@pytest.mark.parametrize(
    "tv_options", [tv_options_with(lights={"tv_lights": [LIGHT_COLOR]})]
)
async def test_tv_lights_turn_off_at_bedtime(
    hass: HomeAssistant,
    ws,
    loaded_entry: MockConfigEntry,
    mocked_lights: dict[str, list],
) -> None:
    """The dashboard's Turn TV lights off works with the TV blocked and off."""
    await _hold(hass, loaded_entry, "bedtime_and_off")
    mocked_lights["turn_off"].clear()
    res = await ws(type="tv_mgmt/lights/off", entry_id=ENTRY_ID)
    assert res["success"], res
    assert res["result"]["lights"] == [LIGHT_COLOR] and res["result"]["failed"] == {}
    assert [
        c
        for c in mocked_lights["turn_off"]
        if c.data.get("entity_id") in (LIGHT_COLOR, [LIGHT_COLOR])
    ]
