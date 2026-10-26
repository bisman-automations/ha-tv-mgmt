"""Optional online metadata (genre) lookups via TMDB and Trakt.

Used **only** for search/lookup by title (+ year): the genres feed the
genre -> colour table and TMDB can supply a poster when the player has no
artwork. Nothing user-specific is read - in particular no Trakt watch history,
no OAuth, no scrobbling.

Design:

* :class:`TmdbProvider` / :class:`TraktProvider` - thin async clients on HA's
  shared aiohttp session. Each provider has a :class:`RateLimiter` (minimum
  spacing between requests + cooldown after HTTP 429, honouring
  ``Retry-After``) shared by every zone.
* :class:`MetadataCache` - domain-wide in-memory LRU backed by a ``Store``
  with per-entry TTL (longer for hits, shorter for "not found"); writes are
  coalesced with ``async_delay_save``.
* :class:`MetadataService` - one for the whole home, built from the
  home-wide settings in the panel's Lights tab: tries the providers in the
  configured order, caches results, and degrades gracefully: network errors
  and rate limits just skip the provider for that lookup, an authentication
  failure disables the provider until the key is changed (or Home Assistant
  restarts) and raises a repair issue.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
import re
import time
from typing import Any, Final, Literal

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .color import clean_title, normalize_title
from .const import (
    DOMAIN,
    LOGGER,
    METADATA_CACHE_SIZE,
    METADATA_CACHE_TTL,
    METADATA_DEFAULT_BACKOFF,
    METADATA_MIN_INTERVAL,
    METADATA_NEGATIVE_TTL,
    METADATA_SAVE_DELAY,
    METADATA_TIMEOUT,
    STORAGE_KEY_METADATA,
    STORAGE_VERSION,
    TMDB_API_URL,
    TMDB_GENRES,
    TMDB_IMAGE_URL,
    TRAKT_API_URL,
    MetadataProviderName,
)
from .storage import LRUCache

type MediaKind = Literal["movie", "show"]

_TMDB_POSTER_SIZE: Final = "w500"
_TMDB_BACKDROP_SIZE: Final = "w780"


# --------------------------------------------------------------------------- #
# Errors and value objects
# --------------------------------------------------------------------------- #
class MetadataError(Exception):
    """Base error for metadata lookups."""


class MetadataAuthError(MetadataError):
    """The API key / client ID was rejected."""


class MetadataConnectionError(MetadataError):
    """Network error, timeout or unexpected server response."""


class MetadataRateLimitedError(MetadataError):
    """The provider asked us to slow down (HTTP 429) or we are cooling down."""


@dataclass(frozen=True, slots=True)
class MetadataQuery:
    """What to look up."""

    title: str
    year: int | None = None
    kind: MediaKind = "movie"
    # The player title was a collection / box set ("X: 2-Movie Collection");
    # ``title`` is then the franchise name.
    collection: bool = False

    @property
    def cache_key(self) -> str:
        """Provider-independent cache key."""
        kind = f"{self.kind}+collection" if self.collection else self.kind
        return f"{kind}|{normalize_title(self.title)}|{self.year or ''}"


@dataclass(frozen=True, slots=True)
class MetadataResult:
    """A provider's best match for a query."""

    provider: str
    title: str
    year: int | None = None
    genres: tuple[str, ...] = ()
    poster_url: str | None = None
    backdrop_url: str | None = None
    provider_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form (cache storage, diagnostics)."""
        return {
            "provider": self.provider,
            "title": self.title,
            "year": self.year,
            "genres": list(self.genres),
            "poster_url": self.poster_url,
            "backdrop_url": self.backdrop_url,
            "provider_id": self.provider_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MetadataResult:
        """Inverse of :meth:`as_dict`."""
        return cls(
            provider=str(data["provider"]),
            title=str(data.get("title") or ""),
            year=_as_year(data.get("year")),
            genres=tuple(str(g) for g in data.get("genres") or ()),
            poster_url=data.get("poster_url"),
            backdrop_url=data.get("backdrop_url"),
            provider_id=data.get("provider_id"),
        )


def _as_year(value: Any) -> int | None:
    """Parse a year from an int or a string such as ``2019`` / ``2019-05-01``."""
    if isinstance(value, int) and 1870 <= value <= 2100:
        return value
    if isinstance(value, str) and (m := re.match(r"\s*(\d{4})", value)):
        year = int(m.group(1))
        return year if 1870 <= year <= 2100 else None
    return None


def build_query(
    title: str | None,
    series_title: str | None,
    content_type: str | None,
    year: Any = None,
) -> MetadataQuery | None:
    """Build a lookup query from media player attributes.

    Episodes are looked up by their *series* title (``media_series_title``)
    as a show; everything else by ``media_title`` as a movie. The title is
    cleaned first (:func:`clean_title`): collection / edition / quality
    suffixes are dropped and a trailing ``(1999)`` / ``[1999]`` is used as
    the year. Collections ("X: 2-Movie Collection") are flagged so
    providers can search for the franchise.
    """
    is_series = bool(series_title) or (content_type or "") in {
        "episode",
        "tvshow",
        "season",
    }
    cleaned = clean_title(series_title or title)
    if cleaned is None or not normalize_title(cleaned.title):
        return None
    collection = cleaned.collection and not is_series
    parsed_year = None if collection else (_as_year(year) or cleaned.year)
    return MetadataQuery(
        cleaned.title,
        parsed_year,
        "show" if is_series else "movie",
        collection=collection,
    )


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
class RateLimiter:
    """Serialise requests with a minimum spacing and a 429 cooldown."""

    def __init__(self, min_interval: float = METADATA_MIN_INTERVAL) -> None:
        """Create a limiter allowing one request per ``min_interval`` seconds."""
        self._lock = asyncio.Lock()
        self._min_interval = min_interval
        self._last = 0.0
        self.blocked_until = 0.0

    @property
    def cooling_down(self) -> bool:
        """True while a 429 cooldown is active."""
        return time.monotonic() < self.blocked_until

    async def __aenter__(self) -> None:
        """Wait for our slot (raises during a 429 cooldown)."""
        await self._lock.acquire()
        try:
            if self.cooling_down:
                raise MetadataRateLimitedError("cooling down after HTTP 429")
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        except BaseException:
            self._lock.release()
            raise

    async def __aexit__(self, *exc: object) -> None:
        """Release the slot."""
        self._last = time.monotonic()
        self._lock.release()

    def backoff(self, retry_after: str | None) -> float:
        """Start a cooldown from a ``Retry-After`` header (seconds)."""
        try:
            seconds = max(1.0, float(retry_after)) if retry_after else 0.0
        except ValueError:
            seconds = 0.0
        seconds = seconds or METADATA_DEFAULT_BACKOFF
        self.blocked_until = time.monotonic() + seconds
        return seconds


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class MetadataProvider(ABC):
    """Base class: one HTTP API with credentials and a rate limiter."""

    name: MetadataProviderName
    supports_artwork: bool = False

    def __init__(
        self,
        session: aiohttp.ClientSession,
        credential: str,
        limiter: RateLimiter | None = None,
    ) -> None:
        """Create a provider client."""
        self._session = session
        self._credential = credential.strip()
        self.limiter = limiter or RateLimiter()

    @abstractmethod
    def _request_args(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return ``(headers, extra query params)`` carrying credentials."""

    @abstractmethod
    async def async_validate(self) -> None:
        """Check the credentials (raises :class:`MetadataError`)."""

    @abstractmethod
    async def async_search(self, query: MetadataQuery) -> MetadataResult | None:
        """Return the best match for ``query`` or ``None``."""

    async def _async_get(self, url: str, params: Mapping[str, str]) -> Any:
        """GET ``url`` and return decoded JSON (``None`` on 404)."""
        headers, auth_params = self._request_args()
        try:
            async with (
                self.limiter,
                self._session.get(
                    url,
                    params={**params, **auth_params},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=METADATA_TIMEOUT),
                ) as resp,
            ):
                if resp.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise MetadataAuthError(f"{self.name}: HTTP {resp.status}")
                if resp.status == HTTPStatus.TOO_MANY_REQUESTS:
                    seconds = self.limiter.backoff(resp.headers.get("Retry-After"))
                    raise MetadataRateLimitedError(
                        f"{self.name}: rate limited for {seconds:.0f}s"
                    )
                if resp.status == HTTPStatus.NOT_FOUND:
                    return None
                if resp.status != HTTPStatus.OK:
                    raise MetadataConnectionError(f"{self.name}: HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise MetadataConnectionError(f"{self.name}: {err!r}") from err


def _pick(
    results: Sequence[Mapping[str, Any]],
    query: MetadataQuery,
    title_of: Any,
    year_of: Any,
) -> Mapping[str, Any] | None:
    """Prefer an exact (normalised) title + year match, else the top hit."""
    if not results:
        return None
    wanted = normalize_title(query.title)
    for item in results:
        if normalize_title(str(title_of(item) or "")) == wanted and (
            query.year is None or year_of(item) == query.year
        ):
            return item
    for item in results:
        if normalize_title(str(title_of(item) or "")) == wanted:
            return item
    return results[0]


class TmdbProvider(MetadataProvider):
    """The Movie Database (themoviedb.org) API v3.

    Accepts either a v3 *API key* (sent as ``api_key`` query parameter) or a
    v4 *API Read Access Token* (a JWT, sent as ``Authorization: Bearer``).
    """

    name = MetadataProviderName.TMDB
    supports_artwork = True

    @property
    def uses_bearer_token(self) -> bool:
        """True for a v4 read access token (JWT)."""
        return self._credential.startswith("eyJ") and self._credential.count(".") == 2

    def _request_args(self) -> tuple[dict[str, str], dict[str, str]]:
        headers = {"Accept": "application/json"}
        if self.uses_bearer_token:
            headers["Authorization"] = f"Bearer {self._credential}"
            return headers, {}
        return headers, {"api_key": self._credential}

    async def async_validate(self) -> None:
        """``GET /authentication`` answers 200 for any valid key or token."""
        data = await self._async_get(f"{TMDB_API_URL}/authentication", {})
        if not isinstance(data, dict) or data.get("success") is False:
            raise MetadataAuthError("tmdb: credentials rejected")

    async def async_search(self, query: MetadataQuery) -> MetadataResult | None:
        """Search movies or TV shows and map ``genre_ids`` to names.

        Collection queries try TMDB collections first (genres aggregated over
        the collection's movies) and fall back to a movie search for the
        franchise name.
        """
        if query.collection:
            if result := await self._async_search_collection(query):
                return result
            query = MetadataQuery(query.title, None, query.kind)
        params = {"query": query.title, "include_adult": "false"}
        if query.kind == "show":
            endpoint = "search/tv"
            if query.year:
                params["first_air_date_year"] = str(query.year)
        else:
            endpoint = "search/movie"
            if query.year:
                params["year"] = str(query.year)
        data = await self._async_get(f"{TMDB_API_URL}/{endpoint}", params)
        results = data.get("results") if isinstance(data, dict) else None
        if not results and query.year:
            # Year metadata from players is often off by one; retry without.
            return await self.async_search(MetadataQuery(query.title, None, query.kind))
        if not isinstance(results, list):
            return None

        def title_of(item: Mapping[str, Any]) -> Any:
            return item.get("name") or item.get("title")

        def year_of(item: Mapping[str, Any]) -> int | None:
            return _as_year(item.get("first_air_date") or item.get("release_date"))

        item = _pick(results, query, title_of, year_of)
        if item is None:
            return None
        genres = tuple(
            TMDB_GENRES[g] for g in item.get("genre_ids") or () if g in TMDB_GENRES
        )
        return self._result(
            title=str(title_of(item) or query.title),
            year=year_of(item),
            genres=genres,
            item=item,
        )

    async def _async_search_collection(
        self, query: MetadataQuery
    ) -> MetadataResult | None:
        """Find a TMDB collection by franchise name; genres from its parts."""
        data = await self._async_get(
            f"{TMDB_API_URL}/search/collection",
            {"query": query.title, "include_adult": "false"},
        )
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or not results:
            return None

        def title_of(item: Mapping[str, Any]) -> str:
            # "High School Musical Collection" -> "High School Musical"
            name = str(item.get("name") or "")
            cleaned = clean_title(name)
            return cleaned.title if cleaned else name

        item = _pick(results, query, title_of, lambda _item: None)
        if item is None or "id" not in item:
            return None
        details = await self._async_get(f"{TMDB_API_URL}/collection/{item['id']}", {})
        parts = details.get("parts") if isinstance(details, dict) else None
        counts: dict[int, int] = {}
        years: list[int] = []
        for part in parts if isinstance(parts, list) else ():
            if not isinstance(part, dict):
                continue
            for genre_id in part.get("genre_ids") or ():
                counts[genre_id] = counts.get(genre_id, 0) + 1
            if year := _as_year(part.get("release_date")):
                years.append(year)
        # Most common genres across the collection first (stable for ties).
        ordered = sorted(counts, key=lambda g: -counts[g])
        return self._result(
            title=str(item.get("name") or query.title),
            year=min(years) if years else None,
            genres=tuple(TMDB_GENRES[g] for g in ordered if g in TMDB_GENRES),
            item=item,
            provider_id=f"collection/{item['id']}",
        )

    def _result(
        self,
        *,
        title: str,
        year: int | None,
        genres: tuple[str, ...],
        item: Mapping[str, Any],
        provider_id: str | None = None,
    ) -> MetadataResult:
        poster = item.get("poster_path")
        backdrop = item.get("backdrop_path")
        return MetadataResult(
            provider=self.name,
            title=title,
            year=year,
            genres=genres,
            poster_url=f"{TMDB_IMAGE_URL}/{_TMDB_POSTER_SIZE}{poster}"
            if poster
            else None,
            backdrop_url=f"{TMDB_IMAGE_URL}/{_TMDB_BACKDROP_SIZE}{backdrop}"
            if backdrop
            else None,
            provider_id=provider_id or (str(item["id"]) if "id" in item else None),
        )


class TraktProvider(MetadataProvider):
    """Trakt (trakt.tv) API v2 - public search only, client ID auth.

    Only ``/search`` and ``/genres`` are used; no OAuth, no user data and no
    watch history.
    """

    name = MetadataProviderName.TRAKT

    def _request_args(self) -> tuple[dict[str, str], dict[str, str]]:
        return (
            {
                "Content-Type": "application/json",
                "trakt-api-version": "2",
                "trakt-api-key": self._credential,
            },
            {},
        )

    async def async_validate(self) -> None:
        """``GET /genres/movies`` needs a valid client ID (403 otherwise)."""
        data = await self._async_get(f"{TRAKT_API_URL}/genres/movies", {})
        if not isinstance(data, list):
            raise MetadataConnectionError("trakt: unexpected response")

    async def async_search(self, query: MetadataQuery) -> MetadataResult | None:
        """Search movies or shows with ``extended=full`` (includes genres)."""
        if query.collection:
            # Trakt has no collection search; the franchise name finds the
            # first movie, whose genres are representative.
            query = MetadataQuery(query.title, None, query.kind)
        params = {"query": query.title, "extended": "full", "limit": "10"}
        if query.year:
            params["years"] = str(query.year)
        data = await self._async_get(f"{TRAKT_API_URL}/search/{query.kind}", params)
        if not data and query.year:
            return await self.async_search(MetadataQuery(query.title, None, query.kind))
        if not isinstance(data, list):
            return None
        items = [d[query.kind] for d in data if isinstance(d, dict) and query.kind in d]

        item = _pick(
            items, query, lambda i: i.get("title"), lambda i: _as_year(i.get("year"))
        )
        if item is None:
            return None
        ids = item.get("ids") or {}
        return MetadataResult(
            provider=self.name,
            title=str(item.get("title") or query.title),
            year=_as_year(item.get("year")),
            genres=tuple(
                str(g).replace("-", " ") for g in item.get("genres") or () if g
            ),
            provider_id=str(ids["slug"]) if ids.get("slug") else None,
        )


PROVIDER_CLASSES: Final[dict[MetadataProviderName, type[MetadataProvider]]] = {
    MetadataProviderName.TMDB: TmdbProvider,
    MetadataProviderName.TRAKT: TraktProvider,
}


async def async_validate_credentials(
    hass: HomeAssistant, provider: MetadataProviderName, credential: str
) -> None:
    """Validate one provider key (when it is saved in the Lights tab).

    :raises MetadataAuthError: credential rejected.
    :raises MetadataError: could not connect / unexpected response.
    """
    client = PROVIDER_CLASSES[provider](
        async_get_clientsession(hass), credential, RateLimiter(0)
    )
    await client.async_validate()


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _CacheEntry:
    result: MetadataResult | None
    expires: float  # wall-clock epoch seconds (persisted)


class MetadataCache:
    """Domain-wide lookup cache: in-memory LRU + persisted ``Store`` with TTL.

    Keys are ``"<provider>|<kind>|<normalised title>|<year>"``. ``None``
    (not found) is cached with the shorter negative TTL.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the cache (call :meth:`async_load` before use)."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_METADATA
        )
        self._lru: LRUCache[str, _CacheEntry] = LRUCache(METADATA_CACHE_SIZE)
        self._load_task: asyncio.Task[None] | None = None
        self._loaded = False
        self._hass = hass

    async def async_ensure_loaded(self) -> None:
        """Load the persisted cache once, on first use.

        Loading is deferred from integration setup so start-up never waits
        for (or reads) a cache that may never be needed.
        """
        if self._loaded:
            return
        if self._load_task is None:
            self._load_task = self._hass.async_create_task(
                self.async_load(), "tv_mgmt light sync metadata cache load"
            )
        await asyncio.shield(self._load_task)

    async def async_load(self) -> None:
        """Load persisted entries, dropping expired or malformed ones."""
        data = await self._store.async_load() or {}
        self._loaded = True
        now = time.time()
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return
        # Oldest first so the newest end up most recently used.
        for key, raw in sorted(entries.items(), key=lambda kv: kv[1].get("expires", 0)):
            try:
                expires = float(raw["expires"])
                result = (
                    MetadataResult.from_dict(raw["result"]) if raw["result"] else None
                )
            except (KeyError, TypeError, ValueError):
                continue
            if expires > now:
                self._lru.put(key, _CacheEntry(result, expires))

    def get(self, key: str) -> tuple[bool, MetadataResult | None]:
        """Return ``(found, result)`` for a non-expired key."""
        found, entry = self._lru.get(key)
        if not found or entry is None:
            return False, None
        if entry.expires <= time.time():
            self._lru.pop(key)
            return False, None
        return True, entry.result

    def put(self, key: str, result: MetadataResult | None) -> None:
        """Store a result (``None`` = not found) and schedule a save."""
        ttl = METADATA_CACHE_TTL if result else METADATA_NEGATIVE_TTL
        self._lru.put(key, _CacheEntry(result, time.time() + ttl))
        self._store.async_delay_save(self._data_to_save, METADATA_SAVE_DELAY)

    async def async_clear(self) -> int:
        """Drop everything (memory and disk)."""
        await self.async_ensure_loaded()
        count = self._lru.clear()
        await self._store.async_remove()
        return count

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "entries": {
                key: {
                    "result": entry.result.as_dict() if entry.result else None,
                    "expires": entry.expires,
                }
                for key, entry in self._lru._data.items()  # noqa: SLF001
                if entry is not None
            }
        }

    def stats(self) -> dict[str, int]:
        """Cache statistics for diagnostics."""
        return self._lru.stats()


def issue_id(provider: str) -> str:
    """Repair issue id for a provider whose key was rejected."""
    return f"light_sync_metadata_auth_{provider}"


# --------------------------------------------------------------------------- #
# Service (one for the whole home)
# --------------------------------------------------------------------------- #
@dataclass
class MetadataStats:
    """Counters exposed in diagnostics."""

    lookups: int = 0
    cache_hits: int = 0
    requests: int = 0
    errors: int = 0
    rate_limited: int = 0
    last_error: str | None = None
    disabled: list[str] = field(default_factory=list)


class MetadataService:
    """Ordered provider chain with caching, shared by every zone."""

    def __init__(
        self,
        hass: HomeAssistant,
        providers: Sequence[MetadataProvider],
        cache: MetadataCache,
    ) -> None:
        """Create the service; ``providers`` are tried in order."""
        self.hass = hass
        self.providers = list(providers)
        self._cache = cache
        self._disabled: set[str] = set()
        self._inflight: dict[str, asyncio.Future[MetadataResult | None]] = {}
        self.stats = MetadataStats()

    @property
    def enabled(self) -> bool:
        """True if at least one usable provider is configured."""
        return any(p.name not in self._disabled for p in self.providers)

    @property
    def has_artwork_provider(self) -> bool:
        """True if a configured provider can supply artwork."""
        return any(
            p.supports_artwork and p.name not in self._disabled for p in self.providers
        )

    async def async_lookup(
        self, query: MetadataQuery, *, require_artwork: bool = False
    ) -> MetadataResult | None:
        """Return the first provider hit for ``query`` (cached).

        With ``require_artwork`` only artwork-capable providers are asked and
        only results with a poster/backdrop count.
        """
        self.stats.lookups += 1
        await self._cache.async_ensure_loaded()
        for provider in self.providers:
            if provider.name in self._disabled:
                continue
            if require_artwork and not provider.supports_artwork:
                continue
            result = await self._async_lookup_one(provider, query)
            if result is None:
                continue
            if require_artwork and not (result.poster_url or result.backdrop_url):
                continue
            if not require_artwork and not result.genres:
                continue
            return result
        return None

    async def _async_lookup_one(
        self, provider: MetadataProvider, query: MetadataQuery
    ) -> MetadataResult | None:
        key = f"{provider.name}|{query.cache_key}"
        found, cached = self._cache.get(key)
        if found:
            self.stats.cache_hits += 1
            return cached
        # Coalesce identical concurrent lookups (several zones, one title).
        if (pending := self._inflight.get(key)) is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[MetadataResult | None] = self.hass.loop.create_future()
        self._inflight[key] = future
        result: MetadataResult | None = None
        try:
            self.stats.requests += 1
            result = await provider.async_search(query)
        except MetadataAuthError as err:
            self._disable(provider, err)
        except MetadataRateLimitedError as err:
            self.stats.rate_limited += 1
            LOGGER.debug("Metadata lookup skipped: %s", err)
        except MetadataError as err:
            self.stats.errors += 1
            self.stats.last_error = str(err)
            LOGGER.debug("Metadata lookup failed: %s", err)
        else:
            self._cache.put(key, result)
            LOGGER.debug("%s %s -> %s", provider.name, query, result)
        finally:
            self._inflight.pop(key, None)
            future.set_result(result)
        return result

    def _disable(self, provider: MetadataProvider, err: Exception) -> None:
        """Stop using a provider whose credentials were rejected."""
        if provider.name in self._disabled:
            return
        self._disabled.add(provider.name)
        self.stats.disabled = sorted(self._disabled)
        self.stats.last_error = str(err)
        LOGGER.warning(
            "%s rejected the configured credentials; metadata lookups via %s are "
            "disabled until the key is changed in TV Mgmt, Lights",
            provider.name,
            provider.name,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id(provider.name),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="metadata_auth_failed",
            translation_placeholders={"provider": provider.name.upper()},
        )

    @callback
    def async_clear_issues(self) -> None:
        """Remove the "key rejected" repair issues (the keys changed)."""
        for name in MetadataProviderName:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(name))

    def as_diagnostics(self) -> dict[str, Any]:
        """Diagnostics (no credentials)."""
        return {
            "providers": [p.name for p in self.providers],
            "stats": {
                "lookups": self.stats.lookups,
                "cache_hits": self.stats.cache_hits,
                "requests": self.stats.requests,
                "errors": self.stats.errors,
                "rate_limited": self.stats.rate_limited,
                "last_error": self.stats.last_error,
                "disabled": self.stats.disabled,
            },
            "cache": self._cache.stats(),
        }


def async_create_service(
    hass: HomeAssistant,
    order: Sequence[str],
    credentials: Mapping[MetadataProviderName, str],
    *,
    cache: MetadataCache,
    limiters: dict[str, RateLimiter],
) -> MetadataService | None:
    """Build the provider chain, or ``None`` if no provider has a key.

    ``order`` is the user's provider order; a provider not in it is not used,
    and one in it without a credential is skipped.
    """
    session = async_get_clientsession(hass)
    names: list[MetadataProviderName] = []
    for raw in order:
        try:
            name = MetadataProviderName(raw)
        except ValueError:
            continue
        if name not in names and (credentials.get(name) or "").strip():
            names.append(name)
    if not names:
        return None
    providers = [
        PROVIDER_CLASSES[name](
            session,
            credentials[name],
            limiters.setdefault(f"{name}|{credentials[name]}", RateLimiter()),
        )
        for name in names
    ]
    return MetadataService(hass, providers, cache)
