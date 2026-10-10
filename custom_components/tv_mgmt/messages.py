"""Messages a parent sends from the dashboard: on the Apple TV, the TV screen, or speakers.

For the Apple TV, the message is drawn as a full-screen slide, turned into a
10-second still video with ffmpeg, and AirPlayed. Videos are cached by
message, under hard-to-guess names, and served to the Apple TV from
/tv_mgmt_messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
import shutil
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import instance_id
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MESSAGES_URL = "/tv_mgmt_messages"
MAX_FILES = 20
MAX_LENGTH = 120
_REGISTERED = f"{DOMAIN}_messages_path"

# Ready-made messages offered on the dashboard.
PRESETS = [
    "Dinner is ready",
    "Time to get ready for bed",
    "Come to the kitchen, please",
    "Time to turn off the TV",
    "Five more minutes",
]


def messages_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(".storage", "tv_mgmt_messages"))


async def async_register_messages_path(hass: HomeAssistant) -> None:
    if hass.data.get(_REGISTERED) or hass.http is None:
        return
    from homeassistant.components.http import StaticPathConfig

    folder = messages_dir(hass)
    await hass.async_add_executor_job(lambda: folder.mkdir(parents=True, exist_ok=True))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(MESSAGES_URL, str(folder), cache_headers=False)]
    )
    hass.data[_REGISTERED] = True


def _ffmpeg(hass: HomeAssistant) -> str | None:
    try:
        from homeassistant.components.ffmpeg import get_ffmpeg_manager

        return get_ffmpeg_manager(hass).binary
    except (ImportError, ValueError):
        return shutil.which("ffmpeg")


def _build(out: Path, text: str, sub: str, ffmpeg: str) -> None:
    from . import slides

    if not out.exists():
        slides.to_video(slides.render(text, sub, slides.BLUE), out, ffmpeg)
    # Keep the newest few.
    videos = sorted(out.parent.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in videos[MAX_FILES:]:
        old.unlink(missing_ok=True)


async def async_message_video_url(hass: HomeAssistant, text: str, sub: str = "") -> str:
    """Make (or reuse) the video for a message. Returns the URL the Apple TV can fetch."""
    await async_register_messages_path(hass)
    try:
        base = get_url(hass, allow_cloud=False, prefer_external=False)
    except NoURLAvailableError as err:
        raise HomeAssistantError(
            "Home Assistant has no local address for the Apple TV to fetch the message from. "
            "Set one under Settings, System, Network."
        ) from err
    ffmpeg = _ffmpeg(hass)
    if not ffmpeg:
        raise HomeAssistantError("ffmpeg isn't available, so the message can't be made into a video.")
    secret = await instance_id.async_get(hass)
    key = hashlib.sha256(f"{secret}\n{text}\n{sub}".encode()).hexdigest()[:32]
    out = messages_dir(hass) / f"{key}.mp4"
    try:
        await hass.async_add_executor_job(_build, out, text, sub, ffmpeg)
    except Exception as err:  # noqa: BLE001 - show the parent why
        _LOGGER.warning("TV Mgmt couldn't make a video for a message: %s", err)
        raise HomeAssistantError(f"Couldn't make the message video: {err}") from err
    return f"{base}{MESSAGES_URL}/{key}.mp4"


async def async_send_message(
    hass: HomeAssistant,
    text: str,
    *,
    apple_tv: str | None = None,
    screen: str | None = None,
    tts_entity: str | None = None,
    players: list[str] | None = None,
    sub: str = "",
) -> list[str]:
    """Send a message everywhere asked. Returns where it went."""
    sent: list[str] = []
    calls: list[tuple[str, str, dict[str, Any], str]] = []
    if apple_tv:
        url = await async_message_video_url(hass, text, sub)
        calls.append(
            ("media_player", "play_media",
             {ATTR_ENTITY_ID: apple_tv, "media_content_id": url, "media_content_type": "video"}, "apple_tv")
        )
    if screen:
        domain, _, name = screen.partition(".")
        message = f"{text}\n{sub}" if sub else text
        if hass.states.get(screen) is not None:
            calls.append((domain, "send_message", {ATTR_ENTITY_ID: screen, "message": message, "title": "TV Mgmt"}, "screen"))
        else:
            calls.append((domain, name, {"message": message, "title": "TV Mgmt"}, "screen"))
    if tts_entity and players:
        calls.append(
            ("tts", "speak", {ATTR_ENTITY_ID: tts_entity, "media_player_entity_id": players, "message": text}, "speak")
        )
    errors: list[str] = []
    for domain, service, data, where in calls:
        if not hass.services.has_service(domain, service):
            errors.append(f"{domain}.{service} isn't available")
            continue
        try:
            if where == "apple_tv":
                # AirPlay waits until the video has played; report errors from the start only.
                task = hass.async_create_task(
                    hass.services.async_call(domain, service, data, blocking=True)
                )
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3)
                except TimeoutError:
                    pass  # Still playing.
            else:
                await hass.services.async_call(domain, service, data, blocking=True)
        except HomeAssistantError as err:
            errors.append(f"{domain}.{service}: {err}")
            continue
        sent.append(where)
    if errors and not sent:
        raise HomeAssistantError("; ".join(errors))
    for error in errors:
        _LOGGER.warning("TV Mgmt message: %s", error)
    return sent
