"""Adapter for HA's Android TV Remote integration (androidtv_remote).

Its media_player has no source list, only `app_id` (the foreground app
package). HDMI inputs show up as an app package that depends on the TV
maker, so allowed sources here are app packages, and the config flow
captures the package while the TV is on the allowed input.

Switching to "HDMI n" sends KEYCODE_TV_INPUT_HDMI_n through the
integration's remote entity; any other target is launched as an app.
"""

from __future__ import annotations

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.components.remote import (
    ATTR_COMMAND,
    DOMAIN as REMOTE_DOMAIN,
    SERVICE_SEND_COMMAND,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import callback

from ..const import HDMI_INPUTS
from .media_player import MediaPlayerBackend

ATTR_APP_ID = "app_id"


def hdmi_number(label: str) -> str | None:
    return label.split()[-1] if label in HDMI_INPUTS else None


class AndroidTVRemoteBackend(MediaPlayerBackend):
    kind = "android_tv_remote"
    targets_are_keys = True

    def __init__(self, hass, entity_id) -> None:
        super().__init__(hass, entity_id)
        self._seen_apps: set[str] = set()

    @property
    def current_source(self) -> str | None:
        return self.attr(ATTR_APP_ID)

    @property
    def source_list(self) -> list[str]:
        if app := self.current_source:
            self._seen_apps.add(app)
        return sorted(self._seen_apps)

    @property
    def target_list(self) -> list[str]:
        return [*HDMI_INPUTS, *self.source_list]

    async def async_select_source(self, source: str) -> None:
        if (number := hdmi_number(source)) and (remote := self.sibling_entity(REMOTE_DOMAIN)):
            await self.hass.services.async_call(
                REMOTE_DOMAIN,
                SERVICE_SEND_COMMAND,
                {ATTR_ENTITY_ID: remote, ATTR_COMMAND: [f"KEYCODE_TV_INPUT_HDMI_{number}"]},
                blocking=False,
            )
            return
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: self.entity_id,
                ATTR_MEDIA_CONTENT_TYPE: "app",
                ATTR_MEDIA_CONTENT_ID: source,
            },
            blocking=False,
        )

    @callback
    def _on_change(self, _event) -> None:
        if app := self.current_source:
            self._seen_apps.add(app)
        self._notify()
