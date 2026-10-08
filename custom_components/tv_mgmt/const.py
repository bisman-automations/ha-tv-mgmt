"""Constants for TV Mgmt."""

from __future__ import annotations

DOMAIN = "tv_mgmt"

CONF_MEDIA_PLAYER = "media_player"

# Option sections (config/options form groups)
SECTION_INPUT_LOCK = "input_lock"
SECTION_SCREEN_TIME = "screen_time"
SECTION_SYNC = "sync"

# Input lock
CONF_ALLOWED_SOURCES = "allowed_sources"
CONF_TARGET_SOURCE = "target_source"
CONF_REVERT_DELAY = "revert_delay"
CONF_ENFORCE_ON_POWER_ON = "enforce_on_power_on"
CONF_MAX_ATTEMPTS = "max_attempts"

DEFAULT_REVERT_DELAY = 2  # seconds
DEFAULT_ENFORCE_ON_POWER_ON = True
DEFAULT_MAX_ATTEMPTS = 5

# Screen time
CONF_DAILY_BUDGET = "daily_budget"  # minutes, 0 = unlimited
CONF_WARN_MINUTES = "warn_minutes"
CONF_QUIET_WINDOWS = "quiet_windows"
CONF_ADULT_MODE_DURATION = "adult_mode_duration"  # minutes

DEFAULT_DAILY_BUDGET = 0
DEFAULT_WARN_MINUTES = 5
DEFAULT_QUIET_WINDOWS = ""
DEFAULT_ADULT_MODE_DURATION = 120

# Sync with Apple TV Mgmt
APPLETV_MGMT_DOMAIN = "appletv_mgmt"
CONF_MODE_SYNC_ENTITY = "mode_sync_entity"

# Input lock: window in which max_attempts is counted, so a TV that refuses
# to switch doesn't get hammered forever.
ATTEMPT_WINDOW = 60  # seconds

# How often usage is counted and the clock-based rules re-checked.
TICK_INTERVAL = 30  # seconds
# Wait after the TV turns on before turning it off again while blocked.
TURN_OFF_DELAY = 3  # seconds
# Don't send turn_off more often than this.
TURN_OFF_COOLDOWN = 15  # seconds

HDMI_INPUTS = ["HDMI 1", "HDMI 2", "HDMI 3", "HDMI 4"]

# Services
SERVICE_GRANT_EXTENSION = "grant_extension"
SERVICE_FORCE_BLOCK = "force_block"
SERVICE_UNBLOCK = "unblock"
SERVICE_RESET_USAGE = "reset_usage"
ATTR_PROFILE_ID = "profile_id"
ATTR_MINUTES = "minutes"

# Events
EVENT_INPUT_BLOCKED = f"{DOMAIN}_input_blocked"
EVENT_ENFORCEMENT_CHANGED = f"{DOMAIN}_enforcement_changed"
EVENT_WARNING = f"{DOMAIN}_warning"

SIGNAL_UPDATED = f"{DOMAIN}_updated_{{}}"
