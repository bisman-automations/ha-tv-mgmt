"""Adapter for HA's Android TV ADB integration (androidtv).

Like Android TV Remote, the current "source" is the foreground app
(`app_id`). "HDMI n" targets are sent as an ADB keyevent; anything else
goes through the integration's select_source, which launches apps.
"""

from __future__ import annotations

from homeassistant.const import ATTR_ENTITY_ID

from .android_tv_remote import ATTR_APP_ID, AndroidTVRemoteBackend, hdmi_number
from .media_player import MediaPlayerBackend


class AndroidTVADBBackend(AndroidTVRemoteBackend):
    kind = "android_tv_adb"

    @property
    def current_source(self) -> str | None:
        return self.attr(ATTR_APP_ID) or self.attr("source")

    async def async_select_source(self, source: str) -> None:
        if number := hdmi_number(source):
            await self.hass.services.async_call(
                "androidtv",
                "adb_command",
                {
                    ATTR_ENTITY_ID: self.entity_id,
                    "command": f"input keyevent KEYCODE_TV_INPUT_HDMI_{number}",
                },
                blocking=False,
            )
            return
        await MediaPlayerBackend.async_select_source(self, source)
