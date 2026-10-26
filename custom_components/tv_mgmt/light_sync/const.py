"""Constants for TV Mgmt light sync (lights that follow what's on the TV).

Light sync came from the Movie Light Sync integration and now lives inside
TV Mgmt. Everything the light sync modules share lives here: zone setting
keys, defaults, option lists, the built-in genre/mood colour table, and the
keyword table used to guess a genre from a title when the media player does
not report one.

Settings are not stored in the config entry. Zones (per TV) and the home-wide
settings (TMDB / Trakt keys) are kept in TV Mgmt's own storage and edited from
the Lights tab of the sidebar panel, so saving them never reloads the TV.
"""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import Final

from ..const import DOMAIN

LOGGER: Final = logging.getLogger(__package__)

MANUFACTURER: Final = "TV Mgmt"
MODEL: Final = "Light sync zone"

# --------------------------------------------------------------------------- #
# Zone settings (one zone = a set of lights following one TV)
# --------------------------------------------------------------------------- #
CONF_ZONE_ID: Final = "id"
CONF_NAME: Final = "name"
CONF_LIGHTS: Final = "lights"
# The TV's own media player and its linked Apple TV are used by default;
# either can be switched off, and other players (for example the Music
# Assistant player that reports the Apple TV's playback) can be added.
CONF_USE_TV_PLAYER: Final = "use_tv_player"
CONF_USE_APPLE_TV: Final = "use_apple_tv"
CONF_EXTRA_PLAYERS: Final = "extra_players"
# Only colour the lights while the TV is on (a TV whose power state is
# unknown counts as on).
CONF_REQUIRE_TV_ON: Final = "require_tv_on"
# Whether the zone's sync switch is on (also settable from the switch entity).
CONF_ENABLED: Final = "enabled"

# Lighting
CONF_BRIGHTNESS_PCT: Final = "brightness_pct"
CONF_TRANSITION: Final = "transition"
CONF_UNSUPPORTED_LIGHTS: Final = "unsupported_lights"
CONF_EXPAND_GROUPS: Final = "expand_groups"

# Colour resolution
CONF_DEFAULT_COLOR: Final = "default_color"
CONF_COLOR_SOURCES: Final = "color_sources"
CONF_FUZZY_TITLE_MATCH: Final = "fuzzy_title_match"
CONF_MEDIA_TYPES: Final = "media_types"

# Poster extraction
CONF_EXTRACTION_METHOD: Final = "extraction_method"
CONF_MIN_SATURATION: Final = "min_saturation"
CONF_SATURATION_BOOST: Final = "saturation_boost"
CONF_MIN_BRIGHTNESS: Final = "min_brightness"
CONF_DARK_THRESHOLD: Final = "dark_threshold"
CONF_LIGHT_THRESHOLD: Final = "light_threshold"
CONF_SKIP_ACHROMATIC: Final = "skip_achromatic"

# Multi-zone palette
CONF_COLOR_MODE: Final = "color_mode"
CONF_PALETTE_SIZE: Final = "palette_size"
CONF_PALETTE_STRATEGY: Final = "palette_strategy"

# Online lookups. The provider order and keys are home-wide settings; whether
# a zone may use a TMDB poster when the player has no artwork is per zone.
CONF_METADATA_PROVIDERS: Final = "metadata_providers"
CONF_TMDB_API_KEY: Final = "tmdb_api_key"
CONF_TRAKT_CLIENT_ID: Final = "trakt_client_id"
CONF_METADATA_ARTWORK: Final = "metadata_artwork"

# Behaviour
CONF_PAUSE_BEHAVIOR: Final = "pause_behavior"
CONF_STOP_BEHAVIOR: Final = "stop_behavior"
CONF_RESTORE_DELAY: Final = "restore_delay"
CONF_PERSIST_SNAPSHOT: Final = "persist_snapshot"



class ColorSource(StrEnum):
    """Where a colour came from (also the ENUM sensor's options)."""

    TITLE_MAPPING = "title_mapping"
    POSTER = "poster"
    GENRE = "genre"
    DEFAULT = "default"
    NONE = "none"


class ExtractionMethod(StrEnum):
    """How the poster colour is computed."""

    DOMINANT = "dominant"
    """Most frequent colour cluster (median-cut quantisation)."""
    VIBRANT = "vibrant"
    """Cluster with best frequency x saturation x value score."""
    AVERAGE = "average"
    """Arithmetic mean of all (filtered) pixels."""


class SyncColorMode(StrEnum):
    """One colour for every light, or a poster palette spread across them."""

    SINGLE = "single"
    PALETTE = "palette"


class PaletteStrategy(StrEnum):
    """How palette colours are assigned to the (ordered) lights."""

    SPREAD = "spread"
    """Round-robin: light 1 gets colour 1, light 2 colour 2, ..."""
    DOMINANT_FIRST = "dominant_first"
    """First light gets the top colour, the rest share the others."""
    MIRRORED = "mirrored"
    """Symmetric from both ends (left/right strips match)."""


class MetadataProviderName(StrEnum):
    """Supported online metadata providers."""

    TMDB = "tmdb"
    TRAKT = "trakt"


class IdleBehavior(StrEnum):
    """What to do with the lights when playback pauses or stops."""

    RESTORE = "restore"
    TURN_OFF = "turn_off"
    DEFAULT_COLOR = "default_color"
    LEAVE = "leave"


class UnsupportedLightBehavior(StrEnum):
    """What to do with lights that cannot display colour."""

    BRIGHTNESS_ONLY = "brightness_only"
    SKIP = "skip"


# Colour sources the user can order. DEFAULT is always used as the final
# fallback even if the user removes it from the list.
SELECTABLE_COLOR_SOURCES: Final[list[str]] = [
    ColorSource.TITLE_MAPPING,
    ColorSource.POSTER,
    ColorSource.GENRE,
    ColorSource.DEFAULT,
]

# Named colour-source priority presets (used by the dashboard select). A
# list in the options that matches none of them shows as "unknown" there.
COLOR_SOURCE_PRESETS: Final[dict[str, list[str]]] = {
    "balanced": [
        ColorSource.TITLE_MAPPING,
        ColorSource.POSTER,
        ColorSource.GENRE,
        ColorSource.DEFAULT,
    ],
    "artwork_first": [
        ColorSource.POSTER,
        ColorSource.TITLE_MAPPING,
        ColorSource.GENRE,
        ColorSource.DEFAULT,
    ],
    "genre_first": [
        ColorSource.TITLE_MAPPING,
        ColorSource.GENRE,
        ColorSource.POSTER,
        ColorSource.DEFAULT,
    ],
    "titles_only": [ColorSource.TITLE_MAPPING, ColorSource.DEFAULT],
    "artwork_only": [ColorSource.POSTER, ColorSource.DEFAULT],
    "genre_only": [ColorSource.GENRE, ColorSource.DEFAULT],
}

# media_content_type values offered in the media-type filter. An empty
# selection means "everything".
MEDIA_TYPE_OPTIONS: Final[list[str]] = [
    "movie",
    "tvshow",
    "episode",
    "video",
    "channel",
    "music",
    "podcast",
    "game",
    "app",
]

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
DEFAULT_NAME: Final = "Lights"
DEFAULT_BRIGHTNESS_PCT: Final = 60
DEFAULT_TRANSITION: Final = 2.0
DEFAULT_DEFAULT_COLOR: Final[list[int]] = [255, 147, 41]  # warm "candle" amber
DEFAULT_COLOR_SOURCES: Final[list[str]] = list(SELECTABLE_COLOR_SOURCES)
DEFAULT_FUZZY_TITLE_MATCH: Final = True
DEFAULT_MEDIA_TYPES: Final[list[str]] = []
DEFAULT_EXTRACTION_METHOD: Final = ExtractionMethod.VIBRANT
DEFAULT_MIN_SATURATION: Final = 35  # percent
DEFAULT_SATURATION_BOOST: Final = 1.3  # multiplier
DEFAULT_MIN_BRIGHTNESS: Final = 45  # percent (HSV value floor)
DEFAULT_DARK_THRESHOLD: Final = 32  # 0-255 luma, 0 disables
DEFAULT_LIGHT_THRESHOLD: Final = 235  # 0-255 per channel, 255 disables
DEFAULT_SKIP_ACHROMATIC: Final = True
DEFAULT_PAUSE_BEHAVIOR: Final = IdleBehavior.LEAVE
DEFAULT_STOP_BEHAVIOR: Final = IdleBehavior.RESTORE
DEFAULT_RESTORE_DELAY: Final = 5.0  # seconds
DEFAULT_UNSUPPORTED_LIGHTS: Final = UnsupportedLightBehavior.BRIGHTNESS_ONLY
DEFAULT_EXPAND_GROUPS: Final = True
DEFAULT_PERSIST_SNAPSHOT: Final = True
DEFAULT_COLOR_MODE: Final = SyncColorMode.SINGLE
DEFAULT_PALETTE_SIZE: Final = 4
MIN_PALETTE_SIZE: Final = 3
MAX_PALETTE_SIZE: Final = 5
DEFAULT_PALETTE_STRATEGY: Final = PaletteStrategy.SPREAD
DEFAULT_METADATA_PROVIDERS: Final[list[str]] = [
    MetadataProviderName.TMDB,
    MetadataProviderName.TRAKT,
]
DEFAULT_METADATA_ARTWORK: Final = False

# Every per-zone setting and its default. A stored zone is always merged on
# top of these, so a zone saved by an older version simply gets the defaults
# for anything added later.
ZONE_DEFAULTS: Final[dict[str, object]] = {
    CONF_ENABLED: True,
    CONF_USE_TV_PLAYER: True,
    CONF_USE_APPLE_TV: True,
    CONF_EXTRA_PLAYERS: [],
    CONF_REQUIRE_TV_ON: True,
    CONF_BRIGHTNESS_PCT: DEFAULT_BRIGHTNESS_PCT,
    CONF_TRANSITION: DEFAULT_TRANSITION,
    CONF_UNSUPPORTED_LIGHTS: DEFAULT_UNSUPPORTED_LIGHTS.value,
    CONF_EXPAND_GROUPS: DEFAULT_EXPAND_GROUPS,
    CONF_DEFAULT_COLOR: DEFAULT_DEFAULT_COLOR,
    CONF_COLOR_SOURCES: DEFAULT_COLOR_SOURCES,
    CONF_FUZZY_TITLE_MATCH: DEFAULT_FUZZY_TITLE_MATCH,
    CONF_MEDIA_TYPES: DEFAULT_MEDIA_TYPES,
    CONF_EXTRACTION_METHOD: DEFAULT_EXTRACTION_METHOD.value,
    CONF_MIN_SATURATION: DEFAULT_MIN_SATURATION,
    CONF_SATURATION_BOOST: DEFAULT_SATURATION_BOOST,
    CONF_MIN_BRIGHTNESS: DEFAULT_MIN_BRIGHTNESS,
    CONF_DARK_THRESHOLD: DEFAULT_DARK_THRESHOLD,
    CONF_LIGHT_THRESHOLD: DEFAULT_LIGHT_THRESHOLD,
    CONF_SKIP_ACHROMATIC: DEFAULT_SKIP_ACHROMATIC,
    CONF_PAUSE_BEHAVIOR: DEFAULT_PAUSE_BEHAVIOR.value,
    CONF_STOP_BEHAVIOR: DEFAULT_STOP_BEHAVIOR.value,
    CONF_RESTORE_DELAY: DEFAULT_RESTORE_DELAY,
    CONF_PERSIST_SNAPSHOT: DEFAULT_PERSIST_SNAPSHOT,
    CONF_COLOR_MODE: DEFAULT_COLOR_MODE.value,
    CONF_PALETTE_SIZE: DEFAULT_PALETTE_SIZE,
    CONF_PALETTE_STRATEGY: DEFAULT_PALETTE_STRATEGY.value,
    CONF_METADATA_ARTWORK: DEFAULT_METADATA_ARTWORK,
}

# Home-wide light sync settings (shared by every TV) and their defaults.
SETTINGS_DEFAULTS: Final[dict[str, object]] = {
    CONF_METADATA_PROVIDERS: DEFAULT_METADATA_PROVIDERS,
    CONF_TMDB_API_KEY: "",
    CONF_TRAKT_CLIENT_ID: "",
}

# Settings that (re)build the TMDB / Trakt lookup chain when they change.
METADATA_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        CONF_METADATA_PROVIDERS,
        CONF_TMDB_API_KEY,
        CONF_TRAKT_CLIENT_ID,
    }
)

# Zone settings that change which players or lights are watched; changing
# them re-subscribes the zone's state listeners (still without a reload).
TRACKING_OPTIONS: Final[frozenset[str]] = frozenset(
    {CONF_LIGHTS, CONF_USE_TV_PLAYER, CONF_USE_APPLE_TV, CONF_EXTRA_PLAYERS, CONF_EXPAND_GROUPS}
)

# Most zones one TV may have; keeps the panel and the device list readable.
MAX_ZONES_PER_TV: Final = 8

# --------------------------------------------------------------------------- #
# Tunables that are intentionally not user options
# --------------------------------------------------------------------------- #
IMAGE_FETCH_TIMEOUT: Final = 10  # seconds
# Per light service call. Cloud lights (e.g. an offline SwitchBot) can take
# long to fail; one slow light must never hold up the others or the state.
LIGHT_CALL_TIMEOUT: Final = 10.0  # seconds
IMAGE_MAX_BYTES: Final = 15 * 1024 * 1024  # refuse absurdly large artwork
IMAGE_CACHE_SIZE: Final = 256  # LRU entries (shared across entries)
EXTRACTION_THUMBNAIL_SIZE: Final = 160  # px, longest edge before analysis
QUANTIZE_COLORS: Final = 8  # clusters for dominant / vibrant extraction
ACHROMATIC_SATURATION: Final = 0.10  # below this the poster is "grey"
FUZZY_MATCH_CUTOFF: Final = 0.88  # difflib ratio for fuzzy title matches
EVALUATE_DEBOUNCE: Final = 0.75  # seconds; coalesces attribute-update storms
PALETTE_QUANTIZE_COLORS: Final = 16  # candidate clusters before ranking
PALETTE_KMEANS_ITERATIONS: Final = 3  # k-means refinement passes
PALETTE_MIN_DELTA_E: Final = 22.0  # CIE76 distance between palette colours
PALETTE_MIN_COVERAGE: Final = 0.03  # share of pixels a palette colour needs
# After the TV turns on, TV Mgmt turns on the TV's own lights. A zone that
# shares one of those lights waits this long before saving light states, so
# "on" is what gets restored rather than the "off" from a moment earlier.
TV_LIGHTS_SETTLE: Final = 3.0  # seconds
# After TV Mgmt stops a blocked app or show, zones of that TV restore their
# lights and don't colour again for this long, while the player catches up.
BLOCK_SUPPRESS: Final = 15.0  # seconds

# Metadata lookups
METADATA_TIMEOUT: Final = 10  # seconds per request
METADATA_CACHE_SIZE: Final = 512  # in-memory LRU entries
METADATA_CACHE_TTL: Final = 14 * 24 * 3600  # seconds, found results
METADATA_NEGATIVE_TTL: Final = 24 * 3600  # seconds, "not found" results
METADATA_SAVE_DELAY: Final = 30  # seconds, coalesce cache writes
METADATA_MIN_INTERVAL: Final = 0.3  # seconds between requests per provider
METADATA_DEFAULT_BACKOFF: Final = 60  # seconds after HTTP 429 without Retry-After
TMDB_API_URL: Final = "https://api.themoviedb.org/3"
TMDB_IMAGE_URL: Final = "https://image.tmdb.org/t/p"
TRAKT_API_URL: Final = "https://api.trakt.tv"

# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
STORAGE_VERSION: Final = 1
# Zones (per TV) and the home-wide settings.
STORAGE_KEY_CONFIG: Final = f"{DOMAIN}.light_sync"
# Title -> colour map, shared by every TV.
STORAGE_KEY_TITLES: Final = f"{DOMAIN}.light_sync.title_colors"
# Light states saved before syncing, suffixed with the zone's unique key.
STORAGE_KEY_SNAPSHOT: Final = f"{DOMAIN}.light_sync.snapshot"
# TMDB / Trakt lookup results, shared by every TV.
STORAGE_KEY_METADATA: Final = f"{DOMAIN}.light_sync.metadata_cache"

# Dispatcher signal fired when the title -> colour map changes.
SIGNAL_TITLE_COLORS_UPDATED: Final = f"{DOMAIN}_light_sync_title_colors_updated"
# Fired with (entry_id, zone_id) when a zone's state changes, for its entities.
SIGNAL_ZONE_UPDATED: Final = f"{DOMAIN}_light_sync_zone_updated"

# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #
SERVICE_SET_TITLE_COLOR: Final = "set_title_color"
SERVICE_REMOVE_TITLE_COLOR: Final = "remove_title_color"
SERVICE_LIST_TITLE_COLORS: Final = "list_title_colors"
SERVICE_RESYNC: Final = "resync_lights"
SERVICE_CLEAR_CACHE: Final = "clear_artwork_cache"

ATTR_TITLE: Final = "title"
ATTR_RGB_COLOR: Final = "rgb_color"
ATTR_ZONE: Final = "zone"

# Sensor attributes
ATTR_HS_COLOR: Final = "hs_color"
ATTR_SOURCE: Final = "source"
ATTR_MEDIA_TITLE: Final = "media_title"
ATTR_MEDIA_PLAYER: Final = "media_player"
ATTR_ENTITY_PICTURE: Final = "entity_picture"
ATTR_GENRE: Final = "genre"
ATTR_LAST_APPLIED: Final = "last_applied"
ATTR_LIGHTS: Final = "lights"
ATTR_PALETTE: Final = "palette"
ATTR_PALETTE_HEX: Final = "palette_hex"
ATTR_LIGHT_COLORS: Final = "light_colors"
ATTR_COLOR_MODE: Final = "color_mode"
ATTR_METADATA_PROVIDER: Final = "metadata_provider"
ATTR_SEARCH_TITLE: Final = "search_title"
ATTR_FAILED_LIGHTS: Final = "failed_lights"
ATTR_LIGHT_ERRORS: Final = "light_errors"
ATTR_LAST_ERROR: Final = "last_error"
ATTR_PENDING_ACTION: Final = "pending_action"

# media_player attributes a genre may be found in (non-standard, but used by
# several integrations such as Kodi, Jellyfin and some Plex forks).
GENRE_ATTRIBUTES: Final[tuple[str, ...]] = (
    "media_genre",
    "media_genres",
    "genre",
    "genres",
)
# Non-standard attributes some players use for the release year.
YEAR_ATTRIBUTES: Final[tuple[str, ...]] = ("media_year", "year")
# media_content_type values that mean "episode of a series".
SERIES_CONTENT_TYPES: Final[frozenset[str]] = frozenset({"episode", "tvshow", "season"})

# --------------------------------------------------------------------------- #
# Built-in genre / mood -> RGB table
# --------------------------------------------------------------------------- #
# Colours are picked to read well on bias lighting (saturated, not too dark).
GENRE_COLORS: Final[dict[str, tuple[int, int, int]]] = {
    "action": (255, 69, 0),  # orange-red explosions
    "adventure": (255, 140, 0),  # dark orange
    "animation": (255, 105, 180),  # hot pink
    "anime": (255, 64, 160),
    "biography": (210, 170, 110),  # sepia
    "comedy": (255, 215, 0),  # gold
    "crime": (178, 34, 34),  # firebrick
    "documentary": (46, 139, 87),  # sea green
    "drama": (138, 43, 226),  # blue violet
    "family": (255, 182, 72),  # warm apricot
    "fantasy": (148, 0, 211),  # dark violet
    "history": (205, 133, 63),  # peru
    "holiday": (220, 20, 60),  # crimson
    "horror": (139, 0, 0),  # dark red
    "kids": (0, 191, 255),  # deep sky blue
    "music": (255, 0, 255),  # magenta
    "musical": (255, 20, 147),  # deep pink
    "mystery": (72, 61, 139),  # dark slate blue
    "news": (30, 144, 255),  # dodger blue
    "reality": (255, 99, 71),  # tomato
    "romance": (255, 20, 147),  # deep pink
    "science fiction": (0, 191, 255),  # deep sky blue
    "sport": (50, 205, 50),  # lime green
    "superhero": (220, 20, 60),  # crimson
    "thriller": (128, 0, 32),  # burgundy
    "war": (107, 142, 35),  # olive drab
    "western": (210, 105, 30),  # chocolate / desert
}

# Normalise common genre spellings onto GENRE_COLORS keys.
GENRE_ALIASES: Final[dict[str, str]] = {
    "sci-fi": "science fiction",
    "sci fi": "science fiction",
    "scifi": "science fiction",
    "sf": "science fiction",
    "science-fiction": "science fiction",
    "sci-fi & fantasy": "science fiction",
    "action & adventure": "action",
    "animated": "animation",
    "cartoon": "animation",
    "children": "kids",
    "kid": "kids",
    "christmas": "holiday",
    "suspense": "thriller",
    "sports": "sport",
    "musicals": "musical",
    "romantic comedy": "romance",
    "rom-com": "romance",
    "historical": "history",
    "true crime": "crime",
    "docuseries": "documentary",
    "war & politics": "war",
    # Trakt genre slugs
    "sporting-events": "sport",
    "sporting events": "sport",
    "kids & family": "kids",
}

# TMDB genre IDs (stable, documented at /genre/movie/list and /genre/tv/list)
# so a search result can be mapped without an extra request.
TMDB_GENRES: Final[dict[int, str]] = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Science Fiction",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
    10759: "Action & Adventure",
    10762: "Kids",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
}

# Keyword -> genre heuristics used when no genre attribute exists. Matched as
# whole words against the normalised title (and series title).
TITLE_KEYWORD_GENRES: Final[dict[str, str]] = {
    "christmas": "holiday",
    "xmas": "holiday",
    "santa": "holiday",
    "grinch": "holiday",
    "elf": "holiday",
    "halloween": "horror",
    "haunting": "horror",
    "haunted": "horror",
    "conjuring": "horror",
    "exorcist": "horror",
    "zombie": "horror",
    "dead": "horror",
    "evil": "horror",
    "nightmare": "horror",
    "scream": "horror",
    "vampire": "horror",
    "star": "science fiction",
    "space": "science fiction",
    "galaxy": "science fiction",
    "alien": "science fiction",
    "aliens": "science fiction",
    "dune": "science fiction",
    "trek": "science fiction",
    "matrix": "science fiction",
    "robot": "science fiction",
    "interstellar": "science fiction",
    "dragon": "fantasy",
    "dragons": "fantasy",
    "wizard": "fantasy",
    "rings": "fantasy",
    "potter": "fantasy",
    "throne": "fantasy",
    "thrones": "fantasy",
    "love": "romance",
    "wedding": "romance",
    "valentine": "romance",
    "war": "war",
    "soldier": "war",
    "battle": "war",
    "murder": "crime",
    "heist": "crime",
    "detective": "mystery",
    "mystery": "mystery",
    "batman": "superhero",
    "superman": "superhero",
    "avengers": "superhero",
    "marvel": "superhero",
    "spider": "superhero",
    "frozen": "kids",
    "toy": "kids",
    "planet": "documentary",
    "earth": "documentary",
    "ocean": "documentary",
    "football": "sport",
    "soccer": "sport",
    "nba": "sport",
    "nfl": "sport",
    "news": "news",
    "western": "western",
    "cowboy": "western",
}

# Light attributes captured in a snapshot so a light can be restored exactly.
SNAPSHOT_ATTRIBUTES: Final[tuple[str, ...]] = (
    "brightness",
    "color_mode",
    "hs_color",
    "rgb_color",
    "rgbw_color",
    "rgbww_color",
    "xy_color",
    "color_temp_kelvin",
    "effect",
    "supported_color_modes",
    "supported_features",
)
