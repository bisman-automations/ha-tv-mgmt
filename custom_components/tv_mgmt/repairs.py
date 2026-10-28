"""Repairs when a profile's TV, Apple TV, media player or light goes missing.

Home Assistant removes or renames an entity in the entity registry when the
integration that owns it is deleted, or when someone renames it. A profile
stores those entity ids (the TV, the Apple TV, warning speakers, TV lights,
and the lights and extra players of a light zone). This module watches the
entity registry and raises a repair for each one that disappears, so a parent
can pick the replacement or take it off the profile.

The repair flow checks the same access rule as the sidebar and the services:
admins, plus parents an admin has allowed. Automations never open a repair.
Diagnostics are unchanged; speaker names stay out of the diagnostics download.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, issue_registry as ir, selector

from .access import async_ensure_allowed
from .const import (
    APPLE_TV_DOMAIN,
    CONF_ANNOUNCE_PLAYERS,
    CONF_MEDIA_PLAYER,
    CONF_STREAMING_PLAYER,
    CONF_TV_LIGHTS,
    DOMAIN,
    SECTION_ANNOUNCE,
    SECTION_APPLE_TV,
    SECTION_LIGHTS,
)
from .light_sync.const import CONF_EXTRA_PLAYERS, CONF_LIGHTS, CONF_ZONE_ID
from .light_sync.storage import DATA_LIGHT_SYNC

# Issue ids start with this so they aren't mixed up with light sync's
# metadata-key repairs.
_ISSUE_PREFIX = "entity-"
_REMOVED = "entity_removed"
_RENAMED = "entity_renamed"
_ISSUE_KEYS = frozenset({_REMOVED, _RENAMED})

# Where the entity id is stored. The fix flow writes back to the same place.
LOCATION_DATA = "data"  # the profile's TV (entry data)
LOCATION_APPLE_TV = "apple_tv"
LOCATION_ANNOUNCE = "announce"
LOCATION_TV_LIGHTS = "tv_lights"
LOCATION_ZONE_LIGHT = "zone_light"
LOCATION_ZONE_PLAYER = "zone_player"

_KIND_TV = "tv"
_KIND_APPLE_TV = "apple_tv"
_KIND_MEDIA_PLAYER = "media_player"
_KIND_LIGHT = "light"

# Parent-facing name of each place, filled into the translation placeholders.
_ROLE = {
    LOCATION_DATA: "TV",
    LOCATION_APPLE_TV: "Apple TV",
    LOCATION_ANNOUNCE: "speaker",
    LOCATION_TV_LIGHTS: "light",
    LOCATION_ZONE_LIGHT: "light",
    LOCATION_ZONE_PLAYER: "media player",
}

_DATA_RENAMES = f"{DOMAIN}_entity_renames"
_DATA_GONE = f"{DOMAIN}_gone_entities"
_DATA_LISTENER = f"{DOMAIN}_entity_repairs_listener"

CONF_REPLACEMENT = "replacement"
CONF_REMOVE = "remove"

_LEARN_MORE = (
    "https://github.com/bisman-automations/ha-tv-mgmt#when-something-is-removed"
)


def _slug(value: str) -> str:
    """An issue id piece: letters, digits and hyphens only."""
    return "".join(char if char.isalnum() else "-" for char in value)


def _issue_id(entry_id: str, location: str, zone_id: str, entity_id: str) -> str:
    return f"{_ISSUE_PREFIX}{location}-{entry_id}-{_slug(zone_id or 'profile')}-{_slug(entity_id)}"


def _renames(hass: HomeAssistant) -> dict[str, str]:
    """Old entity id to the id Home Assistant renamed it to, for this session."""
    return hass.data.setdefault(_DATA_RENAMES, {})


def _gone(hass: HomeAssistant) -> set[str]:
    """Entity ids the registry removed or renamed away from.

    A state can still be sitting on the old id for a moment (or in a test).
    The registry event is what counts: that entity is no longer the one the
    profile stored.
    """
    return hass.data.setdefault(_DATA_GONE, set())


def entity_exists(hass: HomeAssistant, entity_id: str) -> bool:
    """True when Home Assistant still has this entity.

    A registry entry counts even when it has no state (unavailable, not
    removed). A state with no registry entry counts too, unless the registry
    just removed or renamed that id. A registry entry coming back clears that.
    """
    if not entity_id:
        return False
    if er.async_get(hass).async_get(entity_id) is not None:
        _gone(hass).discard(entity_id)
        return True
    if entity_id in _gone(hass):
        return False
    return hass.states.get(entity_id) is not None


def _display_name(hass: HomeAssistant, entity_id: str, suggested: str) -> str:
    """The friendliest name we can still see, else the entity id."""
    for candidate in (suggested, entity_id):
        if not candidate:
            continue
        state = hass.states.get(candidate)
        if state is not None and state.name and state.name != candidate:
            return state.name
    return entity_id


class UsedEntity:
    """One entity id a profile stores, and where to write a replacement."""

    def __init__(
        self,
        entry_id: str,
        entity_id: str,
        kind: str,
        location: str,
        zone_id: str = "",
    ) -> None:
        self.entry_id = entry_id
        self.entity_id = entity_id
        self.kind = kind
        self.location = location
        self.zone_id = zone_id

    def issue_id(self) -> str:
        return _issue_id(self.entry_id, self.location, self.zone_id, self.entity_id)


def _section(options: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    raw = options.get(name) or {}
    return raw if isinstance(raw, Mapping) else {}


def used_entities(hass: HomeAssistant, entry: ConfigEntry) -> list[UsedEntity]:
    """Every TV, Apple TV, media player and light the profile stores."""
    found: list[UsedEntity] = []
    seen: set[tuple[str, str, str]] = set()

    def add(entity_id: Any, kind: str, location: str, zone_id: str = "") -> None:
        if not isinstance(entity_id, str) or not entity_id:
            return
        key = (location, zone_id, entity_id)
        if key in seen:
            return
        seen.add(key)
        found.append(UsedEntity(entry.entry_id, entity_id, kind, location, zone_id))

    add(entry.data.get(CONF_MEDIA_PLAYER), _KIND_TV, LOCATION_DATA)
    apple = _section(entry.options, SECTION_APPLE_TV)
    add(apple.get(CONF_STREAMING_PLAYER), _KIND_APPLE_TV, LOCATION_APPLE_TV)
    announce = _section(entry.options, SECTION_ANNOUNCE)
    for player in announce.get(CONF_ANNOUNCE_PLAYERS) or []:
        add(player, _KIND_MEDIA_PLAYER, LOCATION_ANNOUNCE)
    lights = _section(entry.options, SECTION_LIGHTS)
    for light in lights.get(CONF_TV_LIGHTS) or []:
        add(light, _KIND_LIGHT, LOCATION_TV_LIGHTS)

    data = hass.data.get(DATA_LIGHT_SYNC)
    if data is not None:
        for zone in data.config.zones_for(entry.entry_id):
            zone_id = str(zone.get(CONF_ZONE_ID) or "")
            for light in zone.get(CONF_LIGHTS) or []:
                add(light, _KIND_LIGHT, LOCATION_ZONE_LIGHT, zone_id)
            for player in zone.get(CONF_EXTRA_PLAYERS) or []:
                add(player, _KIND_MEDIA_PLAYER, LOCATION_ZONE_PLAYER, zone_id)
    return found


def _copy_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """A writable copy of the entry options (sections and their lists)."""
    copied: dict[str, Any] = {}
    for key, value in options.items():
        if isinstance(value, Mapping):
            copied[key] = {
                inner: (list(item) if isinstance(item, list) else item)
                for inner, item in value.items()
            }
        else:
            copied[key] = value
    return copied


def _edit_list(items: list[Any], old: str, new: str | None) -> list[str]:
    """Drop ``old``, or swap it for ``new``. Keeps the order and skips duplicates."""
    result: list[str] = []
    for item in items:
        if item == old:
            if new and new not in result:
                result.append(new)
            continue
        if isinstance(item, str) and item not in result:
            result.append(item)
    return result


@callback
def async_reconcile_entities(hass: HomeAssistant) -> None:
    """Raise a repair for each missing entity, and clear repairs that are fixed.

    Called when the entity registry changes, when a profile is set up, and
    after options are saved. An entity that only changed its friendly name
    is left alone: the profile still has the right id.
    """
    registry = ir.async_get(hass)
    wanted: dict[str, UsedEntity] = {}
    profiles: dict[str, ConfigEntry] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        profiles[entry.entry_id] = entry
        for ref in used_entities(hass, entry):
            wanted[ref.issue_id()] = ref

    # Drop repairs whose profile is gone, or whose entity is back, or that
    # the profile no longer stores (someone already changed it).
    for issue in list(registry.issues.values()):
        if issue.domain != DOMAIN or issue.translation_key not in _ISSUE_KEYS:
            continue
        ref = wanted.get(issue.issue_id)
        if ref is None or entity_exists(hass, ref.entity_id):
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)

    renames = _renames(hass)
    for issue_id, ref in wanted.items():
        if entity_exists(hass, ref.entity_id):
            continue
        suggested = renames.get(ref.entity_id, "")
        if suggested and not entity_exists(hass, suggested):
            suggested = ""
        _create_issue(hass, profiles[ref.entry_id], ref, suggested, issue_id)


def _create_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    ref: UsedEntity,
    suggested: str,
    issue_id: str,
) -> None:
    """Create the repair, or refresh it when the suggested replacement changes."""
    translation_key = _RENAMED if suggested else _REMOVED
    placeholders = {
        "profile": entry.title,
        "name": _display_name(hass, ref.entity_id, suggested),
        "entity": ref.entity_id,
        "role": _ROLE.get(ref.location, "entity"),
        "suggested": suggested,
    }
    data: dict[str, str | int | float | None] = {
        "entry_id": ref.entry_id,
        "entity_id": ref.entity_id,
        "kind": ref.kind,
        "location": ref.location,
        "zone_id": ref.zone_id,
        "suggested": suggested,
    }
    registry = ir.async_get(hass)
    existing = registry.async_get_issue(DOMAIN, issue_id)
    if (
        existing is not None
        and existing.data == data
        and existing.translation_placeholders == placeholders
        and existing.translation_key == translation_key
    ):
        return  # Leave a dismissed repair dismissed.
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        data=data,
        is_fixable=True,
        is_persistent=True,
        learn_more_url=_LEARN_MORE,
        severity=(
            ir.IssueSeverity.ERROR
            if ref.location == LOCATION_DATA
            else ir.IssueSeverity.WARNING
        ),
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )


@callback
def async_delete_entry_issues(hass: HomeAssistant, entry_id: str) -> None:
    """The profile was deleted: its repairs go with it."""
    registry = ir.async_get(hass)
    for issue in list(registry.issues.values()):
        if issue.domain != DOMAIN or issue.translation_key not in _ISSUE_KEYS:
            continue
        if (issue.data or {}).get("entry_id") == entry_id:
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Watch the entity registry for the life of this Home Assistant run."""
    if hass.data.get(_DATA_LISTENER) is not None:
        return

    @callback
    def _on_registry(event: Event) -> None:
        action = event.data.get("action")
        entity_id = event.data.get("entity_id")
        old_entity_id = event.data.get("old_entity_id")
        if (
            action == "update"
            and isinstance(old_entity_id, str)
            and isinstance(entity_id, str)
            and old_entity_id != entity_id
        ):
            # Renamed. The profile still has the old id.
            _renames(hass)[old_entity_id] = entity_id
            _gone(hass).add(old_entity_id)
            _gone(hass).discard(entity_id)
        elif action == "remove" and isinstance(entity_id, str):
            _gone(hass).add(entity_id)
        elif action == "create" and isinstance(entity_id, str):
            _gone(hass).discard(entity_id)
        else:
            return
        async_reconcile_entities(hass)

    hass.data[_DATA_LISTENER] = hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED, _on_registry
    )


def _expected_domain(kind: str) -> str:
    return "light" if kind == _KIND_LIGHT else MP_DOMAIN


async def _apply_replacement(
    hass: HomeAssistant, entry: ConfigEntry, data: Mapping[str, Any], new: str | None
) -> str | None:
    """Write the replacement (or a removal) back. Returns an error key, or None.

    ``new`` is None when the entity is being removed from the profile. Removing
    the TV deletes the profile: there is nothing left for it to watch. Anything
    else is taken out of the option or the light zone that stored it.

    Changing the TV or which Apple TV is linked reloads the profile, through
    the update listener (one reload). Speakers, TV lights and zone lights
    apply in place.
    """
    location = str(data.get("location") or "")
    old = str(data.get("entity_id") or "")
    zone_id = str(data.get("zone_id") or "")

    if location == LOCATION_DATA:
        if new is None:
            await hass.config_entries.async_remove(entry.entry_id)
            return None
        other = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, new)
        if other is not None and other.entry_id != entry.entry_id:
            return "already_configured"
        state = hass.states.get(new)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_MEDIA_PLAYER: new},
            unique_id=new,
            title=state.name if state is not None and state.name else entry.title,
        )
        return None

    if location == LOCATION_APPLE_TV:
        options = _copy_options(entry.options)
        section = options.setdefault(SECTION_APPLE_TV, {})
        section[CONF_STREAMING_PLAYER] = new
        hass.config_entries.async_update_entry(entry, options=options)
        return None

    if location in (LOCATION_ANNOUNCE, LOCATION_TV_LIGHTS):
        options = _copy_options(entry.options)
        if location == LOCATION_ANNOUNCE:
            section = options.setdefault(SECTION_ANNOUNCE, {})
            key = CONF_ANNOUNCE_PLAYERS
        else:
            section = options.setdefault(SECTION_LIGHTS, {})
            key = CONF_TV_LIGHTS
        section[key] = _edit_list(list(section.get(key) or []), old, new)
        hass.config_entries.async_update_entry(entry, options=options)
        return None

    if location in (LOCATION_ZONE_LIGHT, LOCATION_ZONE_PLAYER):
        stored = hass.data.get(DATA_LIGHT_SYNC)
        if stored is None or not zone_id:
            return "zone_missing"
        zone = stored.config.zone(entry.entry_id, zone_id)
        if zone is None:
            return "zone_missing"
        key = CONF_LIGHTS if location == LOCATION_ZONE_LIGHT else CONF_EXTRA_PLAYERS
        updated = dict(zone)
        updated[key] = _edit_list(list(zone.get(key) or []), old, new)
        await stored.config.async_save_zone(entry.entry_id, updated)
        saved = stored.config.zone(entry.entry_id, zone_id)
        manager = getattr(entry, "runtime_data", None)
        light_sync = getattr(manager, "light_sync", None)
        if light_sync is not None and saved is not None:
            await light_sync.async_save_zone(saved)
        return None

    return "zone_missing"


class EntityRepairFlow(RepairsFlow):
    """Pick the entity that replaced a missing one, or remove it from the profile."""

    def _data(self) -> dict[str, Any]:
        raw = self.data if isinstance(getattr(self, "data", None), dict) else {}
        if not raw:
            issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
            raw = dict(issue.data) if issue and issue.data else {}
        return raw

    async def _allowed(self) -> bool:
        """The same people who may use the sidebar may fix a repair."""
        user_id = self.context.get("user_id")
        context = Context(user_id=user_id) if user_id else None
        try:
            await async_ensure_allowed(self.hass, context)
        except HomeAssistantError:
            return False
        return True

    def _placeholders(self) -> dict[str, str]:
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        if issue and issue.translation_placeholders:
            return dict(issue.translation_placeholders)
        data = self._data()
        return {
            "profile": "",
            "name": str(data.get("entity_id") or ""),
            "entity": str(data.get("entity_id") or ""),
            "role": _ROLE.get(str(data.get("location") or ""), "entity"),
            "suggested": str(data.get("suggested") or ""),
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        """Ask for the replacement, or to remove the entity from the profile."""
        if not await self._allowed():
            return self.async_abort(reason="not_allowed")

        data = self._data()
        entry_id = str(data.get("entry_id") or "")
        entity_id = str(data.get("entity_id") or "")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not entity_id:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_abort(reason="profile_missing")
        if entity_exists(self.hass, entity_id):
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_abort(reason="already_fixed")

        errors: dict[str, str] = {}
        if user_input is not None:
            remove = bool(user_input.get(CONF_REMOVE))
            replacement = user_input.get(CONF_REPLACEMENT) or None
            if isinstance(replacement, str):
                replacement = replacement.strip() or None
            if remove:
                # Removing the TV deletes the whole profile. Confirm that first.
                if str(data.get("location")) == LOCATION_DATA:
                    return await self.async_step_confirm_remove()
                error = await _apply_replacement(self.hass, entry, data, None)
                if error:
                    errors["base"] = error
                else:
                    async_reconcile_entities(self.hass)
                    return self.async_create_entry(data={})
            elif not replacement:
                errors["base"] = "pick_or_remove"
            elif replacement == entity_id or not entity_exists(self.hass, replacement):
                errors["base"] = (
                    "still_missing" if replacement == entity_id else "unknown_entity"
                )
            elif not _replacement_ok(
                self.hass, str(data.get("kind") or ""), replacement
            ):
                errors["base"] = _replacement_error(
                    self.hass, str(data.get("kind") or ""), replacement
                )
            else:
                error = await _apply_replacement(self.hass, entry, data, replacement)
                if error:
                    errors["base"] = error
                else:
                    async_reconcile_entities(self.hass)
                    return self.async_create_entry(data={})

        suggested = str(data.get("suggested") or "")
        # suggested_value shows the renamed entity in the form. It is not a
        # schema default, so submitting the form without a choice is an error
        # rather than silently accepting it.
        description: dict[str, Any] = {}
        if suggested and entity_exists(self.hass, suggested):
            description["suggested_value"] = suggested
        replacement_key: Any = vol.Optional(CONF_REPLACEMENT, description=description)
        kind = str(data.get("kind") or _KIND_MEDIA_PLAYER)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    replacement_key: _entity_selector(kind),
                    vol.Required(
                        CONF_REMOVE, default=False
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Deleting the TV deletes the profile. Ask once more."""
        if not await self._allowed():
            return self.async_abort(reason="not_allowed")
        data = self._data()
        entry = self.hass.config_entries.async_get_entry(
            str(data.get("entry_id") or "")
        )
        if user_input is not None:
            if entry is not None:
                await _apply_replacement(self.hass, entry, data, None)
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm_remove",
            data_schema=vol.Schema({}),
            description_placeholders=self._placeholders(),
        )


def _replacement_ok(hass: HomeAssistant, kind: str, entity_id: str) -> bool:
    """The replacement is the right domain, and an Apple TV when one is required."""
    return _replacement_error(hass, kind, entity_id) is None


def _replacement_error(hass: HomeAssistant, kind: str, entity_id: str) -> str | None:
    """Why this replacement can't be used, or None when it can."""
    domain = entity_id.split(".", 1)[0]
    if domain != _expected_domain(kind):
        return "wrong_kind"
    if kind != _KIND_APPLE_TV:
        return None
    registered = er.async_get(hass).async_get(entity_id)
    if registered is not None and registered.platform != APPLE_TV_DOMAIN:
        return "not_apple_tv"
    return None


def _entity_selector(kind: str) -> selector.EntitySelector:
    """The same kind of picker setup uses for this entity."""
    if kind == _KIND_LIGHT:
        config = selector.EntitySelectorConfig(domain="light")
    elif kind == _KIND_APPLE_TV:
        config = selector.EntitySelectorConfig(
            integration=APPLE_TV_DOMAIN, domain=MP_DOMAIN
        )
    else:
        config = selector.EntitySelectorConfig(domain=MP_DOMAIN)
    return selector.EntitySelector(config)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Start the fix flow for a missing TV, Apple TV, media player or light."""
    flow = EntityRepairFlow()
    flow.data = dict(data or {})
    return flow
