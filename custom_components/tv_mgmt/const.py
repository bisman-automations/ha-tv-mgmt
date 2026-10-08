"""Constants for TV Management."""

from __future__ import annotations

DOMAIN = "tv_mgmt"

CONF_MEDIA_PLAYER = "media_player"

# Lock settings (stored in options)
CONF_ALLOWED_SOURCES = "allowed_sources"
CONF_TARGET_SOURCE = "target_source"
CONF_REVERT_DELAY = "revert_delay"
CONF_ENFORCE_ON_POWER_ON = "enforce_on_power_on"
CONF_MAX_ATTEMPTS = "max_attempts"

DEFAULT_REVERT_DELAY = 2  # seconds
DEFAULT_ENFORCE_ON_POWER_ON = True
DEFAULT_MAX_ATTEMPTS = 5

# Window in which max_attempts is counted, so a TV that refuses to switch
# doesn't get hammered forever.
ATTEMPT_WINDOW = 60  # seconds

HDMI_INPUTS = ["HDMI 1", "HDMI 2", "HDMI 3", "HDMI 4"]

EVENT_INPUT_BLOCKED = f"{DOMAIN}_blocked"
SIGNAL_STATE_UPDATED = f"{DOMAIN}_state_updated_{{}}"
