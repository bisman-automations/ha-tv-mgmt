"""Adapters that drive a TV through the HA integration that already owns it.

Most TV integrations expose `source` / `source_list` and `select_source`
on their media_player, and the generic adapter handles those (LG webOS,
Roku, SmartThings, Sony Bravia, Vizio, ...). A few integrations need
special handling, picked by the entity's platform.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .base import TVBackend
from .media_player import MediaPlayerBackend


def entity_platform(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry else None


def create_backend(hass: HomeAssistant, entity_id: str) -> TVBackend:
    platform = entity_platform(hass, entity_id)

    if platform == "androidtv_remote":
        from .android_tv_remote import AndroidTVRemoteBackend

        return AndroidTVRemoteBackend(hass, entity_id)
    if platform == "androidtv":
        from .androidtv_adb import AndroidTVADBBackend

        return AndroidTVADBBackend(hass, entity_id)
    if platform == "samsungtv":
        from .samsungtv import SamsungTVBackend

        return SamsungTVBackend(hass, entity_id)
    return MediaPlayerBackend(hass, entity_id)


__all__ = ["MediaPlayerBackend", "TVBackend", "create_backend", "entity_platform"]
