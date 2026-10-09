"""What's playing on the streaming box: the show, episode, movie or song.

Pure module (no Home Assistant imports) so it can be unit tested alone.
Works from a media player's standard attributes, as Home Assistant's Apple
TV integration reports them.
"""

from __future__ import annotations

import re
from typing import Any

# Some apps (Prime Video) put the show in the title and the episode in the
# artist: "Season 2, Ep. 14 Learn Vehicle Names with Transforming Robots!"
_EPISODE_IN_ARTIST = re.compile(
    r"^\s*(?:Season|S)\s*(\d+)\s*,?\s*(?:Episode|Ep\.?|E)\s*(\d+)\s*[:.\-–—]?\s*(.*)$", re.IGNORECASE
)

PLAYING = "playing"
PAUSED = "paused"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> int | str | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def media_from(state: str | None, attributes: dict[str, Any]) -> dict[str, Any] | None:
    """What's on screen, or None when nothing is playing or paused."""
    if state not in (PLAYING, PAUSED):
        return None
    title = _clean(attributes.get("media_title"))
    series = _clean(attributes.get("media_series_title"))
    artist = _clean(attributes.get("media_artist"))
    if not (title or series):
        return None
    media: dict[str, Any] = {"title": title}
    episode_in_artist = _EPISODE_IN_ARTIST.match(artist) if artist and not series else None
    if episode_in_artist and title:
        season, episode, episode_title = episode_in_artist.groups()
        media = {"series": title, "season": int(season), "episode": int(episode),
                 "title": _clean(episode_title) or title}
    elif series:
        media["series"] = series
        media["season"] = _number(attributes.get("media_season"))
        media["episode"] = _number(attributes.get("media_episode"))
    elif artist:
        media["artist"] = artist
        if album := _clean(attributes.get("media_album_name")):
            media["album"] = album
    return {k: v for k, v in media.items() if v is not None}


def show_of(media: dict[str, Any] | None) -> str | None:
    """What time is grouped under: the series, else the movie or song title."""
    if not media:
        return None
    return media.get("series") or media.get("title")


def media_key(media: dict[str, Any] | None) -> tuple | None:
    """Identity of one episode, movie or song, to tell when it changes."""
    if not media:
        return None
    return (media.get("series"), media.get("season"), media.get("episode"), media.get("title"), media.get("artist"))


def describe(media: dict[str, Any] | None) -> str | None:
    """One line, e.g. "Bluey, Season 2, Episode 14: Hammerbarn"."""
    if not media:
        return None
    if series := media.get("series"):
        parts = [series]
        se = ", ".join(
            p for p in (
                f"Season {media['season']}" if media.get("season") is not None else None,
                f"Episode {media['episode']}" if media.get("episode") is not None else None,
            ) if p
        )
        if se:
            parts.append(f", {se}")
        title = media.get("title")
        if title and title != series:
            parts.append(f": {title}")
        return "".join(parts)
    title = media.get("title") or ""
    if artist := media.get("artist"):
        return f"{title} by {artist}"
    return title
