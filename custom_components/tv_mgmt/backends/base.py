"""Base class for TV adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er


class TVBackend(ABC):
    """Reads a TV's power/input state and switches its input.

    Sources are plain labels ("HDMI 2", "Apple TV", an app ID). Each
    adapter maps them to whatever its integration's services need.
    """

    #: Shown in the config flow so users know what to expect.
    kind: str = "generic"

    #: False when the integration can't say which input is showing. The
    #: guard then forces the target input on every power-on instead.
    reports_source: bool = True

    #: True when targets can be things that aren't sources (e.g. "HDMI 2"
    #: on Android TV, where the current source is an app package).
    targets_are_keys: bool = False

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self._listeners: list[Callable[[], None]] = []

    @property
    @abstractmethod
    def is_on(self) -> bool | None:
        """Power state, None if unknown."""

    @property
    @abstractmethod
    def current_source(self) -> str | None:
        """Label of what's on screen, None if unknown."""

    @property
    def source_list(self) -> list[str]:
        """Labels that can be allowed."""
        return []

    @property
    def target_list(self) -> list[str]:
        """Labels the TV can be forced to."""
        return self.source_list

    @abstractmethod
    async def async_select_source(self, source: str) -> None:
        """Switch the TV to a source label."""

    async def async_start(self) -> None:
        """Start receiving updates."""

    async def async_stop(self) -> None:
        """Stop receiving updates."""

    # ---- helpers -------------------------------------------------------------

    def sibling_entity(self, domain: str) -> str | None:
        """Find another entity of `domain` on the same device (e.g. its remote)."""
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if entry is None or entry.device_id is None:
            return None
        for other in er.async_entries_for_device(registry, entry.device_id):
            if other.domain == domain and not other.disabled:
                return other.entity_id
        return None

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
