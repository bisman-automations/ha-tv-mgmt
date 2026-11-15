"""Parent mode (called adult mode before 1.22.0): for minutes, until a time,
or for the rest of the day, from the dashboard and the set_parent_mode action."""

from __future__ import annotations

from datetime import datetime, time

import pytest
import voluptuous as vol

from custom_components.tv_mgmt.manager import parent_mode_end
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from tests.ha.test_adult_mode import setup_tv, ws
from tests.ha.test_integration import set_tv

DOMAIN = "tv_mgmt"


def _local(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=dt_util.get_default_time_zone())


def test_parent_mode_end() -> None:
    now = _local("2026-11-14 19:30:00")
    assert parent_mode_end(now, rest_of_day=True) == _local("2026-11-15 00:00:00")
    assert parent_mode_end(now, until=time(22, 0)) == _local("2026-11-14 22:00:00")
    # Already past today: that time tomorrow.
    assert parent_mode_end(now, until=time(7, 0)) == _local("2026-11-15 07:00:00")
    assert parent_mode_end(now) is None


async def test_dashboard_rest_of_day_and_until(
    hass: HomeAssistant, hass_ws_client
) -> None:
    # No frozen clock: websocket logins fail under one.
    set_tv(hass)
    entry = await setup_tv(hass)
    manager = entry.runtime_data
    base = {
        "type": "tv_mgmt/action",
        "entry_id": entry.entry_id,
        "action": "set_adult_mode",
    }

    res = await ws(hass_ws_client, hass, **base, enabled=True, rest_of_day=True)
    until = dt_util.parse_datetime(res["profile"]["adult_mode_until"])
    assert until == parent_mode_end(dt_util.utcnow(), rest_of_day=True)
    assert manager.adult_mode_active

    res = await ws(hass_ws_client, hass, **base, enabled=True, until="21:15")
    until = dt_util.parse_datetime(res["profile"]["adult_mode_until"])
    assert until == parent_mode_end(dt_util.utcnow(), until=time(21, 15))

    res = await ws(hass_ws_client, hass, **base, enabled=True, minutes=60)
    until = dt_util.parse_datetime(res["profile"]["adult_mode_until"])
    assert abs((until - dt_util.utcnow()).total_seconds() - 3600) < 60

    await ws(hass_ws_client, hass, **base, enabled=False)
    assert not manager.adult_mode_active

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({**base, "enabled": True, "until": "25:00"})
    assert not (await client.receive_json())["success"]


async def test_set_parent_mode_action(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_local("2026-11-14 19:30:00"))
    set_tv(hass)
    entry = await setup_tv(hass)
    manager = entry.runtime_data

    await hass.services.async_call(
        DOMAIN,
        "set_parent_mode",
        {"profile_id": entry.entry_id, "until": "23:00:00"},
        blocking=True,
    )
    assert manager.state.adult_mode_until_dt == _local("2026-11-14 23:00:00")
    await hass.services.async_call(
        DOMAIN,
        "set_parent_mode",
        {"profile_id": entry.entry_id, "rest_of_day": True},
        blocking=True,
    )
    assert manager.state.adult_mode_until_dt == _local("2026-11-15 00:00:00")
    await hass.services.async_call(
        DOMAIN, "set_parent_mode", {"profile_id": entry.entry_id}, blocking=True
    )
    assert manager.state.adult_mode_until_dt == _local("2026-11-14 21:30:00")
    await hass.services.async_call(
        DOMAIN,
        "set_parent_mode",
        {"profile_id": entry.entry_id, "enabled": False},
        blocking=True,
    )
    assert not manager.adult_mode_active
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_parent_mode",
            {"profile_id": entry.entry_id, "minutes": 30, "rest_of_day": True},
            blocking=True,
        )
