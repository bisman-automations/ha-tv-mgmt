"""Tests for the TMDB / Trakt metadata providers, cache and service."""

from __future__ import annotations

from http import HTTPStatus
import time
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tv_mgmt.light_sync.const import (
    DOMAIN,
    STORAGE_KEY_METADATA,
    TMDB_API_URL,
    TRAKT_API_URL,
    MetadataProviderName,
)
from custom_components.tv_mgmt.light_sync.metadata import (
    MetadataAuthError,
    MetadataCache,
    MetadataConnectionError,
    MetadataQuery,
    MetadataRateLimitedError,
    MetadataResult,
    RateLimiter,
    TmdbProvider,
    TraktProvider,
    async_create_service,
    async_validate_credentials,
    build_query,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

V3_KEY = "0123456789abcdef0123456789abcdef"
V4_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.c2lnbmF0dXJl"
TRAKT_ID = "trakt-client-id-123"

TMDB_MOVIE = {
    "results": [
        {
            "id": 438631,
            "title": "Dune",
            "release_date": "2021-09-15",
            "genre_ids": [878, 12],
            "poster_path": "/d5NXSklXo0qyIYkgV94XAgMIckC.jpg",
            "backdrop_path": "/jYEW5xZkZk2WTrdbMGAPFuBqbDc.jpg",
        },
        {"id": 841, "title": "Dune", "release_date": "1984-12-14", "genre_ids": [28]},
    ]
}
TMDB_TV = {
    "results": [
        {
            "id": 1399,
            "name": "Game of Thrones",
            "first_air_date": "2011-04-17",
            "genre_ids": [10765, 18, 10759],
            "poster_path": "/got.jpg",
        }
    ]
}
TRAKT_SHOW = [
    {
        "type": "show",
        "score": 1000,
        "show": {
            "title": "The Office",
            "year": 2005,
            "ids": {"trakt": 1, "slug": "the-office-us"},
            "genres": ["comedy", "science-fiction"],
        },
    }
]


def _tmdb(hass: HomeAssistant, credential: str = V3_KEY) -> TmdbProvider:
    return TmdbProvider(async_get_clientsession(hass), credential, RateLimiter(0))


def _trakt(hass: HomeAssistant) -> TraktProvider:
    return TraktProvider(async_get_clientsession(hass), TRAKT_ID, RateLimiter(0))


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("Dune (2021)", None, "movie"), MetadataQuery("Dune", 2021, "movie")),
        (("Dune", None, None, "2021-10-22"), MetadataQuery("Dune", 2021, "movie")),
        (
            ("The Rains of Castamere", "Game of Thrones", "episode"),
            MetadataQuery("Game of Thrones", None, "show"),
        ),
        (("Pilot", None, "episode"), MetadataQuery("Pilot", None, "show")),
        (("Alien [1979]", None, None), MetadataQuery("Alien", 1979, "movie")),
        ((None, None, None), None),
        (("(2020)", None, None), None),
    ],
)
def test_build_query(args: tuple, expected: MetadataQuery | None) -> None:
    """Episodes use the series title; a trailing year is parsed."""
    assert build_query(*args) == expected


async def test_tmdb_search_movie_v3_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """v3 keys go in the query string; genre ids are mapped; year preferred."""
    aioclient_mock.get(f"{TMDB_API_URL}/search/movie", json=TMDB_MOVIE)
    result = await _tmdb(hass).async_search(MetadataQuery("Dune", 2021))
    assert result is not None
    assert result.provider == "tmdb"
    assert result.year == 2021
    assert result.genres == ("Science Fiction", "Adventure")
    assert result.poster_url == (
        "https://image.tmdb.org/t/p/w500/d5NXSklXo0qyIYkgV94XAgMIckC.jpg"
    )
    assert result.backdrop_url.startswith("https://image.tmdb.org/t/p/w780/")
    _method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert url.query["api_key"] == V3_KEY
    assert url.query["year"] == "2021"
    assert "Authorization" not in headers


async def test_tmdb_search_tv_v4_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """v4 read tokens are sent as a bearer header; shows use search/tv."""
    aioclient_mock.get(f"{TMDB_API_URL}/search/tv", json=TMDB_TV)
    result = await _tmdb(hass, V4_TOKEN).async_search(
        MetadataQuery("Game of Thrones", None, "show")
    )
    assert result is not None
    assert result.genres == ("Sci-Fi & Fantasy", "Drama", "Action & Adventure")
    _method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert "api_key" not in url.query
    assert headers["Authorization"] == f"Bearer {V4_TOKEN}"


async def test_tmdb_retries_without_year(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty result with a year retries once without it."""
    aioclient_mock.get(
        f"{TMDB_API_URL}/search/movie", params={"year": "1999"}, json={"results": []}
    )
    aioclient_mock.get(f"{TMDB_API_URL}/search/movie", json=TMDB_MOVIE)
    result = await _tmdb(hass).async_search(MetadataQuery("Dune", 1999))
    assert result is not None
    assert aioclient_mock.call_count == 2


async def test_trakt_search_show(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Trakt sends the client ID header and returns slug genres."""
    aioclient_mock.get(f"{TRAKT_API_URL}/search/show", json=TRAKT_SHOW)
    result = await _trakt(hass).async_search(MetadataQuery("The Office", None, "show"))
    assert result is not None
    assert result.genres == ("comedy", "science fiction")
    assert result.poster_url is None
    assert result.provider_id == "the-office-us"
    _method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert headers["trakt-api-key"] == TRAKT_ID
    assert headers["trakt-api-version"] == "2"
    assert url.query["extended"] == "full"
    # Only the public search endpoint is used - never user/sync/history.
    assert url.path == "/search/show"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (HTTPStatus.UNAUTHORIZED, MetadataAuthError),
        (HTTPStatus.FORBIDDEN, MetadataAuthError),
        (HTTPStatus.INTERNAL_SERVER_ERROR, MetadataConnectionError),
        (HTTPStatus.TOO_MANY_REQUESTS, MetadataRateLimitedError),
    ],
)
async def test_http_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: HTTPStatus,
    error: type[Exception],
) -> None:
    """HTTP status codes map to typed errors."""
    aioclient_mock.get(f"{TRAKT_API_URL}/search/movie", status=status)
    with pytest.raises(error):
        await _trakt(hass).async_search(MetadataQuery("X"))


async def test_timeout_is_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Network failures become MetadataConnectionError."""
    aioclient_mock.get(f"{TMDB_API_URL}/search/movie", exc=TimeoutError())
    with pytest.raises(MetadataConnectionError):
        await _tmdb(hass).async_search(MetadataQuery("X"))


async def test_rate_limit_cooldown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 429 honours Retry-After; requests are refused during the cooldown."""
    aioclient_mock.get(
        f"{TMDB_API_URL}/search/movie",
        status=HTTPStatus.TOO_MANY_REQUESTS,
        headers={"Retry-After": "30"},
    )
    provider = _tmdb(hass)
    with pytest.raises(MetadataRateLimitedError):
        await provider.async_search(MetadataQuery("X"))
    assert provider.limiter.cooling_down
    assert 25 < provider.limiter.blocked_until - time.monotonic() <= 30
    with pytest.raises(MetadataRateLimitedError):
        await provider.async_search(MetadataQuery("Y"))
    assert aioclient_mock.call_count == 1  # second call never hit the API


@pytest.mark.parametrize(
    ("provider", "credential", "url"),
    [
        (MetadataProviderName.TMDB, V3_KEY, f"{TMDB_API_URL}/authentication"),
        (MetadataProviderName.TRAKT, TRAKT_ID, f"{TRAKT_API_URL}/genres/movies"),
    ],
)
async def test_validate_credentials(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    provider: MetadataProviderName,
    credential: str,
    url: str,
) -> None:
    """Validation succeeds on 200 and raises auth errors on 401."""
    aioclient_mock.get(url, json={"success": True} if "themoviedb" in url else [])
    await async_validate_credentials(hass, provider, credential)
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, status=HTTPStatus.UNAUTHORIZED)
    with pytest.raises(MetadataAuthError):
        await async_validate_credentials(hass, provider, credential)


async def test_cache_ttl_and_persistence(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Results persist with a TTL; expired entries are dropped on load."""
    cache = MetadataCache(hass)
    await cache.async_load()
    hit = MetadataResult("tmdb", "Dune", 2021, ("Science Fiction",))
    cache.put("tmdb|movie|dune|2021", hit)
    cache.put("tmdb|movie|nothing|", None)
    assert cache.get("tmdb|movie|dune|2021") == (True, hit)
    assert cache.get("tmdb|movie|nothing|") == (True, None)
    assert cache.get("tmdb|movie|other|") == (False, None)

    # Persist (delayed save) and reload into a fresh cache.
    stored = cache._data_to_save()
    stored["entries"]["tmdb|movie|old|"] = {"result": None, "expires": 1.0}
    hass_storage[STORAGE_KEY_METADATA] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY_METADATA,
        "data": stored,
    }
    fresh = MetadataCache(hass)
    await fresh.async_load()
    assert fresh.get("tmdb|movie|dune|2021") == (True, hit)
    assert fresh.get("tmdb|movie|old|") == (False, None)

    # Expiry in memory.
    with patch(
        "custom_components.tv_mgmt.light_sync.metadata.time.time",
        return_value=time.time() + 10**9,
    ):
        assert fresh.get("tmdb|movie|dune|2021") == (False, None)
    assert await fresh.async_clear() == 1


async def test_service_order_fallback_and_cache(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Providers are tried in order; misses fall through; results are cached."""
    aioclient_mock.get(f"{TRAKT_API_URL}/search/movie", json=[])
    aioclient_mock.get(f"{TMDB_API_URL}/search/movie", json=TMDB_MOVIE)
    cache = MetadataCache(hass)
    service = async_create_service(
        hass,
        ["trakt", "tmdb"],
        {MetadataProviderName.TMDB: V3_KEY, MetadataProviderName.TRAKT: TRAKT_ID},
        cache=cache,
        limiters={"tmdb": RateLimiter(0)},
    )
    assert service is not None
    for provider in service.providers:
        provider.limiter = RateLimiter(0)
    assert [p.name for p in service.providers] == ["trakt", "tmdb"]

    result = await service.async_lookup(MetadataQuery("Dune"))
    assert result is not None
    assert result.provider == "tmdb"
    calls = aioclient_mock.call_count
    assert calls == 2

    again = await service.async_lookup(MetadataQuery("dune"))  # normalised key
    assert again == result
    assert aioclient_mock.call_count == calls  # both answers cached
    assert service.stats.cache_hits == 2

    artwork = await service.async_lookup(MetadataQuery("Dune"), require_artwork=True)
    assert artwork is not None
    assert artwork.poster_url


async def test_service_skips_unconfigured_and_unlisted(hass: HomeAssistant) -> None:
    """No credential or not in the order -> provider not used."""
    cache = MetadataCache(hass)
    assert (
        async_create_service(hass, ["tmdb", "trakt"], {}, cache=cache, limiters={})
        is None
    )
    service = async_create_service(
        hass,
        ["trakt"],
        {MetadataProviderName.TMDB: V3_KEY, MetadataProviderName.TRAKT: TRAKT_ID},
        cache=cache,
        limiters={},
    )
    assert service is not None
    assert [p.name for p in service.providers] == ["trakt"]
    assert not service.has_artwork_provider


async def test_service_auth_failure_disables_and_raises_issue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected key disables the provider (no retries) and creates an issue."""
    aioclient_mock.get(f"{TMDB_API_URL}/search/movie", status=HTTPStatus.UNAUTHORIZED)
    aioclient_mock.get(f"{TRAKT_API_URL}/search/movie", json=[])
    service = async_create_service(
        hass,
        ["tmdb", "trakt"],
        {MetadataProviderName.TMDB: V3_KEY, MetadataProviderName.TRAKT: TRAKT_ID},
        cache=MetadataCache(hass),
        limiters={},
    )
    assert service is not None
    for provider in service.providers:
        provider.limiter = RateLimiter(0)
    assert await service.async_lookup(MetadataQuery("A")) is None
    assert await service.async_lookup(MetadataQuery("B")) is None
    tmdb_calls = [c for c in aioclient_mock.mock_calls if "themoviedb" in str(c[1])]
    assert len(tmdb_calls) == 1
    assert service.stats.disabled == ["tmdb"]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "light_sync_metadata_auth_tmdb")
    assert issue is not None
    assert issue.translation_key == "metadata_auth_failed"
    service.async_clear_issues()
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "light_sync_metadata_auth_tmdb") is None
    )


async def test_service_network_errors_not_cached(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Transient errors degrade gracefully and are retried next time."""
    aioclient_mock.get(f"{TMDB_API_URL}/search/movie", exc=TimeoutError())
    service = async_create_service(
        hass,
        ["tmdb"],
        {MetadataProviderName.TMDB: V3_KEY},
        cache=MetadataCache(hass),
        limiters={},
    )
    assert service is not None
    service.providers[0].limiter = RateLimiter(0)
    assert await service.async_lookup(MetadataQuery("A")) is None
    assert await service.async_lookup(MetadataQuery("A")) is None
    assert aioclient_mock.call_count == 2
    assert service.stats.errors == 2
    assert "tmdb" not in service.stats.disabled


async def test_tmdb_collection_falls_back_to_movie_search(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No matching TMDB collection -> movie search for the franchise name."""
    aioclient_mock.get(f"{TMDB_API_URL}/search/collection", json={"results": []})
    aioclient_mock.get(f"{TMDB_API_URL}/search/movie", json=TMDB_MOVIE)
    query = build_query("Dune: 2-Movie Collection", None, "movie")
    assert query == MetadataQuery("Dune", None, "movie", collection=True)
    result = await _tmdb(hass).async_search(query)
    assert result is not None
    assert result.genres == ("Science Fiction", "Adventure")
    paths = [url.path for _m, url, _d, _h in aioclient_mock.mock_calls]
    assert paths == ["/3/search/collection", "/3/search/movie"]
    assert aioclient_mock.mock_calls[1][1].query["query"] == "Dune"
    assert "year" not in aioclient_mock.mock_calls[1][1].query
