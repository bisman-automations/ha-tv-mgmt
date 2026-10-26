"""Per-light failure handling, timeouts and collection titles.

Ported from Movie Light Sync. One offline light must never stop the others.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.tv_mgmt.light_sync.const import (
    CONF_METADATA_PROVIDERS,
    CONF_TMDB_API_KEY,
    TMDB_API_URL,
)
from custom_components.tv_mgmt.light_sync.controller import SyncOutcome
from custom_components.tv_mgmt.light_sync.storage import DATA_LIGHT_SYNC
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .conftest import (
    COLOR_SENSOR,
    DOMAIN,
    ENTRY_ID,
    MEDIA_PLAYER,
    RESYNC_BUTTON,
    SOURCE_SENSOR,
    SYNCING,
    light_zone,
    set_tv,
    settle,
    tv_entry,
    zone_config,
)

BLE = "light.tv_backlight_ble"
COUCH = "light.couch_strip"
CLOUD = "light.tv_backlight_cloud"
LIGHTS = [BLE, COUCH, CLOUD]
OFFLINE = HomeAssistantError("SwitchBot Cloud device is offline")

TITLE = "High School Musical: 2-Movie Collection"
V3_KEY = "0123456789abcdef0123456789abcdef"
COORD = "custom_components.tv_mgmt.light_sync.controller"
TMDB_SETTINGS = {CONF_TMDB_API_KEY: V3_KEY, CONF_METADATA_PROVIDERS: ["tmdb"]}


class FakeLights:
    """light.turn_on / turn_off handlers with per-entity behaviour."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Register the fake services."""
        self.calls: list[ServiceCall] = []
        # entity_id -> exception to raise, or "hang" (until released).
        self.behavior: dict[str, Exception | str] = {}
        self.release = asyncio.Event()
        hass.services.async_register("light", "turn_on", self._handle)
        hass.services.async_register("light", "turn_off", self._handle)

    async def _handle(self, call: ServiceCall) -> None:
        self.calls.append(call)
        behavior = self.behavior.get(call.data["entity_id"])
        if behavior == "hang":
            await self.release.wait()
        elif isinstance(behavior, Exception):
            raise behavior

    def entities(self, service: str = "turn_on") -> list[str]:
        """Entity of every call to ``service``, in order."""
        return [c.data["entity_id"] for c in self.calls if c.service == service]


def _set_lights(hass: HomeAssistant) -> None:
    hass.states.async_set(
        BLE,
        "on",
        {
            "supported_color_modes": ["rgb"],
            "color_mode": "rgb",
            "rgb_color": (255, 255, 255),
            "brightness": 200,
            "supported_features": 4,  # EFFECT
        },
    )
    hass.states.async_set(
        COUCH,
        "off",
        {"supported_color_modes": ["color_temp", "rgb"], "supported_features": 0},
    )
    hass.states.async_set(
        CLOUD,
        "on",
        {
            "supported_color_modes": ["rgb"],
            "color_mode": "rgb",
            "rgb_color": (10, 20, 30),
            "brightness": 50,
            "supported_features": 0,
        },
    )


@pytest.fixture(autouse=True)
def no_artwork():
    """The player's artwork can't be fetched (title colors and TMDB decide)."""
    with patch(f"{COORD}.LightZone._async_fetch_image", return_value=None):
        yield


@pytest.fixture
def zone() -> dict[str, Any]:
    """Three lights, like a TV backlight, a couch strip and a cloud light."""
    return zone_config(lights=LIGHTS)


@pytest.fixture
def setup_lights(hass: HomeAssistant) -> None:
    """States for the three lights."""
    _set_lights(hass)


@pytest.fixture
async def fake_lights(hass: HomeAssistant) -> FakeLights:
    """Fake light services (registered after the light component loads)."""
    assert await async_setup_component(hass, "media_player", {})
    assert await async_setup_component(hass, "light", {})
    return FakeLights(hass)


@pytest.fixture
def mocked_lights(fake_lights: FakeLights) -> FakeLights:
    """The shared fixture's light mocks are the fake lights here."""
    return fake_lights


@pytest.fixture
async def entry(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> MockConfigEntry:
    """Loaded TV with a title color for the collection title."""
    await hass.services.async_call(
        DOMAIN,
        "set_title_color",
        {"title": "High School Musical", "rgb_color": [174, 0, 27]},
        blocking=True,
    )
    return loaded_entry


async def _play(hass: HomeAssistant, title: str = TITLE) -> None:
    hass.states.async_set(
        MEDIA_PLAYER,
        "playing",
        {
            "media_title": title,
            "media_content_type": "video",
            "app_name": "TV",
            "entity_picture": "/api/media_player_proxy/x",
        },
    )
    await settle(hass)


async def _press_resync(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": RESYNC_BUTTON}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)


def _warnings(caplog: pytest.LogCaptureFixture, entity_id: str) -> int:
    return sum(
        1
        for r in caplog.records
        if r.levelno == logging.WARNING and f"could not set {entity_id}" in r.message
    )


async def test_one_offline_light_does_not_fail_the_others(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    fake_lights: FakeLights,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Partial success is success; only the failing light is named."""
    fake_lights.behavior[COUCH] = OFFLINE
    await _play(hass)

    # One call per light.
    assert sorted(fake_lights.entities()) == sorted(LIGHTS)
    for call in fake_lights.calls:
        assert call.data["rgb_color"] == [174, 0, 27]

    syncing = hass.states.get(SYNCING)
    assert syncing.state == "on"
    assert syncing.attributes["lights"] == [BLE, CLOUD]
    assert syncing.attributes["failed_lights"] == [COUCH]
    assert syncing.attributes["light_errors"] == {
        COUCH: "SwitchBot Cloud device is offline"
    }
    assert syncing.attributes["last_error"] == (
        f"{COUCH}: SwitchBot Cloud device is offline"
    )
    assert BLE not in syncing.attributes["last_error"]
    assert syncing.attributes["last_outcome"] == SyncOutcome.PARTIAL

    color = hass.states.get(COLOR_SENSOR)
    assert color.state == "#ae001b"
    assert color.attributes["media_title"] == TITLE  # original kept for display
    assert color.attributes["search_title"] == "High School Musical"
    assert hass.states.get(SOURCE_SENSOR).state == "title_mapping"
    assert _warnings(caplog, COUCH) == 1

    # Resync retries the failed light; still failing -> no second warning.
    fake_lights.calls.clear()
    await _press_resync(hass)
    assert COUCH in fake_lights.entities()
    assert _warnings(caplog, COUCH) == 1

    # It comes back online: next resync clears the failure.
    del fake_lights.behavior[COUCH]
    await _press_resync(hass)
    syncing = hass.states.get(SYNCING)
    assert syncing.attributes["failed_lights"] == []
    assert syncing.attributes["last_error"] is None
    assert sorted(syncing.attributes["lights"]) == sorted(LIGHTS)
    assert syncing.attributes["last_outcome"] == SyncOutcome.OK


async def test_total_failure_still_updates_state(
    hass: HomeAssistant, entry: MockConfigEntry, fake_lights: FakeLights
) -> None:
    """Every light failing is reported, the state is still published."""
    for light in LIGHTS:
        fake_lights.behavior[light] = OFFLINE
    await _play(hass)

    syncing = hass.states.get(SYNCING)
    assert syncing.state == "off"
    assert syncing.attributes["failed_lights"] == sorted(LIGHTS)
    assert syncing.attributes["last_outcome"] == SyncOutcome.FAILED
    color = hass.states.get(COLOR_SENSOR)
    assert color.state == "#ae001b"  # what it tried to apply
    assert color.attributes["media_title"] == TITLE
    assert hass.states.get(SOURCE_SENSOR).state == "title_mapping"

    # The resync button reports the failure (and retries every light).
    fake_lights.calls.clear()
    with pytest.raises(HomeAssistantError) as err:
        await _press_resync(hass)
    assert err.value.translation_key == "resync_failed"
    assert sorted(fake_lights.entities()) == sorted(LIGHTS)

    # Recovery on the next resync.
    fake_lights.behavior.clear()
    await _press_resync(hass)
    assert hass.states.get(SYNCING).state == "on"


async def test_hanging_light_times_out(
    hass: HomeAssistant, entry: MockConfigEntry, fake_lights: FakeLights
) -> None:
    """A light that never answers cannot hold up the state or other lights."""
    fake_lights.behavior[CLOUD] = "hang"
    with patch(f"{COORD}.LIGHT_CALL_TIMEOUT", 0.05):
        hass.states.async_set(MEDIA_PLAYER, "playing", {"media_title": TITLE})
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        # Can't block on background tasks: the hung call is one of them.
        for _ in range(50):
            await asyncio.sleep(0.02)
            if hass.states.get(SYNCING).state == "on":
                break
    syncing = hass.states.get(SYNCING)
    assert syncing.state == "on"
    assert syncing.attributes["failed_lights"] == [CLOUD]
    assert "no response within 0.05 s" in syncing.attributes["last_error"]
    fake_lights.release.set()
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_unexpected_exception_updates_state(
    hass: HomeAssistant, entry: MockConfigEntry, fake_lights: FakeLights
) -> None:
    """A bug mid-apply is logged and surfaced instead of leaving stale state."""
    with patch(
        f"{COORD}.LightZone._async_resolve_color",
        side_effect=RuntimeError("boom"),
    ):
        await _play(hass)
        syncing = hass.states.get(SYNCING)
        assert syncing.state == "off"
        assert "boom" in syncing.attributes["last_error"]
        assert hass.states.get(COLOR_SENSOR).attributes["media_title"] == TITLE
        assert light_zone(entry).last_outcome is SyncOutcome.FAILED
    # Fixed -> resync applies.
    await _press_resync(hass)
    assert hass.states.get(SYNCING).state == "on"


async def test_partial_restore_keeps_failed_lights(
    hass: HomeAssistant, entry: MockConfigEntry, fake_lights: FakeLights
) -> None:
    """Lights that could not be restored stay in the snapshot for a retry."""
    await _play(hass)
    coordinator = light_zone(entry)
    assert set(coordinator._snapshot) == set(LIGHTS)

    fake_lights.behavior[CLOUD] = OFFLINE
    fake_lights.calls.clear()
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await settle(hass)  # debouncer -> schedules the idle action
    await settle(hass)  # idle action (restore, delay 0)

    assert BLE in fake_lights.entities("turn_on")  # was on -> restored
    assert COUCH in fake_lights.entities("turn_off")  # was off
    assert set(coordinator._snapshot) == {CLOUD}
    syncing = hass.states.get(SYNCING)
    assert syncing.state == "off"
    assert syncing.attributes["failed_lights"] == [CLOUD]

    # Next session end retries the remaining light.
    del fake_lights.behavior[CLOUD]
    await _play(hass, "Inception")
    hass.states.async_set(MEDIA_PLAYER, "idle", {})
    await settle(hass)
    await settle(hass)
    assert coordinator._snapshot == {}


@pytest.mark.parametrize("light_sync_settings", [TMDB_SETTINGS])
async def test_setup_does_not_wait_for_evaluation(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    fake_lights: FakeLights,
    setup_lights: None,
    zone: dict[str, Any],
    light_sync_settings: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Setup is fast: no network, no metadata cache load, evaluation in bg."""
    hass_storage["tv_mgmt.light_sync"] = {
        "version": 1,
        "key": "tv_mgmt.light_sync",
        "data": {"settings": light_sync_settings, "zones": {ENTRY_ID: [zone]}},
    }
    set_tv(hass)
    hass.states.async_set(MEDIA_PLAYER, "playing", {"media_title": TITLE})
    entry = tv_entry()
    entry.add_to_hass(hass)
    started = asyncio.Event()
    gate = asyncio.Event()

    async def _slow_evaluate(*_args: Any) -> None:
        started.set()
        await gate.wait()

    with patch(f"{COORD}.LightZone._async_evaluate", _slow_evaluate):
        begin = time.monotonic()
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert time.monotonic() - begin < 1
        await asyncio.sleep(0)
        assert started.is_set()  # initial evaluation was started...
        assert not gate.is_set()  # ...but setup did not wait for it
        assert aioclient_mock.call_count == 0
        assert not hass.data[DATA_LIGHT_SYNC].metadata._loaded  # lazy
        gate.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert fake_lights.calls == []


@pytest.mark.parametrize("light_sync_settings", [TMDB_SETTINGS])
async def test_collection_title_uses_tmdb_collection(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fake_lights: FakeLights,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """'X: 2-Movie Collection' is looked up as the X collection on TMDB."""
    aioclient_mock.get(
        f"{TMDB_API_URL}/search/collection",
        json={"results": [{"id": 87, "name": "High School Musical Collection"}]},
    )
    aioclient_mock.get(
        f"{TMDB_API_URL}/collection/87",
        json={
            "parts": [
                {"genre_ids": [10402, 10751], "release_date": "2006-01-20"},
                {"genre_ids": [10402, 35], "release_date": "2007-08-17"},
            ]
        },
    )
    hass.states.async_set(MEDIA_PLAYER, "playing", {"media_title": TITLE})
    await settle(hass)

    urls = [URL(str(call[1])) for call in aioclient_mock.mock_calls]
    assert any(
        url.path.endswith("/search/collection")
        and url.query.get("query") == "High School Musical"
        for url in urls
    )
    attrs = hass.states.get(COLOR_SENSOR).attributes
    assert attrs["metadata_provider"] == "tmdb"
    assert attrs["genre"] == "music"  # most common genre over the collection
    assert attrs["media_title"] == TITLE
    assert hass.states.get(SOURCE_SENSOR).state == "genre"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (TITLE, ("High School Musical", None, True)),
        (
            "High School Musical (3-Movie Collection)",
            ("High School Musical", None, True),
        ),
        ("Blade Runner (Director's Cut) (1982)", ("Blade Runner", 1982, False)),
        ("Blade Runner: The Final Cut", ("Blade Runner", None, False)),
        ("Aliens - Extended Edition 4K", ("Aliens", None, False)),
        ("Ocean's Trilogy (4K Ultra HD)", ("Ocean's", None, True)),
        ("Kill Bill Double Feature", ("Kill Bill", None, True)),
        ("Star Wars: The Complete Saga", ("Star Wars", None, True)),
        ("Frozen | 4K", ("Frozen", None, False)),
        ("Avatar [2009]", ("Avatar", 2009, False)),
        # Real titles that merely look like tags are left alone.
        ("The Collection", ("The Collection", None, False)),
        ("The Final Cut", ("The Final Cut", None, False)),
        ("Uncut Gems", ("Uncut Gems", None, False)),
        ("Mission: Impossible", ("Mission: Impossible", None, False)),
        ("Toy Story 4", ("Toy Story 4", None, False)),
        ("Dune: Part Two", ("Dune: Part Two", None, False)),
    ],
)
def test_clean_title(raw: str, expected: tuple[str, int | None, bool]) -> None:
    """Collection / edition / quality suffixes are stripped."""
    from custom_components.tv_mgmt.light_sync.color import clean_title  # noqa: PLC0415

    cleaned = clean_title(raw)
    assert cleaned is not None
    assert (cleaned.title, cleaned.year, cleaned.collection) == expected


def test_build_query_uses_clean_title() -> None:
    """Metadata queries use the cleaned title and flag collections."""
    from custom_components.tv_mgmt.light_sync.metadata import build_query  # noqa: PLC0415

    query = build_query(TITLE, None, "video", None)
    assert query is not None
    assert (query.title, query.year, query.kind, query.collection) == (
        "High School Musical",
        None,
        "movie",
        True,
    )
    plain = build_query("Blade Runner (Director's Cut) (1982)", None, "movie", None)
    assert plain is not None
    assert (plain.title, plain.year, plain.collection) == ("Blade Runner", 1982, False)
    assert (
        query.cache_key
        != build_query("High School Musical", None, None, None).cache_key
    )
