"""Entities of a light sync zone (one device per zone, under the TV's device).

The values shown by the selects and numbers are the zone's settings, as set
in the panel's Lights tab; changing one here saves it and applies it live.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..access import async_ensure_allowed
from .color import rgb_to_hex, rgb_to_hs
from .const import (
    ATTR_COLOR_MODE,
    ATTR_ENTITY_PICTURE,
    ATTR_FAILED_LIGHTS,
    ATTR_GENRE,
    ATTR_HS_COLOR,
    ATTR_LAST_APPLIED,
    ATTR_LAST_ERROR,
    ATTR_LIGHT_COLORS,
    ATTR_LIGHT_ERRORS,
    ATTR_LIGHTS,
    ATTR_MEDIA_PLAYER,
    ATTR_MEDIA_TITLE,
    ATTR_METADATA_PROVIDER,
    ATTR_PALETTE,
    ATTR_PALETTE_HEX,
    ATTR_PENDING_ACTION,
    ATTR_RGB_COLOR,
    ATTR_SEARCH_TITLE,
    ATTR_SOURCE,
    COLOR_SOURCE_PRESETS,
    CONF_BRIGHTNESS_PCT,
    CONF_COLOR_MODE,
    CONF_COLOR_SOURCES,
    CONF_EXTRACTION_METHOD,
    CONF_PALETTE_STRATEGY,
    CONF_TRANSITION,
    DOMAIN,
    ColorSource,
    ExtractionMethod,
    PaletteStrategy,
    SyncColorMode,
)
from .controller import LightZone, SyncOutcome, SyncState


class LightZoneEntity(CoordinatorEntity[LightZone]):
    """Common base: unique IDs, names and the zone's device."""

    _attr_has_entity_name = True

    def __init__(self, zone: LightZone, key: str) -> None:
        super().__init__(zone)
        self._attr_translation_key = key
        self._attr_unique_id = f"{zone.unique_key}_{key}"
        # The device itself (name, model, link to the TV's device) is made
        # by LightSync; entities only point at it.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, zone.unique_key)})

    async def _ensure_allowed(self) -> None:
        """Only admins and allowed parents may change TV Mgmt from the UI."""
        await async_ensure_allowed(self.hass, self._context)


# ---- switch ----------------------------------------------------------------------


class SyncEnabledSwitch(LightZoneEntity, SwitchEntity):
    """Turns the zone's syncing on and off (saved with the zone).

    Turning it off applies the zone's stop behaviour (e.g. restores the
    lights) and stops following the players until it's turned back on.
    """

    def __init__(self, zone: LightZone) -> None:
        super().__init__(zone, "sync_enabled")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._ensure_allowed()
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._ensure_allowed()
        await self.coordinator.async_set_enabled(False)


# ---- binary sensor ---------------------------------------------------------------


class SyncingBinarySensor(LightZoneEntity, BinarySensorEntity):
    """``on`` while the lights show a colour for what's playing."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, zone: LightZone) -> None:
        super().__init__(zone, "syncing")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.syncing

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Player, lights, pending action and failures.

        ``failed_lights`` / ``light_errors`` list the lights the last apply
        or restore couldn't set (e.g. an offline cloud light); the others
        are still synced (``lights``). ``held_reason`` says why TV Mgmt
        isn't letting the zone colour (``tv_off``, ``blocked``, ``stopped``).
        """
        data = self.coordinator.data
        return {
            ATTR_MEDIA_PLAYER: data.media_player if data.syncing else None,
            ATTR_LIGHTS: data.lights,
            ATTR_PENDING_ACTION: data.pending_action,
            ATTR_FAILED_LIGHTS: sorted(data.failed_lights),
            ATTR_LIGHT_ERRORS: dict(data.failed_lights),
            ATTR_LAST_ERROR: data.last_error,
            "last_outcome": self.coordinator.last_outcome,
            "held_reason": data.held_reason,
        }


# ---- sensors ---------------------------------------------------------------------


def _color_attributes(data: SyncState) -> dict[str, Any]:
    rgb = data.rgb_color
    return {
        ATTR_RGB_COLOR: list(rgb) if rgb else None,
        ATTR_HS_COLOR: list(rgb_to_hs(rgb)) if rgb else None,
        ATTR_SOURCE: data.source.value,
        ATTR_MEDIA_TITLE: data.media_title,
        ATTR_MEDIA_PLAYER: data.media_player,
        ATTR_ENTITY_PICTURE: data.entity_picture,
        ATTR_SEARCH_TITLE: data.search_title,
        ATTR_GENRE: data.genre,
        "matched_title": data.matched_title,
        ATTR_METADATA_PROVIDER: data.metadata_provider,
        ATTR_COLOR_MODE: data.color_mode.value,
        ATTR_PALETTE: [list(c) for c in data.palette],
        ATTR_PALETTE_HEX: [rgb_to_hex(c) for c in data.palette],
        ATTR_LIGHT_COLORS: {
            light: rgb_to_hex(rgb) for light, rgb in data.light_colors.items()
        },
        ATTR_LAST_APPLIED: data.last_applied.isoformat() if data.last_applied else None,
    }


@dataclass(frozen=True, kw_only=True)
class ZoneSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SyncState], str | None]
    attributes_fn: Callable[[SyncState], dict[str, Any]] | None = None


SENSORS: tuple[ZoneSensorDescription, ...] = (
    ZoneSensorDescription(
        key="current_color",
        translation_key="current_color",
        value_fn=lambda d: rgb_to_hex(d.rgb_color) if d.rgb_color else None,
        attributes_fn=_color_attributes,
    ),
    ZoneSensorDescription(
        key="color_source",
        translation_key="color_source",
        device_class=SensorDeviceClass.ENUM,
        options=[s.value for s in ColorSource],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.source.value,
    ),
)


class ZoneSensor(LightZoneEntity, SensorEntity):
    entity_description: ZoneSensorDescription

    def __init__(self, zone: LightZone, description: ZoneSensorDescription) -> None:
        super().__init__(zone, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)


# ---- selects ---------------------------------------------------------------------


def preset_for_sources(sources: list[str]) -> str | None:
    """The preset matching a colour source list, or ``None`` (custom).

    ``default`` is implied at the end, so ``[poster]`` matches
    ``artwork_only``.
    """
    normalized = [str(s) for s in sources]
    if ColorSource.DEFAULT not in normalized:
        normalized.append(ColorSource.DEFAULT)
    for preset, preset_sources in COLOR_SOURCE_PRESETS.items():
        if normalized == [str(s) for s in preset_sources]:
            return preset
    return None


@dataclass(frozen=True, kw_only=True)
class ZoneSelectDescription(SelectEntityDescription):
    current_fn: Callable[[dict[str, Any]], str | None]
    changes_fn: Callable[[str], dict[str, Any]]


SELECTS: tuple[ZoneSelectDescription, ...] = (
    ZoneSelectDescription(
        key="color_source_preset",
        translation_key="color_source_preset",
        entity_category=EntityCategory.CONFIG,
        options=list(COLOR_SOURCE_PRESETS),
        current_fn=lambda o: preset_for_sources(list(o[CONF_COLOR_SOURCES])),
        changes_fn=lambda v: {
            CONF_COLOR_SOURCES: [str(s) for s in COLOR_SOURCE_PRESETS[v]]
        },
    ),
    ZoneSelectDescription(
        key=CONF_EXTRACTION_METHOD,
        translation_key=CONF_EXTRACTION_METHOD,
        entity_category=EntityCategory.CONFIG,
        options=[m.value for m in ExtractionMethod],
        current_fn=lambda o: str(o[CONF_EXTRACTION_METHOD]),
        changes_fn=lambda v: {CONF_EXTRACTION_METHOD: v},
    ),
    ZoneSelectDescription(
        key=CONF_COLOR_MODE,
        translation_key=CONF_COLOR_MODE,
        entity_category=EntityCategory.CONFIG,
        options=[m.value for m in SyncColorMode],
        current_fn=lambda o: str(o[CONF_COLOR_MODE]),
        changes_fn=lambda v: {CONF_COLOR_MODE: v},
    ),
    ZoneSelectDescription(
        key=CONF_PALETTE_STRATEGY,
        translation_key=CONF_PALETTE_STRATEGY,
        entity_category=EntityCategory.CONFIG,
        options=[s.value for s in PaletteStrategy],
        current_fn=lambda o: str(o[CONF_PALETTE_STRATEGY]),
        changes_fn=lambda v: {CONF_PALETTE_STRATEGY: v},
    ),
)


class ZoneSelect(LightZoneEntity, SelectEntity):
    entity_description: ZoneSelectDescription

    def __init__(self, zone: LightZone, description: ZoneSelectDescription) -> None:
        super().__init__(zone, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        value = self.entity_description.current_fn(self.coordinator.options)
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self._ensure_allowed()
        await self.coordinator.async_update_options(
            self.entity_description.changes_fn(option)
        )


# ---- numbers ---------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ZoneNumberDescription(NumberEntityDescription):
    option: str
    as_int: bool = False


NUMBERS: tuple[ZoneNumberDescription, ...] = (
    ZoneNumberDescription(
        key=CONF_BRIGHTNESS_PCT,
        translation_key=CONF_BRIGHTNESS_PCT,
        option=CONF_BRIGHTNESS_PCT,
        as_int=True,
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
    ),
    ZoneNumberDescription(
        key=CONF_TRANSITION,
        translation_key=CONF_TRANSITION,
        option=CONF_TRANSITION,
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=0,
        native_max_value=30,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
    ),
)


class ZoneNumber(LightZoneEntity, NumberEntity):
    entity_description: ZoneNumberDescription

    def __init__(self, zone: LightZone, description: ZoneNumberDescription) -> None:
        super().__init__(zone, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float:
        return float(self.coordinator.options[self.entity_description.option])

    async def async_set_native_value(self, value: float) -> None:
        await self._ensure_allowed()
        description = self.entity_description
        new: float | int = round(value) if description.as_int else float(value)
        await self.coordinator.async_update_options({description.option: new})


# ---- buttons ---------------------------------------------------------------------


async def _save_title(zone: LightZone) -> None:
    await zone.async_save_current_title_color()


async def _resync(zone: LightZone) -> None:
    """Look again now; fail the press only if no light could be set.

    One offline light isn't an error: it's listed in the Syncing sensor's
    ``failed_lights`` / ``last_error``.
    """
    if await zone.async_resync() is SyncOutcome.FAILED:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="resync_failed",
            translation_placeholders={"error": zone.data.last_error or "unknown error"},
        )


@dataclass(frozen=True, kw_only=True)
class ZoneButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[LightZone], Awaitable[None]]


BUTTONS: tuple[ZoneButtonDescription, ...] = (
    ZoneButtonDescription(
        key="save_title_color", translation_key="save_title_color", press_fn=_save_title
    ),
    ZoneButtonDescription(key="resync", translation_key="resync", press_fn=_resync),
)


class ZoneButton(LightZoneEntity, ButtonEntity):
    entity_description: ZoneButtonDescription

    def __init__(self, zone: LightZone, description: ZoneButtonDescription) -> None:
        super().__init__(zone, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self._ensure_allowed()
        await self.entity_description.press_fn(self.coordinator)


# ---- per platform ------------------------------------------------------------------

FACTORIES: dict[str, Callable[[LightZone], list[Entity]]] = {
    "binary_sensor": lambda zone: [SyncingBinarySensor(zone)],
    "button": lambda zone: [ZoneButton(zone, d) for d in BUTTONS],
    "number": lambda zone: [ZoneNumber(zone, d) for d in NUMBERS],
    "select": lambda zone: [ZoneSelect(zone, d) for d in SELECTS],
    "sensor": lambda zone: [ZoneSensor(zone, d) for d in SENSORS],
    "switch": lambda zone: [SyncEnabledSwitch(zone)],
}
