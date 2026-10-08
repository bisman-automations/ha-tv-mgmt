"""Keep the Mode select in sync with an Apple TV Mgmt profile, both ways.

Both integrations use the same modes (enforced / monitor_only / paused), so
a change on either side is copied to the other. Copying stops as soon as the
two match, so there's no loop.

At startup Apple TV Mgmt's mode wins if they differ, unless TV Mgmt's mode
changed while Apple TV Mgmt was unavailable; then TV Mgmt's is pushed once
it's back.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import APPLETV_MGMT_DOMAIN
from .state import MODES

if TYPE_CHECKING:
    from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)

# Apple TV Mgmt option keys that name the TV a profile turns off.
APPLETV_MGMT_TV_KEY = "tv_entity_id"


def appletv_mgmt_mode_selects(hass: HomeAssistant) -> dict[str, str]:
    """Apple TV Mgmt mode selects, entity_id -> config entry id."""
    registry = er.async_get(hass)
    found: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(APPLETV_MGMT_DOMAIN):
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == SELECT_DOMAIN and entity.unique_id.endswith("_mode"):
                found[entity.entity_id] = entry.entry_id
    return found


def suggest_mode_select(hass: HomeAssistant, tv_entity_id: str) -> str | None:
    """Pick the Apple TV Mgmt profile that controls the same TV, or the only one."""
    selects = appletv_mgmt_mode_selects(hass)
    for entity_id, entry_id in selects.items():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and tv_entity_id in (
            entry.options.get(APPLETV_MGMT_TV_KEY),
            entry.data.get(APPLETV_MGMT_TV_KEY),
        ):
            return entity_id
    return next(iter(selects)) if len(selects) == 1 else None


def _mode_of(state: State | None) -> str | None:
    return state.state if state is not None and state.state in MODES else None


class ModeSync:
    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        get_mode: Callable[[], str],
        set_mode: Callable[[str], None],
    ) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self._get_mode = get_mode
        self._set_mode = set_mode
        self._pending_push = False
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def linked_mode(self) -> str | None:
        return _mode_of(self.hass.states.get(self.entity_id))

    @callback
    def async_start(self) -> None:
        self._unsub = async_track_state_change_event(
            self.hass, [self.entity_id], self._on_linked_change
        )
        if (theirs := self.linked_mode) is not None:
            self._adopt(theirs)

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def push(self, mode: str) -> None:
        """Our mode changed: copy it to Apple TV Mgmt."""
        theirs = self.linked_mode
        if theirs is None:
            self._pending_push = True
            return
        self._pending_push = False
        if theirs != mode:
            _LOGGER.debug("Mode sync: setting %s to %s", self.entity_id, mode)
            self.hass.async_create_task(
                self.hass.services.async_call(
                    SELECT_DOMAIN,
                    SERVICE_SELECT_OPTION,
                    {ATTR_ENTITY_ID: self.entity_id, ATTR_OPTION: mode},
                    blocking=False,
                )
            )

    @callback
    def _on_linked_change(self, event: Event[EventStateChangedData]) -> None:
        theirs = _mode_of(event.data["new_state"])
        if theirs is None:
            return
        if self._pending_push:
            # We changed while they were away; ours is newer.
            self.push(self._get_mode())
            return
        self._adopt(theirs)

    @callback
    def _adopt(self, theirs: str) -> None:
        if theirs != self._get_mode():
            _LOGGER.debug("Mode sync: following %s to %s", self.entity_id, theirs)
            self._set_mode(theirs)
