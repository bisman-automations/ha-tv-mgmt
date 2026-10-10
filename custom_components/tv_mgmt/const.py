"""Constants for TV Mgmt."""

from __future__ import annotations

DOMAIN = "tv_mgmt"

CONF_MEDIA_PLAYER = "media_player"

# Option sections (config/options form groups)
SECTION_INPUT_LOCK = "input_lock"
SECTION_SCREEN_TIME = "screen_time"
SECTION_SYNC = "sync"
SECTION_APPLE_TV = "apple_tv"
SECTION_ANNOUNCE = "announcements"

# Input lock
CONF_ALLOWED_SOURCES = "allowed_sources"
CONF_TARGET_SOURCE = "target_source"
CONF_REVERT_DELAY = "revert_delay"
CONF_ENFORCE_ON_POWER_ON = "enforce_on_power_on"
CONF_MAX_ATTEMPTS = "max_attempts"
CONF_INPUT_NAMES = "input_names"  # {raw input: display name}

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

# Apple TV (streaming box) linked to this TV
CONF_STREAMING_PLAYER = "streaming_player"
CONF_APPLE_TV_INPUT = "apple_tv_input"  # the TV input the Apple TV is on
FOLLOW_DELAY = 3  # seconds after the Apple TV wakes before switching the TV to it
CONF_APP_MODE = "app_mode"  # block / allow
CONF_APPS = "apps"  # apps blocked (block mode) or allowed (allow mode)
CONF_APP_LIMITS = "app_limits"  # {app: minutes}
CONF_APP_ACTION = "app_action"  # home / sleep
CONF_SLEEP_ON_BLOCK = "sleep_on_block"
DEFAULT_SLEEP_ON_BLOCK = True
CONF_WAKE_WITH_TV = "wake_with_tv"
CONF_ANNOUNCE_TTS = "announce_tts"
CONF_ANNOUNCE_PLAYERS = "announce_players"
CONF_ANNOUNCE_SCREEN = "announce_screen"
CONF_ANNOUNCE_AIRPLAY = "announce_airplay"
ANNOUNCE_GRACE = 10  # seconds between "the TV is turning off" and turning it off
DEFAULT_WAKE_WITH_TV = True
APP_STOP_DELAY = 2  # seconds before stopping an app that isn't allowed
APPLE_TV_DOMAIN = "apple_tv"

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
SERVICE_SEND_MESSAGE = "send_message"
ATTR_PROFILE_ID = "profile_id"
ATTR_MINUTES = "minutes"

# Events
EVENT_INPUT_BLOCKED = f"{DOMAIN}_input_blocked"
EVENT_ENFORCEMENT_CHANGED = f"{DOMAIN}_enforcement_changed"
EVENT_WARNING = f"{DOMAIN}_warning"
EVENT_APP_BLOCKED = f"{DOMAIN}_app_blocked"

SIGNAL_UPDATED = f"{DOMAIN}_updated_{{}}"
SIGNAL_ANY_UPDATED = f"{DOMAIN}_any_updated"
