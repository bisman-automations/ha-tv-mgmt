"""End-to-end light sync: setup, syncing, restore, entities and services.

Ported from Movie Light Sync; the zone now lives on a TV Mgmt TV.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tv_mgmt.light_sync.color import ImageAnalysis
from custom_components.tv_mgmt.light_sync.const import GENRE_COLORS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component

from .conftest import (
    COLOR_SENSOR,
    DOMAIN,
    LIGHT_COLOR,
    LIGHT_WHITE,
    MEDIA_PLAYER,
    SOURCE_SENSOR,
    SWITCH,
    SYNCING,
    settle,
)


async def test_setup_and_unload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Entities are created and the entry unloads cleanly."""
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(SWITCH).state == "on"
    assert hass.states.get(SWITCH).name == "TV Mgmt — Living Room TV · Lights Light sync"
    assert hass.states.get(SYNCING).state == "off"
    assert hass.states.get(SOURCE_SENSOR).state == "none"
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED


async def test_title_mapping_sync_and_restore(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Playing a mapped title colours the lights; stopping restores them."""
    turn_on: list[ServiceCall] = hass.data["turn_on_calls"]
    await hass.services.async_call(
        DOMAIN,
        "set_title_color",
        {"title": "Blade Runner 2049", "rgb_color": [255, 100, 0]},
        blocking=True,
    )
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {"media_title": "blade runner 2049", "media_content_type": "movie"},
    )
    await settle(hass)

    color_calls = [c for c in turn_on if "rgb_color" in c.data]
    assert color_calls
    assert color_calls[-1].data["rgb_color"] == [255, 100, 0]
    assert color_calls[-1].data["entity_id"] == LIGHT_COLOR
    # White-only light gets brightness only.
    bright = [c for c in turn_on if c.data.get("entity_id") == LIGHT_WHITE]
    assert bright
    assert "rgb_color" not in bright[-1].data

    assert hass.states.get(COLOR_SENSOR).state == "#ff6400"
    assert hass.states.get(SOURCE_SENSOR).state == "title_mapping"
    assert hass.states.get(SYNCING).state == "on"

    # Stop -> restore (delay 0).
    turn_on.clear()
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await settle(hass)  # debouncer -> schedules idle action
    await settle(hass)  # idle action timer fires
    restore = [c for c in turn_on if c.data["entity_id"] == LIGHT_COLOR]
    assert restore
    assert restore[-1].data["color_temp_kelvin"] == 2700
    assert restore[-1].data["brightness"] == 120
    assert any(c.data["entity_id"] == LIGHT_WHITE for c in hass.data["turn_off_calls"])
    assert hass.states.get(SYNCING).state == "off"


async def test_genre_fallback_and_switch(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Genre table is used without artwork; disabling stops syncing."""
    hass.states.async_set(
        MEDIA_PLAYER, "playing", {"media_title": "Night Movie", "media_genre": "Horror"}
    )
    await settle(hass)
    assert hass.states.get(SOURCE_SENSOR).state == "genre"
    turn_on = hass.data["turn_on_calls"]
    assert [c for c in turn_on if "rgb_color" in c.data][-1].data["rgb_color"] == list(
        GENRE_COLORS["horror"]
    )

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(SWITCH).state == "off"
    assert hass.states.get(SYNCING).state == "off"

    turn_on.clear()
    hass.states.async_set(
        MEDIA_PLAYER, "playing", {"media_title": "Another", "media_genre": "Comedy"}
    )
    await settle(hass)
    assert not [c for c in turn_on if "rgb_color" in c.data]


async def test_poster_extraction(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Artwork colour is used (and cached) when no title mapping exists."""
    with (
        patch(
            "custom_components.tv_mgmt.light_sync.controller."
            "LightZone._async_fetch_image",
            return_value=b"img",
        ) as fetch,
        patch(
            "custom_components.tv_mgmt.light_sync.controller.analyze_image",
            return_value=ImageAnalysis((0, 80, 200)),
        ),
    ):
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {
                "media_title": "Ocean Doc",
                "entity_picture": "/api/media_player_proxy/x?token=a&cache=1",
            },
        )
        await settle(hass)
        assert hass.states.get(SOURCE_SENSOR).state == "poster"
        # Pause/resume with same picture but rotated token hits the cache.
        hass.states.async_set(MEDIA_PLAYER, "paused", {"media_title": "Ocean Doc"})
        await settle(hass)
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {
                "media_title": "Ocean Doc",
                "entity_picture": "/api/media_player_proxy/x?token=b&cache=1",
            },
        )
        await settle(hass)
        assert fetch.call_count == 1


async def test_services(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Title colour services and resync / clear_cache."""
    await hass.services.async_call(
        DOMAIN,
        "set_title_color",
        {"title": "The Matrix (1999)", "rgb_color": [0, 255, 0]},
        blocking=True,
    )
    resp = await hass.services.async_call(
        DOMAIN, "list_title_colors", {}, blocking=True, return_response=True
    )
    assert resp["count"] == 1
    assert resp["titles"][0]["key"] == "matrix"
    assert resp["titles"][0]["hex"] == "#00ff00"

    await hass.services.async_call(
        DOMAIN, "remove_title_color", {"title": "matrix"}, blocking=True
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "remove_title_color", {"title": "matrix"}, blocking=True
        )

    await hass.services.async_call(
        DOMAIN, "resync_lights", {"profile_id": loaded_entry.entry_id}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN,
        "resync_lights",
        {"profile_id": loaded_entry.entry_id, "zone": "lights"},
        blocking=True,
    )
    await hass.services.async_call(DOMAIN, "resync_lights", {}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "resync_lights", {"profile_id": "nope"}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "resync_lights",
            {"profile_id": loaded_entry.entry_id, "zone": "Kitchen"},
            blocking=True,
        )
    resp = await hass.services.async_call(
        DOMAIN, "clear_artwork_cache", {}, blocking=True, return_response=True
    )
    assert resp == {"cleared": 0}
    resp = await hass.services.async_call(
        DOMAIN,
        "clear_artwork_cache",
        {"include_metadata": True},
        blocking=True,
        return_response=True,
    )
    assert resp == {"cleared": 0, "metadata_cleared": 0}


async def test_diagnostics_redacts(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Diagnostics redact artwork URLs."""
    from custom_components.tv_mgmt.diagnostics import (  # noqa: PLC0415
        async_get_config_entry_diagnostics,
    )

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {"media_title": "X", "entity_picture": "/secret?token=1"},
    )
    diag = await async_get_config_entry_diagnostics(hass, loaded_entry)
    zone = diag["light_sync"]["zones"][0]
    assert zone["player_states"][MEDIA_PLAYER]["attributes"][
        "entity_picture"
    ] == ("**REDACTED**")
    assert zone["name"] == "Lights"


async def test_setup_component_without_entries(hass: HomeAssistant) -> None:
    """Services exist even with no entries."""
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "set_title_color")
