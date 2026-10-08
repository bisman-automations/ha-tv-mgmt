"""Sensors: screen time, enforcement state, current input, blocked switches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TVMgmtConfigEntry
from .entity import TVMgmtEntity
from .manager import TVManager
from .state import STATES


@dataclass(frozen=True, kw_only=True)
class TVMgmtSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TVManager], Any]
    attrs_fn: Callable[[TVManager], dict[str, Any]] | None = None


def _minutes(seconds: int | None) -> float | None:
    return None if seconds is None else round(seconds / 60, 1)


TV_OFF = "TV off"


def _current_input(m: TVManager) -> str | None:
    """What's on screen, "TV off" when it's off, unknown if HA can't tell."""
    if m.backend.is_on is None:
        return None
    if not m.backend.is_on:
        return TV_OFF
    return m.backend.current_source


SENSORS: tuple[TVMgmtSensorDescription, ...] = (
    TVMgmtSensorDescription(
        key="enforcement_state",
        device_class=SensorDeviceClass.ENUM,
        options=STATES,
        value_fn=lambda m: m.decision.state,
        attrs_fn=lambda m: {
            "reason": m.decision.reason,
            "quiet_window": m.decision.quiet_window,
            "mode": m.state.mode,
            "force_block": m.state.force_block,
        },
    ),
    TVMgmtSensorDescription(
        key="time_used_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda m: _minutes(m.state.used_seconds),
    ),
    TVMgmtSensorDescription(
        key="time_remaining_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        # Unknown when there's no daily limit.
        value_fn=lambda m: _minutes(m.decision.remaining_seconds),
        attrs_fn=lambda m: {"daily_budget": m.daily_budget or None},
    ),
    TVMgmtSensorDescription(
        key="extension_today",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda m: m.state.extension_minutes,
    ),
    TVMgmtSensorDescription(
        key="current_input",
        value_fn=lambda m: _current_input(m),
        attrs_fn=lambda m: {
            "allowed": m.guard.is_allowed(m.backend.current_source) if m.backend.is_on else None
        },
    ),
    TVMgmtSensorDescription(
        key="blocked_switches_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda m: m.state.blocked_switches,
        attrs_fn=lambda m: {
            "last_blocked_input": m.state.last_blocked_source,
            "last_blocked_at": m.state.last_blocked_at,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TVMgmtConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    manager = entry.runtime_data
    async_add_entities(TVMgmtSensor(manager, description) for description in SENSORS)


class TVMgmtSensor(TVMgmtEntity, SensorEntity):
    entity_description: TVMgmtSensorDescription

    def __init__(self, manager: TVManager, description: TVMgmtSensorDescription) -> None:
        super().__init__(manager, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.manager)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.manager)
