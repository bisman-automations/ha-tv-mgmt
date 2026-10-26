"""Light sync storage: zones and settings, title colours, caches.

Everything here is home-wide (shared by every TV) and lives in
``hass.data[DATA_LIGHT_SYNC]``, created once in TV Mgmt's ``async_setup`` so
the services and the panel work before any TV is loaded.

* :class:`LightSyncConfig` - the zones of every TV and the home-wide settings
  (TMDB / Trakt), edited from the panel's Lights tab. Kept out of the config
  entries on purpose: saving a zone must not reload the TV.
* :class:`TitleColorStore` - the user's title -> colour map.
* :class:`LRUCache` / :class:`ColorCache` - in-memory artwork analysis cache.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable
import copy
from dataclasses import dataclass, field
import difflib
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .color import (
    RGB,
    ExtractionParams,
    ImageAnalysis,
    clamp_rgb,
    normalize_title,
    rgb_to_hex,
)
from .const import (
    CONF_ENABLED,
    CONF_LIGHTS,
    CONF_NAME,
    CONF_ZONE_ID,
    DOMAIN,
    FUZZY_MATCH_CUTOFF,
    IMAGE_CACHE_SIZE,
    LOGGER,
    SETTINGS_DEFAULTS,
    SIGNAL_TITLE_COLORS_UPDATED,
    STORAGE_KEY_CONFIG,
    STORAGE_KEY_TITLES,
    STORAGE_VERSION,
    ZONE_DEFAULTS,
)

if TYPE_CHECKING:
    from .metadata import MetadataCache, MetadataService, RateLimiter


class TitleColorRecord(TypedDict):
    """One persisted mapping record."""

    title: str
    rgb_color: list[int]
    updated: str


class TitleColorStore:
    """User-editable mapping of media titles to RGB colours.

    Keys are :func:`normalize_title` results so lookups are case, accent,
    article and punctuation insensitive. The original title is kept for
    display. Optionally falls back to fuzzy matching via :mod:`difflib`.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the store (call :meth:`async_load` before use)."""
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_TITLES
        )
        self._titles: dict[str, TitleColorRecord] = {}

    async def async_load(self) -> None:
        """Load persisted data from ``.storage``."""
        data = await self._store.async_load() or {}
        titles = data.get("titles", {})
        if not isinstance(titles, dict):
            LOGGER.warning("Ignoring malformed title colour storage")
            titles = {}
        self._titles = {}
        for key, record in titles.items():
            try:
                self._titles[key] = TitleColorRecord(
                    title=str(record["title"]),
                    rgb_color=list(clamp_rgb(record["rgb_color"])),
                    updated=str(record.get("updated", "")),
                )
            except KeyError, TypeError, ValueError:
                LOGGER.warning("Skipping invalid title colour record %r", key)
        LOGGER.debug("Loaded %d title colour mappings", len(self._titles))

    async def _async_save_and_notify(self) -> None:
        await self._store.async_save({"titles": self._titles})
        async_dispatcher_send(self.hass, SIGNAL_TITLE_COLORS_UPDATED)

    async def async_set(self, title: str, rgb: RGB) -> str:
        """Create/replace a mapping. Returns the normalised key."""
        key = normalize_title(title)
        if not key:
            raise ValueError("Title is empty after normalisation")
        self._titles[key] = TitleColorRecord(
            title=title.strip(),
            rgb_color=list(clamp_rgb(rgb)),
            updated=dt_util.utcnow().isoformat(),
        )
        await self._async_save_and_notify()
        return key

    async def async_remove(self, title: str) -> bool:
        """Remove a mapping. Returns ``False`` if it did not exist."""
        key = normalize_title(title)
        if self._titles.pop(key, None) is None:
            return False
        await self._async_save_and_notify()
        return True

    @callback
    def lookup(
        self, titles: list[str | None], *, fuzzy: bool = True
    ) -> tuple[RGB, str] | None:
        """Return ``(rgb, matched_title)`` for the first matching candidate."""
        keys = [k for k in (normalize_title(t) for t in titles) if k]
        for key in keys:
            if record := self._titles.get(key):
                return clamp_rgb(record["rgb_color"]), record["title"]
        if fuzzy and self._titles:
            for key in keys:
                if match := difflib.get_close_matches(
                    key, self._titles, n=1, cutoff=FUZZY_MATCH_CUTOFF
                ):
                    record = self._titles[match[0]]
                    LOGGER.debug("Fuzzy matched %r -> %r", key, record["title"])
                    return clamp_rgb(record["rgb_color"]), record["title"]
        return None

    @callback
    def as_list(self) -> list[dict[str, Any]]:
        """Return all mappings, sorted by title, for service responses."""
        return [
            {
                "title": rec["title"],
                "key": key,
                "rgb_color": rec["rgb_color"],
                "hex": rgb_to_hex(rec["rgb_color"]),
                "updated": rec["updated"],
            }
            for key, rec in sorted(
                self._titles.items(), key=lambda kv: kv[1]["title"].casefold()
            )
        ]

    def __len__(self) -> int:
        """Return the number of mappings."""
        return len(self._titles)


class LRUCache[K: Hashable, V]:
    """Small in-memory LRU cache with hit/miss statistics.

    ``get`` returns ``(found, value)`` so ``None`` can be cached too - e.g.
    an achromatic poster is not downloaded and analysed again and again.
    """

    def __init__(self, max_size: int) -> None:
        """Create an empty cache."""
        self._data: OrderedDict[K, V] = OrderedDict()
        self._max = max_size
        self.hits = 0
        self.misses = 0

    def get(self, key: K) -> tuple[bool, V | None]:
        """Return ``(found, value)`` and refresh recency on hit."""
        if key in self._data:
            self._data.move_to_end(key)
            self.hits += 1
            return True, self._data[key]
        self.misses += 1
        return False, None

    def put(self, key: K, value: V) -> None:
        """Insert a value, evicting the least recently used entry."""
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def pop(self, key: K) -> None:
        """Drop ``key`` if present."""
        self._data.pop(key, None)

    def clear(self) -> int:
        """Empty the cache, returning how many entries were dropped."""
        count = len(self._data)
        self._data.clear()
        self.hits = self.misses = 0
        return count

    def __len__(self) -> int:
        """Number of cached entries."""
        return len(self._data)

    def stats(self) -> dict[str, int]:
        """Return cache statistics for diagnostics."""
        return {
            "size": len(self._data),
            "max_size": self._max,
            "hits": self.hits,
            "misses": self.misses,
        }


# (artwork key, extraction params, palette size) -> analysis (None = no colour)
type ArtworkCacheKey = tuple[str, ExtractionParams, int]


class ColorCache(LRUCache[ArtworkCacheKey, ImageAnalysis | None]):
    """Artwork analysis cache shared by every zone."""

    def __init__(self, max_size: int = IMAGE_CACHE_SIZE) -> None:
        """Create an empty artwork cache."""
        super().__init__(max_size)


# Seconds to wait before writing a switch flip (sync on/off) to disk.
_CONFIG_SAVE_DELAY = 5


class LightSyncConfig:
    """Zones per TV, and the home-wide light sync settings.

    Saved as ``{"settings": {...}, "zones": {entry_id: [zone, ...]}}``. Zones
    are stored as saved by the panel and merged over :data:`ZONE_DEFAULTS`
    when read, so settings added in later versions get their defaults.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the store (call :meth:`async_load` before use)."""
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_CONFIG
        )
        self._settings: dict[str, Any] = {}
        self._zones: dict[str, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        """Load the saved zones and settings."""
        data = await self._store.async_load() or {}
        settings = data.get("settings")
        self._settings = dict(settings) if isinstance(settings, dict) else {}
        zones = data.get("zones")
        self._zones = {}
        if isinstance(zones, dict):
            for entry_id, items in zones.items():
                if isinstance(items, list):
                    self._zones[str(entry_id)] = [
                        dict(z)
                        for z in items
                        if isinstance(z, dict) and z.get(CONF_ZONE_ID)
                    ]

    def _data(self) -> dict[str, Any]:
        return {"settings": self._settings, "zones": self._zones}

    async def _async_save(self) -> None:
        await self._store.async_save(self._data())

    # ---- home-wide settings ----------------------------------------------------

    @property
    def settings(self) -> dict[str, Any]:
        """Home-wide settings merged over their defaults (a copy)."""
        return {**copy.deepcopy(SETTINGS_DEFAULTS), **copy.deepcopy(self._settings)}

    async def async_set_settings(self, settings: dict[str, Any]) -> None:
        """Replace the home-wide settings."""
        self._settings = {k: v for k, v in settings.items() if k in SETTINGS_DEFAULTS}
        await self._async_save()

    # ---- zones -----------------------------------------------------------------

    @callback
    def zones_for(self, entry_id: str) -> list[dict[str, Any]]:
        """A TV's zones, each merged over :data:`ZONE_DEFAULTS` (copies)."""
        return [merge_zone(zone) for zone in self._zones.get(entry_id, [])]

    @callback
    def zone(self, entry_id: str, zone_id: str) -> dict[str, Any] | None:
        """One zone, merged over the defaults, or ``None``."""
        return next(
            (z for z in self.zones_for(entry_id) if z[CONF_ZONE_ID] == zone_id), None
        )

    async def async_save_zone(self, entry_id: str, zone: dict[str, Any]) -> None:
        """Add a zone, or replace the one with the same id (keeping its place)."""
        zones = self._zones.setdefault(entry_id, [])
        clean = {
            k: v for k, v in zone.items() if k in ZONE_DEFAULTS or k in _ZONE_IDENTITY
        }
        for index, existing in enumerate(zones):
            if existing.get(CONF_ZONE_ID) == clean[CONF_ZONE_ID]:
                zones[index] = clean
                break
        else:
            zones.append(clean)
        await self._async_save()

    async def async_remove_zone(self, entry_id: str, zone_id: str) -> bool:
        """Delete a zone. Returns ``False`` if it didn't exist."""
        zones = self._zones.get(entry_id, [])
        kept = [z for z in zones if z.get(CONF_ZONE_ID) != zone_id]
        if len(kept) == len(zones):
            return False
        self._zones[entry_id] = kept
        await self._async_save()
        return True

    @callback
    def set_enabled(self, entry_id: str, zone_id: str, enabled: bool) -> None:
        """Remember a zone's sync switch (written to disk shortly after)."""
        for zone in self._zones.get(entry_id, []):
            if zone.get(CONF_ZONE_ID) == zone_id:
                zone[CONF_ENABLED] = enabled
                self._store.async_delay_save(self._data, _CONFIG_SAVE_DELAY)
                return

    @callback
    def update_zone(self, entry_id: str, zone_id: str, changes: dict[str, Any]) -> None:
        """Change a few settings of a zone, from its entities (saved shortly after)."""
        for zone in self._zones.get(entry_id, []):
            if zone.get(CONF_ZONE_ID) == zone_id:
                zone.update({k: v for k, v in changes.items() if k in ZONE_DEFAULTS})
                self._store.async_delay_save(self._data, _CONFIG_SAVE_DELAY)
                return

    async def async_remove_entry(self, entry_id: str) -> None:
        """Forget a TV's zones (the TV was deleted from TV Mgmt)."""
        if self._zones.pop(entry_id, None) is not None:
            await self._async_save()


# Keys a zone has besides its settings.
_ZONE_IDENTITY = frozenset({CONF_ZONE_ID, CONF_NAME, CONF_LIGHTS})


def merge_zone(zone: dict[str, Any]) -> dict[str, Any]:
    """A stored zone merged over the defaults (deep copy, never shared)."""
    merged = {**copy.deepcopy(ZONE_DEFAULTS), **copy.deepcopy(zone)}
    merged.setdefault(CONF_NAME, "")
    merged.setdefault(CONF_LIGHTS, [])
    return merged


@dataclass
class LightSyncData:
    """Light sync objects shared by every TV."""

    config: LightSyncConfig
    titles: TitleColorStore
    cache: ColorCache = field(default_factory=ColorCache)
    metadata: MetadataCache | None = None
    # Per provider+credential rate limiters, shared by every zone.
    metadata_limiters: dict[str, RateLimiter] = field(default_factory=dict)
    # The TMDB / Trakt lookup chain built from the home-wide settings
    # (``None`` when no provider has a key).
    metadata_service: MetadataService | None = None


DATA_LIGHT_SYNC: HassKey[LightSyncData] = HassKey(f"{DOMAIN}_light_sync")
