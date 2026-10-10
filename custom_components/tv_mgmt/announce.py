"""Tell the room when TV time is running out, by voice and on the TV screen.

Speaks through a text-to-speech entity on the media players you pick, shows a
message on screen through a notify service, such as Home Assistant's
Notifications for Android TV / Fire TV integration or LG webOS's, and can
AirPlay a short still video with the message to the Apple TV.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .state import REASON_BUDGET, REASON_MANUAL, REASON_QUIET

_LOGGER = logging.getLogger(__name__)

TITLE = "TV Mgmt"
REPEAT_AFTER = 60  # seconds: don't repeat the same message more often than this
# Still videos AirPlayed to the Apple TV, served with the sidebar app's files.
SLIDE_ALMOST_UP = "almost-up"
SLIDE_TIME_UP = "time-up"
SLIDE_QUIET = "quiet-time"
SLIDES_PATH = "/tv_mgmt_static/airplay"

# Notify services that aren't a screen.
NOT_SCREENS = {"notify", "send_message", "persistent_notification"}


def warning_message(minutes: int) -> str:
    minutes = max(1, int(minutes))
    return f"{minutes} minute{'s' if minutes != 1 else ''} of TV time left."


# Minutes with their own "N minutes of TV time left" video (see scripts/make_airplay_videos.py).
WARN_SLIDE_MINUTES = {*range(1, 16), 20, 25, 30, 45, 60}


def warning_slide(minutes: int) -> str:
    minutes = max(1, int(minutes))
    return f"left-{minutes}" if minutes in WARN_SLIDE_MINUTES else SLIDE_ALMOST_UP


def turning_off_slide(reason: str | None) -> str:
    return SLIDE_QUIET if reason == REASON_QUIET else SLIDE_TIME_UP


def turning_off_message(reason: str | None, quiet_window: str | None = None) -> str:
    if reason == REASON_QUIET:
        if quiet_window:
            return f"It's {quiet_window} time. The TV is turning off."
        return "It's quiet time. The TV is turning off."
    if reason == REASON_MANUAL:
        return "A parent turned off TV time. The TV is turning off."
    if reason == REASON_BUDGET:
        return "TV time is up for today. The TV is turning off."
    return "The TV is turning off."


def screen_targets(hass: HomeAssistant) -> list[str]:
    """Notify entities and services that could show text on a screen."""
    targets = [
        f"notify.{name}"
        for name in sorted(hass.services.async_services_for_domain("notify"))
        if name not in NOT_SCREENS
    ]
    targets += [s.entity_id for s in hass.states.async_all("notify") if s.entity_id not in targets]
    return targets


class Announcer:
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        tts_entity: str | None,
        players: list[str],
        screen: str | None,
        airplay: str | None = None,
    ) -> None:
        self.hass = hass
        self.airplay = airplay or None
        self.tts_entity = tts_entity or None
        self.players = [p for p in players or [] if p]
        self.screen = screen or None
        self._last: dict[str, datetime] = {}

    @property
    def speaks(self) -> bool:
        return bool(self.tts_entity and self.players)

    @property
    def enabled(self) -> bool:
        return self.speaks or bool(self.screen) or bool(self.airplay)

    @callback
    def announce(self, message: str, slide: str | None = None, *, airplay_ok: bool = True) -> bool:
        """Say and show a message. Returns True if anything was sent.

        With a slide, and when the Apple TV is on (airplay_ok), it's also
        AirPlayed to the Apple TV.
        """
        if not self.enabled:
            return False
        now = dt_util.utcnow()
        last = self._last.get(message)
        if last and (now - last).total_seconds() < REPEAT_AFTER:
            return False
        self._last[message] = now
        if self.speaks:
            self._call(
                "tts", "speak",
                {
                    ATTR_ENTITY_ID: self.tts_entity,
                    "media_player_entity_id": self.players,
                    "message": message,
                },
            )
        if self.airplay and slide and airplay_ok and (url := self._slide_url(slide)):
            self._call(
                "media_player", "play_media",
                {ATTR_ENTITY_ID: self.airplay, "media_content_id": url, "media_content_type": "video"},
            )
        if self.screen:
            domain, _, name = self.screen.partition(".")
            if self.hass.states.get(self.screen) is not None:
                # A notify entity.
                self._call(domain, "send_message", {ATTR_ENTITY_ID: self.screen, "message": message, "title": TITLE})
            else:
                # A notify service, like notify.family_room_tv.
                self._call(domain, name, {"message": message, "title": TITLE})
        return True

    def _slide_url(self, slide: str) -> str | None:
        # The Apple TV fetches it itself, so it needs Home Assistant's local address.
        try:
            base = get_url(self.hass, allow_cloud=False, prefer_external=False)
        except NoURLAvailableError:
            _LOGGER.warning("TV Mgmt can't AirPlay a message: Home Assistant has no local URL set")
            return None
        return f"{base}{SLIDES_PATH}/{slide}.mp4"

    def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("TV Mgmt can't announce: %s.%s isn't available", domain, service)
            return

        async def _send() -> None:
            try:
                await self.hass.services.async_call(domain, service, data, blocking=True)
            except Exception as err:  # noqa: BLE001 - an announcement must never break enforcement
                _LOGGER.warning("TV Mgmt couldn't announce with %s.%s: %s", domain, service, err)

        self.hass.async_create_task(_send())
