"""Display names for inputs.

Pure module (no Home Assistant imports) so it can be unit tested alone.

TVs report inputs as whatever their integration knows them by: "HDMI 2",
or on Android TV an app package like "com.tcl.tv". People can give any
input a display name; matching still uses the raw value.

In settings, names are written one per line:

    com.tcl.tv = Apple TV
    HDMI 1 = Xbox
"""

from __future__ import annotations

# Readable names for common Android TV / Google TV packages. User names win.
KNOWN_INPUT_NAMES: dict[str, str] = {
    "com.google.android.apps.tv.launcherx": "Google TV home",
    "com.google.android.tvlauncher": "Android TV home",
    "com.google.android.youtube.tv": "YouTube",
    "com.google.android.youtube.tvkids": "YouTube Kids",
    "com.netflix.ninja": "Netflix",
    "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.disney.disneyplus": "Disney+",
    "com.hulu.livingroomplus": "Hulu",
    "com.wbd.stream": "Max",
    "com.apple.atve.androidtv.appletv": "Apple TV app",
    "com.plexapp.android": "Plex",
    "com.spotify.tv.android": "Spotify",
    "tv.pluto.android": "Pluto TV",
    "com.google.android.tv": "Live TV",
}


def parse_names(text: str | None) -> dict[str, str]:
    """Parse "raw = Name" lines. Raises ValueError on a bad line."""
    names: dict[str, str] = {}
    for number, line in enumerate((text or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        raw, sep, name = line.partition("=")
        raw, name = raw.strip(), name.strip()
        if not sep or not raw or not name:
            raise ValueError(f"line {number}: expected 'input = name', got {line!r}")
        names[raw] = name
    return names


def format_names(names: dict[str, str] | None) -> str:
    return "\n".join(f"{raw} = {name}" for raw, name in (names or {}).items())


def clean_names(names: dict[str, str] | None) -> dict[str, str]:
    """Drop blanks and names that just repeat the raw value."""
    return {
        str(raw).strip(): str(name).strip()
        for raw, name in (names or {}).items()
        if str(raw).strip() and str(name).strip() and str(name).strip() != str(raw).strip()
    }


class InputNames:
    def __init__(self, user_names: dict[str, str] | None = None) -> None:
        self.user = clean_names(user_names)

    @property
    def all(self) -> dict[str, str]:
        return {**KNOWN_INPUT_NAMES, **self.user}

    def name(self, raw: str | None) -> str | None:
        if raw is None:
            return None
        return self.user.get(raw) or KNOWN_INPUT_NAMES.get(raw) or raw

    def label(self, raw: str) -> str:
        """For pickers: the name, with the raw value when they differ."""
        name = self.name(raw)
        return raw if name == raw else f"{name} ({raw})"
