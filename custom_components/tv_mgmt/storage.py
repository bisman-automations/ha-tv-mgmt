"""Per-profile persistent state: today's usage and parent controls."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .state import MODE_ENFORCED

STORAGE_VERSION = 1
SAVE_DELAY = 10  # seconds


@dataclass
class ProfileState:
    # Controls that survive restarts
    mode: str = MODE_ENFORCED
    input_lock: bool = True
    adult_mode_until: str | None = None  # ISO datetime (UTC)
    force_block: bool = False

    # Today's counters
    day: str = field(default_factory=lambda: date.today().isoformat())
    used_seconds: int = 0
    extension_minutes: int = 0
    blocked_switches: int = 0
    last_blocked_source: str | None = None
    last_blocked_at: str | None = None

    def reset_day(self, day: date) -> None:
        """Start a new day. Extensions and manual blocks don't carry over."""
        self.day = day.isoformat()
        self.used_seconds = 0
        self.extension_minutes = 0
        self.blocked_switches = 0
        self.force_block = False

    @property
    def adult_mode_until_dt(self) -> datetime | None:
        return datetime.fromisoformat(self.adult_mode_until) if self.adult_mode_until else None


class ProfileStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.state = ProfileState()

    async def async_load(self) -> ProfileState:
        data = await self._store.async_load()
        if data:
            known = ProfileState.__dataclass_fields__
            self.state = ProfileState(**{k: v for k, v in data.items() if k in known})
        return self.state

    def schedule_save(self) -> None:
        self._store.async_delay_save(lambda: asdict(self.state), SAVE_DELAY)

    async def async_save(self) -> None:
        await self._store.async_save(asdict(self.state))

    async def async_remove(self) -> None:
        await self._store.async_remove()
