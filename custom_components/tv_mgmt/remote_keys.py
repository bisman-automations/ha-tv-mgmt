"""Remote buttons for the sidebar app's dashboard.

Every TV integration names its remote buttons differently, so each button
is mapped to the Home Assistant service that presses it on that brand.
Buttons a device can't press aren't offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.components.remote import ATTR_COMMAND, DOMAIN as REMOTE_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_STANDBY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

POWER = "power"
VOLUME_UP = "volume_up"
VOLUME_DOWN = "volume_down"
MUTE = "mute"
NAV_KEYS = ("up", "down", "left", "right", "select", "back", "home")

DEVICE_TV = "tv"
DEVICE_APPLE_TV = "apple_tv"

# Navigation keys per TV integration: (how they're pressed, key names).
#   "remote" -> remote.send_command on the device's remote entity
#   "webostv" -> webostv.button on the media player
#   "adb"     -> androidtv.adb_command on the media player
NAV_BY_PLATFORM: dict[str, tuple[str, dict[str, str]]] = {
    "androidtv_remote": ("remote", {
        "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
        "select": "DPAD_CENTER", "back": "BACK", "home": "HOME",
    }),
    "androidtv": ("adb", {
        "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
        "select": "CENTER", "back": "BACK", "home": "HOME",
    }),
    "roku": ("remote", {
        "up": "up", "down": "down", "left": "left", "right": "right",
        "select": "select", "back": "back", "home": "home",
    }),
    "webostv": ("webostv", {
        "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
        "select": "ENTER", "back": "BACK", "home": "HOME",
    }),
    "samsungtv": ("remote", {
        "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
        "select": "KEY_ENTER", "back": "KEY_RETURN", "home": "KEY_HOME",
    }),
    "braviatv": ("remote", {
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "select": "Confirm", "back": "Return", "home": "Home",
    }),
    # The Apple TV's "back" is its Menu button.
    "apple_tv": ("remote", {
        "up": "up", "down": "down", "left": "left", "right": "right",
        "select": "select", "back": "menu", "home": "home",
    }),
}

# Apple TV volume goes through its remote (it drives the TV or receiver).
APPLE_TV_VOLUME = {VOLUME_UP: "volume_up", VOLUME_DOWN: "volume_down"}


@dataclass
class Press:
    domain: str
    service: str
    data: dict[str, Any]


def _remote_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.device_id is None:
        return None
    for other in er.async_entries_for_device(registry, entry.device_id):
        if other.domain == REMOTE_DOMAIN and not other.disabled:
            return other.entity_id
    return None


def _platform(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry else None


def presses(hass: HomeAssistant, entity_id: str) -> dict[str, Press]:
    """Every button this media player's device can press, mapped to a service call."""
    state = hass.states.get(entity_id)
    if state is None:
        return {}
    features = int(state.attributes.get("supported_features") or 0)
    platform = _platform(hass, entity_id)
    remote = _remote_entity(hass, entity_id)
    target = {ATTR_ENTITY_ID: entity_id}
    keys: dict[str, Press] = {}

    asleep = state.state in (STATE_OFF, STATE_STANDBY)
    if asleep and features & MediaPlayerEntityFeature.TURN_ON:
        keys[POWER] = Press(MEDIA_PLAYER_DOMAIN, "turn_on", target)
    elif not asleep and features & MediaPlayerEntityFeature.TURN_OFF:
        keys[POWER] = Press(MEDIA_PLAYER_DOMAIN, "turn_off", target)

    if platform == "apple_tv":
        if remote:
            for key, command in APPLE_TV_VOLUME.items():
                keys[key] = Press(REMOTE_DOMAIN, "send_command", {ATTR_ENTITY_ID: remote, ATTR_COMMAND: command})
    else:
        if features & (MediaPlayerEntityFeature.VOLUME_STEP | MediaPlayerEntityFeature.VOLUME_SET):
            keys[VOLUME_UP] = Press(MEDIA_PLAYER_DOMAIN, "volume_up", target)
            keys[VOLUME_DOWN] = Press(MEDIA_PLAYER_DOMAIN, "volume_down", target)
        if features & MediaPlayerEntityFeature.VOLUME_MUTE:
            muted = bool(state.attributes.get("is_volume_muted"))
            keys[MUTE] = Press(MEDIA_PLAYER_DOMAIN, "volume_mute", {**target, "is_volume_muted": not muted})

    if platform in NAV_BY_PLATFORM:
        how, names = NAV_BY_PLATFORM[platform]
        for key, name in names.items():
            if how == "remote" and remote:
                keys[key] = Press(REMOTE_DOMAIN, "send_command", {ATTR_ENTITY_ID: remote, ATTR_COMMAND: name})
            elif how == "webostv":
                keys[key] = Press("webostv", "button", {**target, "button": name})
            elif how == "adb":
                keys[key] = Press("androidtv", "adb_command", {**target, "command": name})
    return keys


def available_keys(hass: HomeAssistant, entity_id: str | None) -> list[str]:
    """Buttons to offer for this media player, whether it's on or off right now."""
    if not entity_id:
        return []
    keys = list(presses(hass, entity_id))
    state = hass.states.get(entity_id)
    if state is not None and POWER not in keys:
        features = int(state.attributes.get("supported_features") or 0)
        if features & (MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF):
            keys.insert(0, POWER)
    return keys
