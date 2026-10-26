"""Light sync zone entities: selects, numbers, buttons, palette mode, TMDB.

Ported from Movie Light Sync; zone settings now live in TV Mgmt's store.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from PIL import Image
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tv_mgmt.light_sync.const import (
    CONF_COLOR_SOURCES,
    CONF_METADATA_PROVIDERS,
    CONF_PERSIST_SNAPSHOT,
    CONF_TMDB_API_KEY,
    GENRE_COLORS,
    TMDB_API_URL,
)
from custom_components.tv_mgmt.light_sync.storage import DATA_LIGHT_SYNC
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import (
    BRIGHTNESS,
    COLOR_MODE,
    COLOR_SENSOR,
    DOMAIN,
    ENTRY_ID,
    LIGHT_COLOR,
    LIGHT_WHITE,
    MEDIA_PLAYER,
    METHOD,
    PRESET,
    RESYNC_BUTTON,
    SAVE_BUTTON,
    SOURCE_SENSOR,
    STRATEGY,
    TRANSITION,
    ZONE_ID,
    light_zone,
    settle,
    zone_config,
)

STRIP = ["light.strip_left", "light.strip_center", "light.strip_right"]
FETCH = "custom_components.tv_mgmt.light_sync.controller.LightZone._async_fetch_image"
V3_KEY = "0123456789abcdef0123456789abcdef"


def _poster() -> bytes:
    img = Image.new("RGB", (200, 300), (8, 8, 8))
    img.paste((220, 30, 30), (0, 0, 100, 140))
    img.paste((30, 60, 220), (100, 0, 200, 140))
    img.paste((250, 200, 20), (0, 140, 100, 300))
    img.paste((20, 200, 80), (100, 140, 200, 210))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": option},
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def _set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)


def _rgb_calls(hass: HomeAssistant) -> list[ServiceCall]:
    return [c for c in hass.data["turn_on_calls"] if "rgb_color" in c.data]


async def test_entities_created_with_categories(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """All controls exist, reflect the options and use translation keys."""
    assert hass.states.get(PRESET).state == "balanced"
    assert hass.states.get(METHOD).state == "vibrant"
    assert hass.states.get(COLOR_MODE).state == "single"
    assert hass.states.get(STRATEGY).state == "spread"
    assert float(hass.states.get(BRIGHTNESS).state) == 60
    assert float(hass.states.get(TRANSITION).state) == 0
    assert hass.states.get(SAVE_BUTTON) is not None
    assert hass.states.get(RESYNC_BUTTON) is not None

    for entity_id, category in (
        (PRESET, "config"),
        (METHOD, "config"),
        (COLOR_MODE, "config"),
        (STRATEGY, "config"),
        (TRANSITION, "config"),
        (BRIGHTNESS, None),
        (SAVE_BUTTON, None),
        (RESYNC_BUTTON, None),
    ):
        entry = entity_registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert (entry.entity_category.value if entry.entity_category else None) == (
            category
        )
        assert entry.translation_key
        assert entry.unique_id.startswith(f"{loaded_entry.entry_id}_zone_{ZONE_ID}_")


async def test_controls_write_through_without_reload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Selects/numbers save the zone live, without a reload."""
    manager = loaded_entry.runtime_data
    coordinator = light_zone(loaded_entry)
    hass.states.async_set(
        MEDIA_PLAYER, "playing", {"media_title": "Night", "media_genre": "Horror"}
    )
    await settle(hass)
    assert _rgb_calls(hass)[-1].data["brightness_pct"] == 60

    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        await _set_number(hass, BRIGHTNESS, 35)
        await settle(hass)
        await _set_number(hass, TRANSITION, 1.5)
        await _select(hass, METHOD, "dominant")
        await _select(hass, PRESET, "genre_first")
        await _select(hass, COLOR_MODE, "palette")
        await _select(hass, STRATEGY, "mirrored")
        await settle(hass)
    reload.assert_not_called()
    assert loaded_entry.runtime_data is manager
    assert light_zone(loaded_entry) is coordinator
    assert loaded_entry.state is ConfigEntryState.LOADED

    opts = hass.data[DATA_LIGHT_SYNC].config.zone(ENTRY_ID, ZONE_ID)
    assert opts["brightness_pct"] == 35
    assert isinstance(opts["brightness_pct"], int)
    assert opts["transition"] == 1.5
    assert opts["extraction_method"] == "dominant"
    assert opts[CONF_COLOR_SOURCES] == ["title_mapping", "genre", "poster", "default"]
    assert opts["color_mode"] == "palette"
    assert opts["palette_strategy"] == "mirrored"
    assert coordinator.options["brightness_pct"] == 35
    assert float(hass.states.get(BRIGHTNESS).state) == 35
    assert hass.states.get(PRESET).state == "genre_first"
    # The lights were re-applied with the new brightness while syncing.
    assert _rgb_calls(hass)[-1].data["brightness_pct"] == 35


async def test_custom_source_list_shows_unknown_preset(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A source order that matches no preset is shown as unknown."""
    await light_zone(loaded_entry).async_update_options(
        {CONF_COLOR_SOURCES: ["genre", "title_mapping"]}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(PRESET).state == "unknown"


async def test_option_changes_never_reload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone changes (lights, players, persistence) apply live, with no reload."""
    manager = loaded_entry.runtime_data
    coordinator = light_zone(loaded_entry)
    hass.states.async_set("light.desk", "on", {"supported_color_modes": ["rgb"]})
    with patch.object(hass.config_entries, "async_reload") as reload:
        await coordinator.async_update_options(
            {
                CONF_PERSIST_SNAPSHOT: True,
                "lights": [LIGHT_COLOR, "light.desk"],
                "extra_players": [MEDIA_PLAYER, "media_player.kitchen"],
            }
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert coordinator.configured_lights == [LIGHT_COLOR, "light.desk"]
        assert "media_player.kitchen" in coordinator.media_players

        # Dashboard entities write through to the stored zone.
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": COLOR_MODE, "option": "palette"},
            blocking=True,
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    reload.assert_not_called()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert loaded_entry.runtime_data is manager
    assert light_zone(loaded_entry) is coordinator
    stored = hass.data[DATA_LIGHT_SYNC].config.zone(ENTRY_ID, ZONE_ID)
    assert stored["color_mode"] == "palette"
    assert stored[CONF_PERSIST_SNAPSHOT] is True


async def test_save_title_color_button(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The button stores the shown colour under the (series) title."""
    with pytest.raises(ServiceValidationError):
        await _press(hass, SAVE_BUTTON)

    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            "media_title": "Pilot",
            "media_series_title": "Stranger Things",
            "media_content_type": "episode",
            "media_genre": "Horror",
        },
    )
    await settle(hass)
    assert hass.states.get(SOURCE_SENSOR).state == "genre"
    await _press(hass, SAVE_BUTTON)
    await settle(hass)

    resp = await hass.services.async_call(
        DOMAIN, "list_title_colors", {}, blocking=True, return_response=True
    )
    assert resp["titles"][0]["title"] == "Stranger Things"
    assert resp["titles"][0]["rgb_color"] == list(GENRE_COLORS["horror"])
    # The saved mapping now wins.
    assert hass.states.get(SOURCE_SENSOR).state == "title_mapping"


async def test_resync_button(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Resync re-applies the current colour."""
    hass.states.async_set(
        MEDIA_PLAYER, "playing", {"media_title": "Night", "media_genre": "Horror"}
    )
    await settle(hass)
    before = len(_rgb_calls(hass))
    await _press(hass, RESYNC_BUTTON)
    assert len(_rgb_calls(hass)) == before + 1


async def test_palette_mode_distributes_over_group_members(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Palette mode: group members (in order) get mirrored palette colours."""
    for member in STRIP:
        hass.states.async_set(
            member, "on", {"supported_color_modes": ["rgb"], "color_mode": "rgb"}
        )
    hass.states.async_set(
        LIGHT_COLOR,
        "on",
        {"entity_id": STRIP, "supported_color_modes": ["rgb"], "color_mode": "rgb"},
    )
    await _select(hass, COLOR_MODE, "palette")
    await _select(hass, STRATEGY, "mirrored")

    with patch(FETCH, return_value=_poster()):
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {"media_title": "Colorful", "entity_picture": "/api/x?cache=1"},
        )
        await settle(hass)

    calls = _rgb_calls(hass)
    per_light: dict[str, list[int]] = {}
    for call in calls:
        # One call per light (v0.2.1): entity_id is a single entity.
        per_light[call.data["entity_id"]] = call.data["rgb_color"]
    assert set(per_light) == set(STRIP)
    left, center, right = (per_light[e] for e in STRIP)
    assert left == right  # mirrored ends
    assert left != center
    # White lamp got brightness only and took no palette slot.
    white = [
        c for c in hass.data["turn_on_calls"] if c.data["entity_id"] == LIGHT_WHITE
    ]
    assert white
    assert "rgb_color" not in white[-1].data

    state = hass.states.get(COLOR_SENSOR)
    attrs = state.attributes
    assert attrs["color_mode"] == "palette"
    assert 3 <= len(attrs["palette"]) <= 4
    assert attrs["palette_hex"][0] == state.state
    assert set(attrs["light_colors"]) == set(STRIP)
    assert attrs["light_colors"][STRIP[0]] == attrs["palette_hex"][0]

    # Restore semantics unchanged: members are restored individually.
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await settle(hass)
    await settle(hass)
    restored = {
        c.data["entity_id"]
        for c in hass.data["turn_on_calls"]
        if isinstance(c.data["entity_id"], str)
    }
    assert set(STRIP) <= restored
    assert hass.states.get(COLOR_SENSOR).attributes["palette"] == []


async def test_single_mode_ignores_palette(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """In single mode one colour goes to all colour lights."""
    with patch(FETCH, return_value=_poster()):
        hass.states.async_set(
            MEDIA_PLAYER,
            "playing",
            {"media_title": "Colorful", "entity_picture": "/api/x?cache=2"},
        )
        await settle(hass)
    attrs = hass.states.get(COLOR_SENSOR).attributes
    assert attrs["color_mode"] == "single"
    assert attrs["palette"] == []
    assert list(attrs["light_colors"]) == [LIGHT_COLOR]


TMDB_SETTINGS = {CONF_TMDB_API_KEY: V3_KEY, CONF_METADATA_PROVIDERS: ["tmdb"]}


@pytest.fixture
async def tmdb_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    loaded_entry: MockConfigEntry,
) -> MockConfigEntry:
    """A TV whose zone uses TMDB (key set home-wide) with artwork fallback."""
    for provider in hass.data[DATA_LIGHT_SYNC].metadata_service.providers:
        provider.limiter._min_interval = 0
    return loaded_entry


@pytest.mark.parametrize("light_sync_settings", [TMDB_SETTINGS])
@pytest.mark.parametrize("zone", [zone_config(metadata_artwork=True)])
async def test_genre_from_tmdb_lookup(
    hass: HomeAssistant,
    tmdb_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Without player genres the TMDB genre feeds the genre table."""
    aioclient_mock.get(
        f"{TMDB_API_URL}/search/movie",
        json={"results": [{"id": 1, "title": "Hereditary", "genre_ids": [27]}]},
    )
    # Artwork-only providers are asked first (poster source) - no poster here.
    hass.states.async_set(MEDIA_PLAYER, "playing", {"media_title": "Hereditary (2018)"})
    await settle(hass)
    assert hass.states.get(SOURCE_SENSOR).state == "genre"
    attrs = hass.states.get(COLOR_SENSOR).attributes
    assert attrs["genre"] == "horror"
    assert attrs["metadata_provider"] == "tmdb"
    assert _rgb_calls(hass)[-1].data["rgb_color"] == list(GENRE_COLORS["horror"])
    # Same title again: answered from cache.
    calls = aioclient_mock.call_count
    await _press(hass, RESYNC_BUTTON)
    assert aioclient_mock.call_count == calls


@pytest.mark.parametrize("light_sync_settings", [TMDB_SETTINGS])
@pytest.mark.parametrize("zone", [zone_config(metadata_artwork=True)])
async def test_tmdb_artwork_fallback(
    hass: HomeAssistant,
    tmdb_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """With no player artwork the TMDB poster is analysed."""
    aioclient_mock.get(
        f"{TMDB_API_URL}/search/movie",
        json={
            "results": [
                {"id": 2, "title": "Amelie", "genre_ids": [35], "poster_path": "/a.jpg"}
            ]
        },
    )
    with patch(FETCH, return_value=_poster()) as fetch:
        hass.states.async_set(MEDIA_PLAYER, "playing", {"media_title": "Amelie"})
        await settle(hass)
    assert hass.states.get(SOURCE_SENSOR).state == "poster"
    assert fetch.call_args.args[0] is None  # plain URL, not the player entity
    assert fetch.call_args.args[1] == "https://image.tmdb.org/t/p/w500/a.jpg"
    assert hass.states.get(COLOR_SENSOR).attributes["metadata_provider"] == "tmdb"


@pytest.mark.parametrize("light_sync_settings", [TMDB_SETTINGS])
@pytest.mark.parametrize("zone", [zone_config(metadata_artwork=True)])
async def test_diagnostics_redact_keys(
    hass: HomeAssistant, tmdb_entry: MockConfigEntry
) -> None:
    """API keys never appear in diagnostics."""
    from custom_components.tv_mgmt.diagnostics import (  # noqa: PLC0415
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, tmdb_entry)
    assert diag["light_sync"]["settings"][CONF_TMDB_API_KEY] == "**REDACTED**"
    # Empty (unset) credentials are left as-is by async_redact_data.
    assert diag["light_sync"]["settings"].get("trakt_client_id") in (None, "", "**REDACTED**")
    assert V3_KEY not in repr(diag)
    assert diag["light_sync"]["metadata"]["providers"] == ["tmdb"]


