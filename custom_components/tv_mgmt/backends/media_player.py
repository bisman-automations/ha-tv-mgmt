"""Generic adapter: any media_player with source / select_source."""

from __future__ import annotations

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_SELECT_SOURCE,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .base import TVBackend


class MediaPlayerBackend(TVBackend):
    kind = "generic"

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        super().__init__(hass, entity_id)
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def state(self) -> State | None:
        return self.hass.states.get(self.entity_id)

    def attr(self, name: str):
        state = self.state
        return state.attributes.get(name) if state else None

    @property
    def is_on(self) -> bool | None:
        state = self.state
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state != STATE_OFF

    @property
    def current_source(self) -> str | None:
        return self.attr(ATTR_INPUT_SOURCE)

    @property
    def source_list(self) -> list[str]:
        return list(self.attr(ATTR_INPUT_SOURCE_LIST) or [])

    async def async_select_source(self, source: str) -> None:
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_SELECT_SOURCE,
            {ATTR_ENTITY_ID: self.entity_id, ATTR_INPUT_SOURCE: source},
            blocking=False,
        )

    async def async_start(self) -> None:
        self._unsub = async_track_state_change_event(
            self.hass, [self.entity_id], self._on_change
        )

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _on_change(self, _event) -> None:
        self._notify()
