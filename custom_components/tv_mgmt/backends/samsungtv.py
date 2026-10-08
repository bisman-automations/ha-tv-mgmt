"""Adapter for HA's Samsung Smart TV integration (samsungtv).

That integration can't report which input or app is on screen; its
source list is just "TV", "HDMI" and installed apps. So this adapter
can't catch someone switching mid-session. It forces the target input
every time the TV turns on, via KEY_HDMIn on the integration's remote
entity.

For a full lock on a Samsung, use the TV's SmartThings media_player
instead: it reports the real input and goes through the generic adapter.
"""

from __future__ import annotations

from homeassistant.components.remote import (
    ATTR_COMMAND,
    DOMAIN as REMOTE_DOMAIN,
    SERVICE_SEND_COMMAND,
)
from homeassistant.const import ATTR_ENTITY_ID

from ..const import HDMI_INPUTS
from .media_player import MediaPlayerBackend


class SamsungTVBackend(MediaPlayerBackend):
    kind = "samsungtv"
    reports_source = False

    def __init__(self, hass, entity_id) -> None:
        super().__init__(hass, entity_id)
        self._assumed: str = HDMI_INPUTS[0]

    def set_assumed_source(self, source: str) -> None:
        self._assumed = source

    @property
    def current_source(self) -> str | None:
        return self._assumed if self.is_on else None

    @property
    def source_list(self) -> list[str]:
        return list(HDMI_INPUTS)

    async def async_select_source(self, source: str) -> None:
        remote = self.sibling_entity(REMOTE_DOMAIN)
        if source in HDMI_INPUTS and remote:
            await self.hass.services.async_call(
                REMOTE_DOMAIN,
                SERVICE_SEND_COMMAND,
                {ATTR_ENTITY_ID: remote, ATTR_COMMAND: [f"KEY_HDMI{source.split()[-1]}"]},
                blocking=False,
            )
        else:
            # Cycles to the next HDMI input; better than nothing.
            await super().async_select_source("HDMI")
        self._assumed = source
