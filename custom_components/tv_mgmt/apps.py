"""App rules for streaming boxes (Apple TV).

Pure module (no Home Assistant imports) so it can be unit tested alone.

Apps are identified by their bundle ID (com.google.ios.youtube) when the
box reports one, and rules match either the ID or the app's name, so a
rule typed as "YouTube" works too.

Per-app limits are written one per line, in minutes:

    YouTube = 30
    com.netflix.Netflix = 60
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What happens when an app isn't allowed
ACTION_HOME = "home"  # send the box back to its home screen
ACTION_SLEEP = "sleep"  # put the box to sleep
ACTIONS = [ACTION_HOME, ACTION_SLEEP]

# How the app list is used
APP_MODE_BLOCK = "block"  # listed apps are blocked
APP_MODE_ALLOW = "allow"  # only listed apps are allowed
APP_MODES = [APP_MODE_BLOCK, APP_MODE_ALLOW]

# Why an app was stopped
REASON_BLOCKED = "blocked"
REASON_NOT_ALLOWED = "not_allowed"
REASON_LIMIT = "limit"

# The home screen and system UI: never counted as app time, never blocked.
HOME_APP_IDS = frozenset(
    {
        "com.apple.HeadBoard",
        "com.apple.PineBoard",
        "com.apple.TVSystemUI",
        "com.apple.TVIdleScreen",
        "com.apple.TVAirPlay",
    }
)


def is_home(app_id: str | None) -> bool:
    return not app_id or app_id in HOME_APP_IDS


def parse_limits(text: str | None) -> dict[str, int]:
    """Parse "app = minutes" lines. Raises ValueError on a bad line."""
    limits: dict[str, int] = {}
    for number, line in enumerate((text or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        app, sep, minutes = line.rpartition("=")
        app, minutes = app.strip(), minutes.strip()
        if not sep or not app or not minutes.isdigit():
            raise ValueError(f"line {number}: expected 'app = minutes', got {line!r}")
        limits[app] = int(minutes)
    return limits


def format_limits(limits: dict[str, int] | None) -> str:
    return "\n".join(f"{app} = {minutes}" for app, minutes in (limits or {}).items())


def _matches(rule: str, app_id: str | None, app_name: str | None) -> bool:
    rule = rule.strip().casefold()
    return bool(rule) and rule in {(app_id or "").casefold(), (app_name or "").casefold()}


@dataclass
class AppRules:
    mode: str = APP_MODE_BLOCK
    apps: list[str] = field(default_factory=list)
    limits: dict[str, int] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.limits) or bool(self.apps) or self.mode == APP_MODE_ALLOW

    def limit_for(self, app_id: str | None, app_name: str | None) -> int | None:
        for rule, minutes in self.limits.items():
            if _matches(rule, app_id, app_name):
                return minutes
        return None

    def listed(self, app_id: str | None, app_name: str | None) -> bool:
        return any(_matches(rule, app_id, app_name) for rule in self.apps)

    def check(self, app_id: str | None, app_name: str | None, used_seconds: int) -> str | None:
        """Why this app must be stopped now, or None if it's fine."""
        if is_home(app_id):
            return None
        listed = self.listed(app_id, app_name)
        if self.mode == APP_MODE_BLOCK and listed:
            return REASON_BLOCKED
        if self.mode == APP_MODE_ALLOW and not listed:
            return REASON_NOT_ALLOWED
        limit = self.limit_for(app_id, app_name)
        if limit is not None and used_seconds >= limit * 60:
            return REASON_LIMIT
        return None
