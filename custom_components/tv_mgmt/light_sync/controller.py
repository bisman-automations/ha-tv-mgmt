"""Sync engine for one light sync zone of a TV.

One :class:`LightZone` exists per zone. A zone is a set of lights that
follows what's playing on one TV: the TV's own media player and its linked
Apple TV by default, plus any extra players (for example the Music Assistant
player that reports what the Apple TV is playing). It is a push-only
:class:`DataUpdateCoordinator` (no polling): the zone listens to state
changes of its media players, works out a colour for whatever is playing,
drives its lights, and pushes a :class:`SyncState` snapshot to its entities
via ``async_set_updated_data``.

This is the engine of the Movie Light Sync integration, ported in full. What
is different inside TV Mgmt:

* Settings come from TV Mgmt's light sync storage (the panel's Lights tab),
  not from a config entry, and are applied live by :meth:`async_apply_options`.
* The TMDB / Trakt lookups and the title colours are shared by every zone.
* TV Mgmt owns the TV's own lights (``tv_lights``) and its rules come first.
  The :class:`ZoneLink` the zone is given tells it whether it may colour
  right now, and TV Mgmt calls :meth:`async_handle_tv_off` and
  :meth:`async_handle_media_blocked`:

  - TV turned off: TV Mgmt turns the TV lights off, so the zone drops their
    saved state (nothing to restore) and hands its other lights back now.
  - TV turned on: the zone waits for the TV lights to come on before saving
    their state, so "on" is what gets restored later.
  - TV blocked (time's up, quiet window, blocked by a parent): no colouring.
  - TV Mgmt stopped a blocked app or show: the zone restores its lights.

Lifecycle (per zone)::

    LightSync.async_setup / zone added in the panel
      -> zone.async_initialize()   # load persisted light snapshot
      -> entities added            # switch, selects, numbers, sensors...
      -> zone.async_start()        # subscribe + first evaluation
    TV unloaded / zone deleted
      -> zone.async_shutdown()     # unsubscribe, cancel timers

State machine::

    any player PLAYING (+ title/content id, + media type filter),
    and TV Mgmt allows colouring (TV on, not blocked)
        -> cancel pending idle action
        -> if (player, title, content id, picture) changed: resolve colour
           (title map / artwork / genre incl. optional TMDB+Trakt lookup /
           default), snapshot lights (first time only), apply one colour or
           distribute the artwork palette across the ordered lights
    no player playing
        -> schedule pause/stop behaviour after ``restore_delay`` seconds
           (debounce: resuming within the delay cancels it, no flicker)

Lights are driven one service call per light, concurrently and with a
timeout, so a single offline or slow light never fails (or delays) the
others: partial success is success, failures are recorded per light in
``SyncState.failed_lights`` and retried on the next change / resync.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
import time
from typing import Any

import aiohttp
from yarl import URL

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    DOMAIN as LIGHT_DOMAIN,
    EFFECT_OFF,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_SERIES_TITLE,
    ATTR_MEDIA_TITLE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HassJob,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .color import (
    RGB,
    AdjustParams,
    ColorExtractionError,
    ExtractionParams,
    ImageAnalysis,
    adjust_color,
    analyze_image,
    clamp_rgb,
    clean_title,
    distribute_palette,
    iter_genres,
    match_genre_color,
    normalize_title,
)
from .const import (
    BLOCK_SUPPRESS,
    CONF_BRIGHTNESS_PCT,
    CONF_COLOR_MODE,
    CONF_COLOR_SOURCES,
    CONF_DARK_THRESHOLD,
    CONF_DEFAULT_COLOR,
    CONF_ENABLED,
    CONF_EXPAND_GROUPS,
    CONF_EXTRA_PLAYERS,
    CONF_EXTRACTION_METHOD,
    CONF_FUZZY_TITLE_MATCH,
    CONF_LIGHT_THRESHOLD,
    CONF_LIGHTS,
    CONF_MEDIA_TYPES,
    CONF_METADATA_ARTWORK,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_SATURATION,
    CONF_NAME,
    CONF_PALETTE_SIZE,
    CONF_PALETTE_STRATEGY,
    CONF_PAUSE_BEHAVIOR,
    CONF_PERSIST_SNAPSHOT,
    CONF_REQUIRE_TV_ON,
    CONF_RESTORE_DELAY,
    CONF_SATURATION_BOOST,
    CONF_SKIP_ACHROMATIC,
    CONF_STOP_BEHAVIOR,
    CONF_TRANSITION,
    CONF_UNSUPPORTED_LIGHTS,
    CONF_USE_APPLE_TV,
    CONF_USE_TV_PLAYER,
    CONF_ZONE_ID,
    DOMAIN,
    EVALUATE_DEBOUNCE,
    GENRE_ATTRIBUTES,
    IMAGE_FETCH_TIMEOUT,
    IMAGE_MAX_BYTES,
    LIGHT_CALL_TIMEOUT,
    LOGGER,
    MAX_PALETTE_SIZE,
    MIN_PALETTE_SIZE,
    SIGNAL_TITLE_COLORS_UPDATED,
    SNAPSHOT_ATTRIBUTES,
    STORAGE_KEY_SNAPSHOT,
    STORAGE_VERSION,
    TRACKING_OPTIONS,
    YEAR_ATTRIBUTES,
    ColorSource,
    ExtractionMethod,
    IdleBehavior,
    PaletteStrategy,
    SyncColorMode,
    UnsupportedLightBehavior,
)
from .metadata import MetadataQuery, MetadataResult, MetadataService, build_query
from .storage import DATA_LIGHT_SYNC, LightSyncData, merge_zone

# Colour modes that can render an arbitrary RGB colour.
COLOR_CAPABLE_MODES: frozenset[str] = frozenset(
    {ColorMode.HS, ColorMode.XY, ColorMode.RGB, ColorMode.RGBW, ColorMode.RGBWW}
)
# Colour modes that at least support dimming.
DIMMABLE_MODES: frozenset[str] = frozenset(
    {ColorMode.BRIGHTNESS, ColorMode.COLOR_TEMP, ColorMode.WHITE} | COLOR_CAPABLE_MODES
)
# color_mode -> attribute that must be passed to light.turn_on on restore.
RESTORE_COLOR_ATTR: dict[str, str] = {
    ColorMode.HS: ATTR_HS_COLOR,
    ColorMode.XY: ATTR_XY_COLOR,
    ColorMode.RGB: ATTR_RGB_COLOR,
    ColorMode.RGBW: ATTR_RGBW_COLOR,
    ColorMode.RGBWW: ATTR_RGBWW_COLOR,
    ColorMode.COLOR_TEMP: ATTR_COLOR_TEMP_KELVIN,
}
# media_player attributes whose change can alter the chosen colour.
RELEVANT_PLAYER_ATTRS: tuple[str, ...] = (
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_SERIES_TITLE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    "entity_picture",
    *GENRE_ATTRIBUTES,
)
UNAVAILABLE_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN)
_MEDIA_PLAYER_PROXY_PATH = "/api/media_player_proxy/"


class ZoneLink:
    """What a zone needs to know about its TV (implemented by ``LightSync``).

    Kept as a small interface so the engine doesn't depend on TV Mgmt's
    manager, and so tests can drive it directly.
    """

    tv_entity_id: str | None = None
    apple_tv_entity_id: str | None = None

    def tv_lights(self) -> list[str]:
        """The TV's own lights, which TV Mgmt turns on and off with the TV."""
        return []

    def held_off_lights(self) -> list[str]:
        """TV lights turned off by hand: leave them off (don't colour them)."""
        return []

    def tv_is_on(self) -> bool | None:
        """Whether the TV is on (``None`` when it can't tell)."""
        return None

    def blocked(self) -> bool:
        """Whether TV Mgmt is keeping the TV off right now."""
        return False

    def tv_lights_settle_remaining(self) -> float:
        """Seconds until the TV lights have had time to come on (0 = settled)."""
        return 0.0

    def zone_changed(self, zone: LightZone) -> None:
        """A zone's state changed (refresh the panel)."""

    def save_changes(self, zone: LightZone, changes: Mapping[str, Any]) -> None:
        """Remember settings changed from a zone's own entities."""


@dataclass
class SyncState:
    """Snapshot of what a sync group is doing; consumed by the entities."""

    enabled: bool = True
    syncing: bool = False
    # Why the lights aren't following the TV right now, when something is
    # playing: "tv_off", "blocked" (TV Mgmt is keeping the TV off) or
    # "stopped" (TV Mgmt just stopped a blocked app or show).
    held_reason: str | None = None
    rgb_color: RGB | None = None
    source: ColorSource = ColorSource.NONE
    media_player: str | None = None
    media_title: str | None = None
    entity_picture: str | None = None
    genre: str | None = None
    matched_title: str | None = None
    lights: list[str] = field(default_factory=list)
    last_applied: datetime | None = None
    last_error: str | None = None
    pending_action: str | None = None
    color_mode: SyncColorMode = SyncColorMode.SINGLE
    palette: list[RGB] = field(default_factory=list)
    light_colors: dict[str, RGB] = field(default_factory=dict)
    metadata_provider: str | None = None
    save_title: str | None = None
    # Title used for title-mapping fallbacks and metadata searches when it
    # differs from the displayed one ("X: 2-Movie Collection" -> "X").
    search_title: str | None = None
    # entity_id -> reason, for lights the last apply/restore could not set.
    failed_lights: dict[str, str] = field(default_factory=dict)


class SyncOutcome(StrEnum):
    """Result of the last attempt to apply a colour for playing media."""

    OK = "ok"
    PARTIAL = "partial"  # some lights failed, at least one was set
    FAILED = "failed"  # no light could be set (or resolution failed)


@dataclass(slots=True)
class ApplyResult:
    """What an apply actually achieved."""

    applied: list[str]  # lights that accepted the call
    light_colors: dict[str, RGB]  # colour per applied colour light
    failed: dict[str, str]  # entity_id -> reason


class LightSyncError(HomeAssistantError):
    """No light could be set; ``failed`` names each light and its reason."""

    def __init__(self, failed: Mapping[str, str]) -> None:
        """Create the error from the per-light failures."""
        self.failed = dict(failed)
        super().__init__(format_light_errors(self.failed) or "No lights to control")


def format_light_errors(failed: Mapping[str, str]) -> str:
    """``"light.a: offline; light.b: unavailable"`` (empty when none failed)."""
    return "; ".join(f"{entity_id}: {reason}" for entity_id, reason in failed.items())


@dataclass(frozen=True, slots=True)
class ResolvedColor:
    """Result of colour resolution.

    ``palette`` is only filled from artwork in palette mode; title mappings,
    genres and the default colour are single colours.
    """

    rgb: RGB
    source: ColorSource
    genre: str | None = None
    matched_title: str | None = None
    palette: tuple[RGB, ...] = ()
    metadata_provider: str | None = None


class LightZone(DataUpdateCoordinator[SyncState]):
    """Watches a TV's media players and drives the lights of one zone."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        link: ZoneLink,
        zone: Mapping[str, Any],
    ) -> None:
        """Create the zone from its saved settings (see ``ZONE_DEFAULTS``)."""
        self.options: dict[str, Any] = merge_zone(dict(zone))
        self.zone_id: str = str(self.options["id"])
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} light sync ({entry.title} {self.zone_id})",
            update_interval=None,  # local push - never polls
            always_update=True,
        )
        self._link = link
        self._domain_data: LightSyncData = hass.data[DATA_LIGHT_SYNC]
        self._state = SyncState(enabled=bool(self.options[CONF_ENABLED]))
        self._state.color_mode = self.color_mode
        self.data = self._state

        # Light snapshot: entity_id -> {"state": str, "attributes": {...}}
        self._snapshot: dict[str, dict[str, Any]] = {}
        self._snapshot_store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_SNAPSHOT}.{entry.entry_id}_{self.zone_id}",
        )

        self._applied_signature: tuple[Any, ...] | None = None
        self._idle_unsub: CALLBACK_TYPE | None = None
        self._idle_behavior: IdleBehavior | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unsub_players: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()
        self._warned_unsupported: set[str] = set()
        # Lights whose failure was already logged as a warning this session.
        self._warned_failed: set[str] = set()
        self._last_outcome: SyncOutcome | None = None
        self._started = False
        self._shut_down = False
        self._last_context: Context | None = None
        # After TV Mgmt stops a blocked app or show: no colouring until then
        # (monotonic seconds), while the player catches up.
        self._suppress_until = 0.0
        # (query, result) of the last metadata lookup - reused within one
        # evaluation by the genre and artwork sources.
        self._last_metadata: (
            tuple[MetadataQuery, bool, MetadataResult | None] | None
        ) = None
        self._debouncer: Debouncer = Debouncer(
            hass,
            LOGGER,
            cooldown=EVALUATE_DEBOUNCE,
            immediate=False,
            function=self._async_evaluate,
            # Evaluations can wait on artwork, metadata and (cloud) lights;
            # never let HA start-up / setup wait on them.
            background=True,
        )

    # ------------------------------------------------------------------ #
    # Identity and option helpers
    # ------------------------------------------------------------------ #
    @property
    def zone_name(self) -> str:
        """The zone's name as set in the panel (never empty)."""
        return str(self.options[CONF_NAME] or "").strip() or "Lights"

    @property
    def label(self) -> str:
        """``"<TV> · <zone>"``, used in logs."""
        return f"{self.config_entry.title} · {self.zone_name}"

    @property
    def unique_key(self) -> str:
        """Prefix of this zone's entity unique IDs and device identifier."""
        return f"{self.config_entry.entry_id}_zone_{self.zone_id}"

    @property
    def configured_lights(self) -> list[str]:
        """The zone's lights in the order set in the panel (groups unexpanded)."""
        return [e for e in self.options[CONF_LIGHTS] or [] if e]

    @property
    def media_players(self) -> list[str]:
        """Players watched, in priority order: extra players, Apple TV, TV.

        Extra players come first because they're added on purpose, usually
        because they report playback better (an Apple TV can report "off"
        while its Music Assistant player reports what's playing).
        """
        players: list[str] = list(self.options[CONF_EXTRA_PLAYERS] or [])
        if self.options[CONF_USE_APPLE_TV] and self._link.apple_tv_entity_id:
            players.append(self._link.apple_tv_entity_id)
        if self.options[CONF_USE_TV_PLAYER] and self._link.tv_entity_id:
            players.append(self._link.tv_entity_id)
        return [p for p in dict.fromkeys(players) if p]

    @property
    def metadata(self) -> MetadataService | None:
        """The home-wide TMDB / Trakt lookups (``None`` when no key is set)."""
        return self._domain_data.metadata_service

    @property
    def extraction_params(self) -> ExtractionParams:
        """Extraction parameters derived from options (hashable)."""
        return ExtractionParams(
            method=ExtractionMethod(self.options[CONF_EXTRACTION_METHOD]),
            dark_threshold=int(self.options[CONF_DARK_THRESHOLD]),
            light_threshold=int(self.options[CONF_LIGHT_THRESHOLD]),
            skip_achromatic=bool(self.options[CONF_SKIP_ACHROMATIC]),
        )

    @property
    def adjust_params(self) -> AdjustParams:
        """Colour adjustment parameters derived from options."""
        return AdjustParams(
            min_saturation=float(self.options[CONF_MIN_SATURATION]) / 100,
            saturation_boost=float(self.options[CONF_SATURATION_BOOST]),
            min_brightness=float(self.options[CONF_MIN_BRIGHTNESS]) / 100,
        )

    @property
    def color_mode(self) -> SyncColorMode:
        """Single colour or palette mode."""
        try:
            return SyncColorMode(self.options[CONF_COLOR_MODE])
        except ValueError:
            return SyncColorMode.SINGLE

    @property
    def palette_strategy(self) -> PaletteStrategy:
        """How palette colours are distributed over the lights."""
        try:
            return PaletteStrategy(self.options[CONF_PALETTE_STRATEGY])
        except ValueError:
            return PaletteStrategy.SPREAD

    @property
    def palette_size(self) -> int:
        """Palette size to extract (0 in single-colour mode)."""
        if self.color_mode is not SyncColorMode.PALETTE:
            return 0
        size = int(self.options[CONF_PALETTE_SIZE])
        return max(MIN_PALETTE_SIZE, min(MAX_PALETTE_SIZE, size))

    @property
    def default_color(self) -> RGB:
        """Configured fallback colour."""
        return clamp_rgb(self.options[CONF_DEFAULT_COLOR])

    @property
    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Read-only view of the current light snapshot (diagnostics)."""
        return dict(self._snapshot)

    @callback
    def async_set_updated_data(self, data: SyncState) -> None:
        """Push the state to the zone's entities, and tell the panel."""
        super().async_set_updated_data(data)
        self._link.zone_changed(self)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def _async_update_data(self) -> SyncState:
        """Return current state (push coordinator; nothing to poll)."""
        return self._state

    async def async_initialize(self) -> None:
        """Load a persisted snapshot (survives restarts and TV reloads)."""
        data = await self._snapshot_store.async_load() or {}
        snapshot = data.get("lights")
        if isinstance(snapshot, dict) and snapshot:
            # Keep it in memory even if persistence was just switched off, so
            # a settings change mid-movie still restores the original state.
            self._snapshot = snapshot
            LOGGER.debug(
                "%s: restored persisted snapshot for %s",
                self.label,
                ", ".join(snapshot),
            )
        if not self.options[CONF_PERSIST_SNAPSHOT]:
            await self._snapshot_store.async_remove()

    @callback
    def _track_players(self) -> None:
        """(Re)subscribe to the zone's media players."""
        if self._unsub_players is not None:
            self._unsub_players()
            self._unsub_players = None
        if players := self.media_players:
            self._unsub_players = async_track_state_change_event(
                self.hass, players, self._async_handle_player_event
            )

    @callback
    def async_start(self) -> None:
        """Subscribe to media players and run a first evaluation."""
        self._track_players()
        self._unsubs.append(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_TITLE_COLORS_UPDATED,
                self._async_handle_titles_updated,
            )
        )
        self._started = True

        @callback
        def _initial(_hass: HomeAssistant) -> None:
            # A background task: TV setup never waits for artwork downloads,
            # metadata lookups or (cloud) light calls, and HA cancels it if
            # the TV is unloaded first.
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_evaluate(),
                f"{DOMAIN} light sync first evaluation ({self.label})",
            )

        # Wait for HA to finish starting so the players have real states.
        self._unsubs.append(async_at_started(self.hass, _initial))

    async def async_shutdown(self) -> None:
        """Tear down listeners/timers (TV unloaded or zone deleted).

        Safe to call more than once: Home Assistant also calls it when the
        TV's config entry unloads.
        """
        if self._shut_down:
            return
        self._shut_down = True
        self._started = False
        self._cancel_idle()
        self._debouncer.async_cancel()
        if self._unsub_players is not None:
            self._unsub_players()
            self._unsub_players = None
        while self._unsubs:
            self._unsubs.pop()()
        if self._snapshot and not self.options[CONF_PERSIST_SNAPSHOT]:
            # Without persistence the snapshot would be lost: restore now.
            await self._async_restore(clear=True)
        await super().async_shutdown()

    async def async_remove(self) -> None:
        """The zone was deleted: give the lights back and forget it."""
        async with self._lock:
            if self._snapshot:
                await self._async_restore(clear=True)
        await self.async_shutdown()
        await self._snapshot_store.async_remove()

    # ------------------------------------------------------------------ #
    # Public API (switch, services, TV Mgmt)
    # ------------------------------------------------------------------ #
    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable or disable syncing.

        Disabling applies the configured *stop* behaviour so the lights are
        handed back to the user (e.g. restored).
        """
        if self._state.enabled == enabled:
            return
        self._state.enabled = enabled
        self.options[CONF_ENABLED] = enabled
        self._link.save_changes(self, {CONF_ENABLED: enabled})
        LOGGER.debug("%s: sync %s", self.label, "enabled" if enabled else "disabled")
        if not self._started:
            self.async_set_updated_data(self._state)
            return
        if enabled:
            self._applied_signature = None
            self._debouncer.async_schedule_call()
        else:
            self._cancel_idle()
            async with self._lock:
                if self._state.syncing or self._snapshot:
                    await self._async_run_idle_behavior(
                        IdleBehavior(self.options[CONF_STOP_BEHAVIOR]),
                        session_end=True,
                    )
        self.async_set_updated_data(self._state)

    @property
    def last_outcome(self) -> SyncOutcome | None:
        """Outcome of the last colour apply (``None`` before the first)."""
        return self._last_outcome

    async def async_resync(self) -> SyncOutcome | None:
        """Re-evaluate now, bypassing the change signature, and re-apply.

        Returns the outcome of applying a colour, or ``None`` when nothing is
        playing. Entities are always updated, whatever happened.
        """
        self._applied_signature = None
        self._last_metadata = None
        self._last_outcome = None
        try:
            await self._async_evaluate()
        finally:
            self.async_set_updated_data(self._state)
        return self._last_outcome

    @callback
    def async_schedule_evaluate(self) -> None:
        """Look again soon (TV Mgmt's view of the TV changed)."""
        if self._started:
            self._debouncer.async_schedule_call()

    @callback
    def async_schedule_reapply(self) -> None:
        """Colour the lights again soon, even if the title hasn't changed."""
        self._applied_signature = None
        self.async_schedule_evaluate()

    async def async_handle_tv_off(self) -> None:
        """The TV turned off.

        TV Mgmt turns the TV's own lights off, so their saved state is
        dropped (restoring them would turn them back on), and any pending
        pause/stop action is replaced by the stop behaviour, run now for the
        zone's other lights.
        """
        if not self._started:
            return
        self._cancel_idle()
        self._debouncer.async_cancel()
        tv_lights = set(self._expand_lights(self._link.tv_lights()))
        async with self._lock:
            dropped = [e for e in self._snapshot if e in tv_lights]
            for entity_id in dropped:
                self._snapshot.pop(entity_id)
            if dropped:
                LOGGER.debug(
                    "%s: TV off, not restoring TV lights %s", self.label, dropped
                )
            if self.options[CONF_REQUIRE_TV_ON] and (
                self._state.syncing or self._snapshot
            ):
                await self._async_run_idle_behavior(
                    IdleBehavior(self.options[CONF_STOP_BEHAVIOR]),
                    session_end=True,
                    exclude=tv_lights,
                )
            elif dropped:
                await self._async_save_snapshot()
            self._state.held_reason = self._held_reason()
        self.async_set_updated_data(self._state)

    def _held_off(self) -> set[str]:
        return set(self._expand_lights(self._link.held_off_lights()))

    async def async_release_tv_lights(self) -> None:
        """TV Mgmt is turning the TV lights off by hand (the panel or the button).

        Their saved state is dropped, so ending the movie doesn't turn them
        back on, and the zone doesn't colour them while they're held off
        (``held_off_lights``). The zone's other lights carry on.
        """
        if not self._started:
            return
        held = self._held_off()
        async with self._lock:
            dropped = [e for e in self._snapshot if e in held]
            for entity_id in dropped:
                self._snapshot.pop(entity_id)
            if dropped:
                LOGGER.debug(
                    "%s: TV lights turned off, not restoring %s", self.label, dropped
                )
                await self._async_save_snapshot()
        self.async_set_updated_data(self._state)

    async def async_handle_media_blocked(self) -> None:
        """TV Mgmt stopped a blocked app or show: restore the lights now.

        Whatever the stop behaviour is, the lights go back to how they were,
        and the zone doesn't colour again for a short while, so the player
        reporting the stopped title a moment longer doesn't recolour them.
        """
        if not self._started:
            return
        self._suppress_until = time.monotonic() + BLOCK_SUPPRESS
        self._cancel_idle()
        self._debouncer.async_cancel()
        async with self._lock:
            if self._state.syncing or self._snapshot:
                await self._async_run_idle_behavior(
                    IdleBehavior.RESTORE, session_end=True
                )
            self._state.held_reason = self._held_reason()
        self.async_set_updated_data(self._state)
        # Look again once the hold is over (the app may have been allowed).
        self._unsubs.append(
            async_call_later(
                self.hass,
                BLOCK_SUPPRESS + 0.1,
                HassJob(self._end_suppress, cancel_on_shutdown=True),
            )
        )

    @callback
    def _end_suppress(self, _now: datetime) -> None:
        self.async_schedule_evaluate()

    # ------------------------------------------------------------------ #
    # Live options (panel and dashboard controls)
    # ------------------------------------------------------------------ #
    async def async_update_options(self, changes: Mapping[str, Any]) -> None:
        """Change settings from the zone's own selects / numbers.

        Saved to TV Mgmt's light sync storage and applied straight away.
        """
        new = {**self.options, **changes}
        if new == self.options:
            return
        self._link.save_changes(self, dict(changes))
        self.async_apply_options(new)

    @callback
    def async_apply_options(self, zone: Mapping[str, Any]) -> set[str]:
        """Apply new zone settings live (never reloads); return changed keys.

        Changing the lights or players re-subscribes the state listeners and,
        while syncing, hands lights that left the zone back first. Turning
        syncing on or off is up to the caller (:meth:`async_set_enabled`).
        """
        new = merge_zone(dict(zone))
        new["id"] = self.zone_id
        changed = {key for key, value in new.items() if self.options.get(key) != value}
        if not changed:
            return changed
        LOGGER.debug("%s: applying settings live: %s", self.label, sorted(changed))
        old_lights = set(self._expand_lights(self.configured_lights))
        self.options = new
        self._state.color_mode = self.color_mode
        if changed & TRACKING_OPTIONS and self._started:
            self._track_players()
            gone = old_lights - set(self._resolve_lights())
            if gone:
                self.config_entry.async_create_task(
                    self.hass,
                    self._async_release(gone),
                    f"{DOMAIN} light sync release lights ({self.label})",
                )
        if CONF_PERSIST_SNAPSHOT in changed:
            self.config_entry.async_create_task(
                self.hass,
                self._async_apply_persistence(),
                f"{DOMAIN} light sync snapshot persistence ({self.label})",
            )
        if self._state.enabled and self._started:
            # Re-resolve and re-apply with the new settings.
            self._applied_signature = None
            self._debouncer.async_schedule_call()
        self.async_set_updated_data(self._state)
        return changed

    @callback
    def async_metadata_changed(self) -> None:
        """The home-wide TMDB / Trakt settings changed: look titles up again."""
        self._last_metadata = None
        if self._state.syncing and self._state.enabled:
            self._applied_signature = None
            self._debouncer.async_schedule_call()

    async def _async_release(self, lights: set[str]) -> None:
        """Restore lights that were removed from the zone while syncing."""
        async with self._lock:
            part = {
                e: self._snapshot.pop(e) for e in list(self._snapshot) if e in lights
            }
            if not part:
                return
            failed = await async_restore_snapshot(
                self.hass, part, float(self.options[CONF_TRANSITION])
            )
            self._report_light_failures(failed, "restore")
            await self._async_save_snapshot()

    async def _async_apply_persistence(self) -> None:
        """Save or drop the stored snapshot after the option was toggled."""
        if not self.options[CONF_PERSIST_SNAPSHOT]:
            await self._snapshot_store.async_remove()
        elif self._snapshot:
            await self._snapshot_store.async_save({"lights": self._snapshot})

    async def _async_save_snapshot(self) -> None:
        """Write the snapshot (or remove the file when it's empty)."""
        if not self.options[CONF_PERSIST_SNAPSHOT]:
            return
        if self._snapshot:
            await self._snapshot_store.async_save({"lights": self._snapshot})
        else:
            await self._snapshot_store.async_remove()

    async def async_save_current_title_color(self) -> str:
        """Store the colour currently shown under the playing title.

        Episodes are stored under the series title so the colour applies to
        every episode. Returns the stored title key.

        :raises ServiceValidationError: nothing is being synced right now.
        """
        state = self._state
        if not state.syncing or state.rgb_color is None or not state.save_title:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="nothing_to_save",
            )
        key = await self._domain_data.titles.async_set(
            state.save_title, state.rgb_color
        )
        LOGGER.info(
            "%s: saved %s for title %r",
            self.label,
            state.rgb_color,
            state.save_title,
        )
        return key

    # ------------------------------------------------------------------ #
    # Event handling
    # ------------------------------------------------------------------ #
    @callback
    def _async_handle_player_event(self, event: Event[EventStateChangedData]) -> None:
        """Schedule an evaluation when a relevant player change happens."""
        if not self._state.enabled:
            return
        old = event.data["old_state"]
        new = event.data["new_state"]
        # Ignore pure media_position / volume updates.
        if (
            old is not None
            and new is not None
            and old.state == new.state
            and all(
                old.attributes.get(attr) == new.attributes.get(attr)
                for attr in RELEVANT_PLAYER_ATTRS
            )
        ):
            return
        self._debouncer.async_schedule_call()

    @callback
    def _async_handle_titles_updated(self) -> None:
        """Re-apply when the user edits the title map while we sync."""
        if self._state.syncing:
            self._applied_signature = None
            self._debouncer.async_schedule_call()

    # ------------------------------------------------------------------ #
    # Core evaluation
    # ------------------------------------------------------------------ #
    def _type_allowed(self, state: State) -> bool:
        allowed: list[str] = self.options[CONF_MEDIA_TYPES] or []
        if not allowed:
            return True
        ctype = state.attributes.get(ATTR_MEDIA_CONTENT_TYPE)
        # Unknown type -> allowed (many TV apps never report one).
        return ctype is None or str(ctype) in allowed

    def _held_reason(self) -> str | None:
        """Why TV Mgmt doesn't let the zone colour right now (``None``: it may).

        TV Mgmt's rules come first: no colouring while the TV is off (when
        the zone asks for that) or while TV Mgmt is keeping it off, and not
        for a short while after it stopped a blocked app or show.
        """
        if self._link.blocked():
            return "blocked"
        if self.options[CONF_REQUIRE_TV_ON] and self._link.tv_is_on() is False:
            return "tv_off"
        if time.monotonic() < self._suppress_until:
            return "stopped"
        return None

    def _find_active_player(self) -> State | None:
        """Return the first configured player that is playing something.

        ``None`` too while TV Mgmt holds the lights (see :meth:`_held_reason`).
        """
        if self._held_reason() is not None:
            return None
        return self._find_playing()

    def _find_playing(self) -> State | None:
        """The first player playing something, ignoring TV Mgmt's hold."""
        for entity_id in self.media_players:
            state = self.hass.states.get(entity_id)
            if state is None or state.state != MediaPlayerState.PLAYING:
                continue
            attrs = state.attributes
            if not (attrs.get(ATTR_MEDIA_TITLE) or attrs.get(ATTR_MEDIA_CONTENT_ID)):
                continue
            if not self._type_allowed(state):
                LOGGER.debug(
                    "%s: %s media type %s filtered out",
                    self.label,
                    entity_id,
                    attrs.get(ATTR_MEDIA_CONTENT_TYPE),
                )
                continue
            return state
        return None

    def _any_paused(self) -> bool:
        if self._held_reason() is not None:
            return False  # held by TV Mgmt: treated as stopped
        return any(
            (s := self.hass.states.get(e)) is not None
            and s.state == MediaPlayerState.PAUSED
            for e in self.media_players
        )

    async def _async_evaluate(self) -> None:
        """Decide what the lights should do right now (never raises)."""
        async with self._lock:
            try:
                await self._async_evaluate_locked()
            except Exception as err:  # noqa: BLE001 - must never leave state stale
                LOGGER.exception("%s: evaluation failed", self.label)
                self._state.last_error = f"Unexpected error: {err!r}"
                self.async_set_updated_data(self._state)

    async def _async_evaluate_locked(self) -> None:
        """Body of :meth:`_async_evaluate`; caller holds ``self._lock``."""
        if not self._state.enabled or not self._started:
            return
        held = self._held_reason()
        if held != self._state.held_reason:
            self._state.held_reason = held
            self.async_set_updated_data(self._state)
        active = self._find_active_player()
        if active is not None:
            self._cancel_idle()
            attrs = active.attributes
            signature = (
                active.entity_id,
                attrs.get(ATTR_MEDIA_TITLE),
                attrs.get(ATTR_MEDIA_SERIES_TITLE),
                attrs.get(ATTR_MEDIA_CONTENT_ID),
                attrs.get("entity_picture"),
            )
            if signature == self._applied_signature and self._state.syncing:
                return
            self._last_metadata = None
            await self._async_sync_to(active, signature)
            return

        # Nothing playing.
        if not self._state.syncing and not self._snapshot:
            return
        paused = self._any_paused()
        behavior = IdleBehavior(
            self.options[CONF_PAUSE_BEHAVIOR if paused else CONF_STOP_BEHAVIOR]
        )
        self._schedule_idle(behavior, session_end=not paused)

    async def _async_sync_to(self, player: State, signature: tuple[Any, ...]) -> None:
        """Resolve a colour for ``player`` and apply it to the lights.

        Whatever happens (partial failure, total failure, unexpected
        exception) the state is updated and pushed to the entities.
        """
        attrs = player.attributes
        title = attrs.get(ATTR_MEDIA_TITLE) or attrs.get(ATTR_MEDIA_CONTENT_ID)
        state = self._state
        state.media_player = player.entity_id
        state.media_title = str(title) if title else None
        state.entity_picture = attrs.get("entity_picture")
        state.save_title = self._save_title(attrs)
        state.search_title = self._search_title(attrs)
        state.color_mode = self.color_mode

        resolved: ResolvedColor | None = None
        result: ApplyResult | None = None
        error: str | None = None
        failed: dict[str, str] = {}
        try:
            resolved = await self._async_resolve_color(player)
            held = self._held_off()
            lights = [e for e in self._resolve_lights() if e not in held]
            await self._async_take_snapshot(lights)
            result = await self._async_apply_color(resolved, lights)
        except LightSyncError as err:
            error, failed = str(err), err.failed
        except HomeAssistantError as err:
            error = str(err)
        except Exception as err:  # noqa: BLE001 - state must still be updated
            LOGGER.exception(
                "%s: unexpected error while syncing %r",
                self.label,
                title,
            )
            error = f"Unexpected error: {err!r}"
        finally:
            self._finish_sync(
                player=player,
                title=title,
                signature=signature,
                resolved=resolved,
                result=result,
                error=error,
                failed=failed,
            )

    @callback
    def _finish_sync(
        self,
        *,
        player: State,
        title: Any,
        signature: tuple[Any, ...],
        resolved: ResolvedColor | None,
        result: ApplyResult | None,
        error: str | None,
        failed: dict[str, str],
    ) -> None:
        """Record the outcome of :meth:`_async_sync_to` and notify entities."""
        state = self._state
        if resolved is not None:
            state.rgb_color = resolved.rgb
            state.source = resolved.source
            state.genre = resolved.genre
            state.matched_title = resolved.matched_title
            state.palette = list(resolved.palette)
            state.metadata_provider = resolved.metadata_provider
        else:
            state.rgb_color = None
            state.source = ColorSource.NONE
            state.genre = state.matched_title = state.metadata_provider = None
            state.palette = []

        if result is not None:
            # At least one light accepted the colour: this is success.
            self._applied_signature = signature
            self._last_outcome = (
                SyncOutcome.PARTIAL if result.failed else SyncOutcome.OK
            )
            state.syncing = True
            state.lights = result.applied
            state.light_colors = result.light_colors
            state.failed_lights = result.failed
            state.last_error = format_light_errors(result.failed) or None
            state.last_applied = dt_util.utcnow()
            state.pending_action = None
            LOGGER.info(
                "%s: %s playing %r -> %s (%s)%s%s",
                self.label,
                player.entity_id,
                title,
                resolved.rgb if resolved else None,
                resolved.source if resolved else None,
                f" palette {list(resolved.palette)}"
                if resolved and resolved.palette
                else "",
                f"; failed: {state.last_error}" if result.failed else "",
            )
        else:
            # Nothing was set; keep the signature unset so any change or a
            # resync tries again.
            self._applied_signature = None
            self._last_outcome = SyncOutcome.FAILED
            state.syncing = False
            state.lights = []
            state.light_colors = {}
            state.failed_lights = failed
            state.last_error = error
            LOGGER.warning(
                "%s: failed to sync lights for %r: %s",
                self.label,
                title,
                error,
            )
        self.async_set_updated_data(state)

    # ------------------------------------------------------------------ #
    # Colour resolution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _save_title(attrs: Mapping[str, Any]) -> str | None:
        """Title to store a colour under (series title for episodes)."""
        for candidate in (
            attrs.get(ATTR_MEDIA_SERIES_TITLE),
            attrs.get(ATTR_MEDIA_TITLE),
            attrs.get(ATTR_MEDIA_CONTENT_ID),
        ):
            if candidate and normalize_title(str(candidate)):
                return str(candidate)
        return None

    @staticmethod
    def _search_title(attrs: Mapping[str, Any]) -> str | None:
        """Cleaned title used for lookups, if cleaning changed anything."""
        raw = attrs.get(ATTR_MEDIA_SERIES_TITLE) or attrs.get(ATTR_MEDIA_TITLE)
        cleaned = clean_title(raw)
        if cleaned is None or cleaned.title == " ".join(str(raw).split()):
            return None
        return cleaned.title

    async def _async_resolve_color(self, player: State) -> ResolvedColor:
        """Walk the configured source priority and return the first hit."""
        attrs = player.attributes
        titles: list[str | None] = [
            attrs.get(ATTR_MEDIA_TITLE),
            attrs.get(ATTR_MEDIA_SERIES_TITLE),
        ]
        sources = [ColorSource(s) for s in self.options[CONF_COLOR_SOURCES]]
        if ColorSource.DEFAULT not in sources:
            sources.append(ColorSource.DEFAULT)

        for source in sources:
            if source is ColorSource.TITLE_MAPPING:
                # Originals first, then cleaned variants ("X: 2-Movie
                # Collection" / "X (Director's Cut)" also match a mapping
                # saved as "X").
                candidates = [
                    *titles,
                    attrs.get(ATTR_MEDIA_CONTENT_ID),
                    *(c.title for t in titles if (c := clean_title(t))),
                ]
                if hit := self._domain_data.titles.lookup(
                    candidates, fuzzy=bool(self.options[CONF_FUZZY_TITLE_MATCH])
                ):
                    return ResolvedColor(hit[0], source, matched_title=hit[1])
            elif source is ColorSource.POSTER:
                if resolved := await self._async_resolve_artwork(player):
                    return resolved
            elif source is ColorSource.GENRE:
                if resolved := await self._async_resolve_genre(player, titles):
                    return resolved
            elif source is ColorSource.DEFAULT:
                return ResolvedColor(self.default_color, source)
        return ResolvedColor(self.default_color, ColorSource.DEFAULT)

    async def _async_resolve_artwork(self, player: State) -> ResolvedColor | None:
        """Colour (and palette) from the player artwork or a TMDB poster."""
        picture: str | None = player.attributes.get("entity_picture")
        entity_id: str | None = player.entity_id
        provider: str | None = None
        if not picture and self.options[CONF_METADATA_ARTWORK]:
            meta = await self._async_metadata(player, require_artwork=True)
            if meta is not None:
                picture = meta.poster_url or meta.backdrop_url
                entity_id = None  # plain URL download, not the player's image
                provider = meta.provider
        if not picture:
            return None
        analysis = await self._async_poster_analysis(
            entity_id, picture, player.entity_id
        )
        if analysis is None or analysis.color is None:
            return None
        adjust = self.adjust_params
        palette = tuple(
            dict.fromkeys(adjust_color(c, adjust) for c in analysis.palette)
        )
        return ResolvedColor(
            palette[0] if palette else adjust_color(analysis.color, adjust),
            ColorSource.POSTER,
            palette=palette,
            metadata_provider=provider,
        )

    async def _async_resolve_genre(
        self, player: State, titles: list[str | None]
    ) -> ResolvedColor | None:
        """Genre colour: player attributes, then TMDB/Trakt, then keywords."""
        attrs = player.attributes
        genres: list[str] = []
        for attr in GENRE_ATTRIBUTES:
            genres.extend(iter_genres(attrs.get(attr)))
        if hit := match_genre_color(genres, []):
            return ResolvedColor(hit[0], ColorSource.GENRE, genre=hit[1])
        if (meta := await self._async_metadata(player)) is not None and (
            hit := match_genre_color(meta.genres, [])
        ):
            return ResolvedColor(
                hit[0], ColorSource.GENRE, genre=hit[1], metadata_provider=meta.provider
            )
        if hit := match_genre_color([], titles):
            return ResolvedColor(hit[0], ColorSource.GENRE, genre=hit[1])
        return None

    async def _async_metadata(
        self, player: State, *, require_artwork: bool = False
    ) -> MetadataResult | None:
        """Look up the playing title with TMDB/Trakt (if configured)."""
        if self.metadata is None:
            return None
        if require_artwork and not self.metadata.has_artwork_provider:
            return None
        attrs = player.attributes
        year = next(
            (attrs[a] for a in YEAR_ATTRIBUTES if attrs.get(a) is not None), None
        )
        query = build_query(
            attrs.get(ATTR_MEDIA_TITLE),
            attrs.get(ATTR_MEDIA_SERIES_TITLE),
            attrs.get(ATTR_MEDIA_CONTENT_TYPE),
            year,
        )
        if query is None:
            return None
        if (
            self._last_metadata is not None
            and self._last_metadata[0] == query
            and self._last_metadata[1] == require_artwork
        ):
            return self._last_metadata[2]
        result = await self.metadata.async_lookup(
            query, require_artwork=require_artwork
        )
        self._last_metadata = (query, require_artwork, result)
        return result

    @staticmethod
    def _picture_cache_key(picture: str) -> str:
        """Stable cache key for an artwork URL.

        Proxy URLs embed a per-entity access token that can rotate; the
        ``cache`` query parameter is the image hash, which is what matters.
        """
        try:
            url = URL(picture)
        except ValueError:
            return picture
        if url.path.startswith(_MEDIA_PLAYER_PROXY_PATH) and "cache" in url.query:
            return f"{url.path}#{url.query['cache']}"
        return str(url)

    async def _async_poster_analysis(
        self, entity_id: str | None, picture: str, log_id: str
    ) -> ImageAnalysis | None:
        """Return the (unadjusted) artwork colour and palette, cached.

        ``entity_id`` ``None`` downloads ``picture`` as a plain URL (TMDB).
        """
        params = self.extraction_params
        palette_size = self.palette_size
        key = (self._picture_cache_key(picture), params, palette_size)
        cache = self._domain_data.cache
        found, cached = cache.get(key)
        if found:
            return cached

        image = await self._async_fetch_image(entity_id, picture, log_id)
        if image is None:
            return None  # transient failure - do not cache
        analysis: ImageAnalysis | None
        try:
            analysis = await self.hass.async_add_executor_job(
                analyze_image, image, params, palette_size
            )
        except ColorExtractionError as err:
            LOGGER.warning("%s: %s", log_id, err)
            analysis = None
        cache.put(key, analysis)
        if analysis is None or analysis.color is None:
            LOGGER.debug("%s: artwork gave no usable colour", log_id)
        return analysis

    async def _async_fetch_image(
        self, entity_id: str | None, picture: str, log_id: str | None = None
    ) -> bytes | None:
        """Fetch artwork bytes for a media player (or a plain image URL).

        With ``entity_id`` ``None`` step 1 is skipped (e.g. a TMDB poster).

        Strategy, most robust first:

        1. Ask the media player *entity* via ``async_get_media_image()`` -
           exactly what HA's ``/api/media_player_proxy`` view does. It uses
           the integration's own fetch logic (auth headers, local-only
           hosts, custom implementations like Apple TV / Plex) and HA's
           shared image cache. No token or URL juggling needed.
        2. Absolute URL: download it with HA's shared aiohttp session.
        3. Relative URL (``/api/...``): prefix with HA's *internal* URL; the
           proxy URL already carries its access token as a query parameter.
        """
        log_id = log_id or entity_id or picture
        component = self.hass.data.get(MEDIA_PLAYER_DOMAIN)
        entity = (
            component.get_entity(entity_id)
            if component is not None and entity_id is not None
            else None
        )
        if entity is not None:
            try:
                async with asyncio.timeout(IMAGE_FETCH_TIMEOUT):
                    data, _content_type = await entity.async_get_media_image()
            except TimeoutError:
                LOGGER.debug("%s: async_get_media_image timed out", log_id)
            except Exception as err:  # noqa: BLE001 - third-party entity code
                LOGGER.debug("%s: async_get_media_image failed: %s", log_id, err)
            else:
                if data and len(data) <= IMAGE_MAX_BYTES:
                    return data

        verify_ssl = True
        if picture.startswith(("http://", "https://")):
            url = picture
        elif picture.startswith("/"):
            try:
                base = get_url(self.hass, allow_external=False, allow_cloud=False)
            except NoURLAvailableError:
                try:
                    base = get_url(self.hass, prefer_external=False)
                except NoURLAvailableError:
                    LOGGER.warning(
                        "%s: relative artwork URL but no Home Assistant URL "
                        "is configured (Settings > System > Network)",
                        log_id,
                    )
                    return None
            url = f"{base.rstrip('/')}{picture}"
            verify_ssl = False  # internal URL may use a self-signed cert
        else:
            LOGGER.debug("%s: unsupported artwork URL %r", log_id, picture)
            return None

        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=IMAGE_FETCH_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    LOGGER.debug("%s: artwork HTTP %s", log_id, resp.status)
                    return None
                if (resp.content_length or 0) > IMAGE_MAX_BYTES:
                    LOGGER.warning("%s: artwork too large, skipping", log_id)
                    return None
                data = await resp.content.read(IMAGE_MAX_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as err:
            LOGGER.debug("%s: artwork download failed: %s", log_id, err)
            return None
        if len(data) > IMAGE_MAX_BYTES:
            LOGGER.warning("%s: artwork too large, skipping", log_id)
            return None
        return data or None

    # ------------------------------------------------------------------ #
    # Lights
    # ------------------------------------------------------------------ #
    def _resolve_lights(self) -> list[str]:
        """Return target light entity IDs, expanding light groups if set."""
        return self._expand_lights(self.configured_lights)

    def _expand_lights(self, lights: list[str]) -> list[str]:
        """``lights`` with light groups expanded to members (if set), deduped."""
        if not self.options[CONF_EXPAND_GROUPS]:
            return list(dict.fromkeys(lights))
        result: list[str] = []
        seen: set[str] = set()

        def _walk(entity_id: str, depth: int) -> None:
            if entity_id in seen:
                return
            seen.add(entity_id)
            state = self.hass.states.get(entity_id)
            members = state.attributes.get(ATTR_ENTITY_ID) if state else None
            if depth < 4 and isinstance(members, (list, tuple)) and members:
                for member in members:
                    if isinstance(member, str) and member.startswith(
                        f"{LIGHT_DOMAIN}."
                    ):
                        _walk(member, depth + 1)
                return
            result.append(entity_id)

        for light in lights:
            _walk(light, 0)
        return result

    async def _async_take_snapshot(self, lights: list[str]) -> None:
        """Snapshot lights not yet captured (first change only).

        The TV's own lights belong to TV Mgmt: they're on whenever the TV is.
        Right after the TV turns on, TV Mgmt is still turning them on, so the
        snapshot waits for that, and a TV light that is still off then is
        saved as on - restoring it must not turn it off under a TV that's on.
        """
        missing = [e for e in lights if e not in self._snapshot]
        if not missing:
            return
        tv_lights = set(self._expand_lights(self._link.tv_lights()))
        if tv_lights & set(missing):
            if (wait := self._link.tv_lights_settle_remaining()) > 0:
                LOGGER.debug(
                    "%s: waiting %.1fs for the TV lights to come on", self.label, wait
                )
                await asyncio.sleep(wait)
        tv_on = self._link.tv_is_on() is not False
        changed = False
        for entity_id in missing:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNAVAILABLE_STATES:
                continue  # captured later once it becomes available
            if entity_id in tv_lights and tv_on and state.state == STATE_OFF:
                self._snapshot[entity_id] = {"state": STATE_ON, "attributes": {}}
                changed = True
                continue
            self._snapshot[entity_id] = {
                "state": state.state,
                "attributes": {
                    key: _jsonable(state.attributes[key])
                    for key in SNAPSHOT_ATTRIBUTES
                    if key in state.attributes
                },
            }
            changed = True
        if changed and self.options[CONF_PERSIST_SNAPSHOT]:
            await self._snapshot_store.async_save({"lights": self._snapshot})

    async def _async_clear_snapshot(self) -> None:
        self._snapshot = {}
        if self.options[CONF_PERSIST_SNAPSHOT]:
            await self._snapshot_store.async_remove()

    async def _async_apply_color(
        self, resolved: ResolvedColor, lights: list[str]
    ) -> ApplyResult:
        """Turn the lights on with the resolved colour(s), one call per light.

        In palette mode with a multi-colour palette, colour-capable lights
        (in configured order, groups expanded) get palette colours according
        to the strategy; otherwise every colour light gets ``resolved.rgb``.
        Lights without colour support get brightness only (or are skipped).

        Calls run concurrently with a per-call timeout. Unavailable or failing
        lights are reported in the result; only when *no* light could be set
        is :class:`LightSyncError` raised.
        """
        brightness_pct = int(self.options[CONF_BRIGHTNESS_PCT])
        transition = float(self.options[CONF_TRANSITION])
        unsupported = UnsupportedLightBehavior(self.options[CONF_UNSUPPORTED_LIGHTS])

        failed: dict[str, str] = {}
        # Classify lights first so only colour lights take palette slots.
        classified: list[tuple[str, str, bool]] = []  # (entity, kind, transition)
        for entity_id in lights:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNAVAILABLE_STATES:
                failed[entity_id] = "unavailable"
                continue
            modes = set(state.attributes.get(ATTR_SUPPORTED_COLOR_MODES) or [])
            if modes & COLOR_CAPABLE_MODES:
                kind = "color"
            elif unsupported is UnsupportedLightBehavior.BRIGHTNESS_ONLY:
                kind = "brightness" if modes & DIMMABLE_MODES else "onoff"
            else:
                if entity_id not in self._warned_unsupported:
                    self._warned_unsupported.add(entity_id)
                    LOGGER.warning(
                        "%s does not support colour and is skipped "
                        "(setting 'Lights without colour')",
                        entity_id,
                    )
                continue
            classified.append((entity_id, kind, _supports_transition(state)))

        if not classified:
            self._report_light_failures(failed, "set")
            raise LightSyncError(failed)

        color_lights = [e for e, kind, _ in classified if kind == "color"]
        if self.color_mode is SyncColorMode.PALETTE and len(resolved.palette) > 1:
            assignment = distribute_palette(
                resolved.palette, color_lights, self.palette_strategy
            )
        else:
            assignment = dict.fromkeys(color_lights, resolved.rgb)

        calls: dict[str, tuple[str, dict[str, Any]]] = {}
        for entity_id, kind, supports_transition in classified:
            data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}
            if kind == "color":
                data[ATTR_RGB_COLOR] = list(assignment[entity_id])
            if kind in ("color", "brightness"):
                data[ATTR_BRIGHTNESS_PCT] = brightness_pct
            if supports_transition and transition > 0:
                data[ATTR_TRANSITION] = transition
            calls[entity_id] = (SERVICE_TURN_ON, data)

        self._last_context = Context()
        failed.update(
            await async_call_lights(self.hass, calls, context=self._last_context)
        )
        self._report_light_failures(failed, "set")
        applied = [entity_id for entity_id in calls if entity_id not in failed]
        if not applied:
            raise LightSyncError(failed)
        return ApplyResult(
            applied=applied,
            light_colors={e: assignment[e] for e in applied if e in assignment},
            failed=failed,
        )

    def _report_light_failures(self, failed: Mapping[str, str], action: str) -> None:
        """Warn once per light per session; later repeats go to debug."""
        for entity_id, reason in failed.items():
            if entity_id in self._warned_failed:
                LOGGER.debug("Could not %s %s: %s", action, entity_id, reason)
                continue
            self._warned_failed.add(entity_id)
            LOGGER.warning(
                "%s: could not %s %s: %s (other lights are unaffected; it is "
                "retried on the next title change or resync)",
                self.label,
                action,
                entity_id,
                reason,
            )

    async def _async_turn_off(self, lights: list[str]) -> dict[str, str]:
        """Turn lights off (one call per light); return failures."""
        transition = float(self.options[CONF_TRANSITION])
        calls: dict[str, tuple[str, dict[str, Any]]] = {}
        failed: dict[str, str] = {}
        for entity_id in lights:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNAVAILABLE_STATES:
                failed[entity_id] = "unavailable"
                continue
            data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}
            if transition > 0 and _supports_transition(state):
                data[ATTR_TRANSITION] = transition
            calls[entity_id] = (SERVICE_TURN_OFF, data)
        failed.update(await async_call_lights(self.hass, calls))
        self._report_light_failures(failed, "turn off")
        return failed

    async def _async_restore(self, *, clear: bool) -> dict[str, str]:
        """Restore the snapshot; return failures.

        With ``clear`` the snapshot is dropped except for lights that could
        not be restored (still existing ones), so a later stop / resync
        retries them instead of forgetting their original state.
        """
        failed: dict[str, str] = {}
        if self._snapshot:
            failed = await async_restore_snapshot(
                self.hass, self._snapshot, float(self.options[CONF_TRANSITION])
            )
            self._report_light_failures(failed, "restore")
        if clear:
            keep = {
                entity_id: self._snapshot[entity_id]
                for entity_id in failed
                if entity_id in self._snapshot
                and self.hass.states.get(entity_id) is not None
            }
            if keep:
                self._snapshot = keep
                if self.options[CONF_PERSIST_SNAPSHOT]:
                    await self._snapshot_store.async_save({"lights": keep})
            else:
                await self._async_clear_snapshot()
        return failed

    # ------------------------------------------------------------------ #
    # Idle (pause / stop) handling with debounce
    # ------------------------------------------------------------------ #
    @callback
    def _cancel_idle(self) -> None:
        if self._idle_unsub is not None:
            self._idle_unsub()
            self._idle_unsub = None
            LOGGER.debug("%s: pending idle action cancelled", self.label)
        self._idle_behavior = None
        self._state.pending_action = None

    @callback
    def _schedule_idle(self, behavior: IdleBehavior, *, session_end: bool) -> None:
        """Run ``behavior`` after ``restore_delay`` unless playback resumes."""
        if self._idle_unsub is not None and self._idle_behavior is behavior:
            return
        self._cancel_idle()
        delay = float(self.options[CONF_RESTORE_DELAY])
        self._idle_behavior = behavior
        self._state.pending_action = behavior.value
        self.async_set_updated_data(self._state)

        async def _run(_now: datetime | None = None) -> None:
            self._idle_unsub = None
            async with self._lock:
                if self._find_active_player() is not None or not self._state.enabled:
                    return
                await self._async_run_idle_behavior(behavior, session_end=session_end)
                self.async_set_updated_data(self._state)

        LOGGER.debug("%s: scheduling %s in %.1fs", self.label, behavior, delay)
        self._idle_unsub = async_call_later(
            self.hass, max(delay, 0.0), HassJob(_run, cancel_on_shutdown=True)
        )

    async def _async_run_idle_behavior(
        self,
        behavior: IdleBehavior,
        *,
        session_end: bool,
        exclude: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        """Apply a pause/stop behaviour. Caller must hold ``self._lock``.

        Lights in ``exclude`` are left alone (TV Mgmt is turning them off).
        """
        LOGGER.debug(
            "%s: running %s (session_end=%s)",
            self.label,
            behavior,
            session_end,
        )
        # The TV lights turned off by hand stay off too.
        exclude = set(exclude) | self._held_off()
        lights = [e for e in self._resolve_lights() if e not in exclude]
        failed: dict[str, str] = {}
        error: str | None = None
        try:
            if behavior is not IdleBehavior.RESTORE and not lights:
                if session_end:
                    await self._async_clear_snapshot()
            elif behavior is IdleBehavior.RESTORE:
                failed = await self._async_restore(clear=True)
            elif behavior is IdleBehavior.TURN_OFF:
                failed = await self._async_turn_off(lights)
                if session_end:
                    await self._async_clear_snapshot()
            elif behavior is IdleBehavior.DEFAULT_COLOR:
                result = await self._async_apply_color(
                    ResolvedColor(self.default_color, ColorSource.DEFAULT), lights
                )
                failed = result.failed
                self._state.light_colors = result.light_colors
                self._state.rgb_color = self.default_color
                self._state.source = ColorSource.DEFAULT
                self._state.palette = []
                if session_end:
                    await self._async_clear_snapshot()
            elif session_end:  # LEAVE at end of session
                await self._async_clear_snapshot()
        except LightSyncError as err:
            failed, error = err.failed, str(err)
        except HomeAssistantError as err:
            error = str(err)
        except Exception as err:  # noqa: BLE001 - state must still be updated
            LOGGER.exception("%s: idle action failed", self.label)
            error = f"Unexpected error: {err!r}"
        if error:
            LOGGER.warning("%s: idle action failed: %s", self.label, error)
        self._state.failed_lights = failed
        self._state.last_error = error or format_light_errors(failed) or None
        if session_end:
            self._warned_failed.clear()

        self._applied_signature = None
        self._idle_behavior = None
        self._state.syncing = False
        self._state.pending_action = None
        if behavior in (IdleBehavior.RESTORE, IdleBehavior.TURN_OFF):
            self._state.source = ColorSource.NONE
            self._state.rgb_color = None
            self._state.palette = []
            self._state.light_colors = {}

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #
    def as_diagnostics(self) -> dict[str, Any]:
        """Return a JSON-safe diagnostics dump (redacted by the caller)."""
        state = asdict(self._state)
        state["last_applied"] = (
            self._state.last_applied.isoformat() if self._state.last_applied else None
        )
        return {
            "zone_id": self.zone_id,
            "name": self.zone_name,
            "options": dict(self.options),
            "held_reason": self._held_reason(),
            "state": state,
            "media_players": self.media_players,
            "configured_lights": self.configured_lights,
            "resolved_lights": self._resolve_lights(),
            "snapshot": self._snapshot,
            "applied_signature": list(self._applied_signature or []),
            "pending_idle_behavior": self._idle_behavior,
            "warned_unsupported": sorted(self._warned_unsupported),
            "warned_failed": sorted(self._warned_failed),
            "last_outcome": self._last_outcome,
            "metadata": self.metadata.as_diagnostics() if self.metadata else None,
        }


# ---------------------------------------------------------------------- #
# Module helpers
# ---------------------------------------------------------------------- #
def _jsonable(value: Any) -> Any:
    """Convert tuples/sets/enums into JSON-friendly values for storage."""
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and not isinstance(value, (int, float, str)):
        return value.value
    return value


def _supports_transition(state: State) -> bool:
    return bool(
        int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)
        & LightEntityFeature.TRANSITION
    )


async def async_call_lights(
    hass: HomeAssistant,
    calls: Mapping[str, tuple[str, dict[str, Any]]],
    *,
    context: Context | None = None,
    call_timeout: float | None = None,
) -> dict[str, str]:
    """Run one ``light.<service>`` call per entity, concurrently.

    Each call is blocking with its own ``call_timeout`` (default
    ``LIGHT_CALL_TIMEOUT``); a call that times out
    keeps running in the background (it is not cancelled half-way inside the
    light integration). Returns ``{entity_id: reason}`` for every light that
    failed - one light's error never affects the others.
    """
    if not calls:
        return {}
    if call_timeout is None:
        call_timeout = LIGHT_CALL_TIMEOUT

    async def _one(entity_id: str, service: str, data: dict[str, Any]) -> None:
        task = hass.async_create_background_task(
            hass.services.async_call(
                LIGHT_DOMAIN, service, data, blocking=True, context=context
            ),
            f"{DOMAIN} light sync light.{service} {entity_id}",
        )
        try:
            async with asyncio.timeout(call_timeout):
                await asyncio.shield(task)
        except TimeoutError:
            task.add_done_callback(_log_late_result(entity_id, service))
            raise

    results = await asyncio.gather(
        *(_one(entity_id, *call) for entity_id, call in calls.items()),
        return_exceptions=True,
    )
    failed: dict[str, str] = {}
    for entity_id, result in zip(calls, results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            failed[entity_id] = _describe_light_error(result, call_timeout)
            if not isinstance(result, (HomeAssistantError, TimeoutError)):
                LOGGER.debug("Unexpected error from %s", entity_id, exc_info=result)
    return failed


def _describe_light_error(err: BaseException, timeout: float) -> str:
    """Human readable reason for a failed light call."""
    if isinstance(err, TimeoutError):
        return f"no response within {timeout:g} s"
    if isinstance(err, HomeAssistantError):
        return str(err) or type(err).__name__
    return f"{type(err).__name__}: {err}" if str(err) else type(err).__name__


def _log_late_result(entity_id: str, service: str) -> Callable[[asyncio.Task], None]:
    """Done-callback for a timed-out light call (retrieves its exception)."""

    def _done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        if (err := task.exception()) is not None:
            LOGGER.debug("Late failure of light.%s %s: %s", service, entity_id, err)
        else:
            LOGGER.debug("light.%s %s completed after the timeout", service, entity_id)

    return _done


async def async_restore_snapshot(
    hass: HomeAssistant,
    snapshot: Mapping[str, Mapping[str, Any]],
    transition: float,
) -> dict[str, str]:
    """Restore lights to a snapshot taken by the coordinator.

    Mirrors what ``scene.create`` + ``scene.turn_on`` would do but only
    passes the colour attribute that matches the saved ``color_mode`` (light
    services reject mixed colour attributes). One call per light,
    concurrently; returns ``{entity_id: reason}`` for lights not restored.
    """
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    failed: dict[str, str] = {}
    for entity_id, saved in snapshot.items():
        current = hass.states.get(entity_id)
        if current is None or current.state in UNAVAILABLE_STATES:
            failed[entity_id] = "unavailable"
            continue
        attrs: Mapping[str, Any] = saved.get("attributes", {})
        data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}
        if transition > 0 and _supports_transition(current):
            data[ATTR_TRANSITION] = transition

        if saved.get("state") == STATE_OFF:
            service = SERVICE_TURN_OFF
        elif saved.get("state") == STATE_ON:
            service = SERVICE_TURN_ON
            if (brightness := attrs.get(ATTR_BRIGHTNESS)) is not None:
                data[ATTR_BRIGHTNESS] = brightness
            mode = attrs.get(ATTR_COLOR_MODE)
            if mode == ColorMode.WHITE and brightness is not None:
                data.pop(ATTR_BRIGHTNESS, None)
                data[ATTR_WHITE] = brightness
            elif (attr := RESTORE_COLOR_ATTR.get(str(mode))) and (
                value := attrs.get(attr)
            ) is not None:
                data[attr] = value
            effect = attrs.get(ATTR_EFFECT)
            if effect and str(effect).lower() not in (EFFECT_OFF, "none"):
                data[ATTR_EFFECT] = effect
        else:
            continue
        calls[entity_id] = (service, data)
    failed.update(await async_call_lights(hass, calls))
    return failed


async def async_remove_zone_snapshots(
    hass: HomeAssistant, entry_id: str, zones: list[dict[str, Any]]
) -> None:
    """A TV was deleted: restore its zones' saved light states, then drop them."""
    for zone in zones:
        store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_SNAPSHOT}.{entry_id}_{zone[CONF_ZONE_ID]}",
        )
        data = await store.async_load() or {}
        snapshot = data.get("lights")
        if isinstance(snapshot, dict) and snapshot:
            failed = await async_restore_snapshot(
                hass, snapshot, float(zone.get(CONF_TRANSITION) or 0)
            )
            if failed:
                LOGGER.warning(
                    "Light sync: couldn't restore %s after the TV was deleted",
                    format_light_errors(failed),
                )
        await store.async_remove()
