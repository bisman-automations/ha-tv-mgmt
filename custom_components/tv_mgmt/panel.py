"""Registers the TV Mgmt sidebar panel."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "tv-mgmt"
STATIC_URL = "/tv_mgmt_static"
PANEL_COMPONENT = "tv-mgmt-panel"
FRONTEND_DIR = Path(__file__).parent / "frontend"
_REGISTERED = f"{DOMAIN}_panel_registered"


def _version() -> str:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    return manifest.get("version", "0")


async def async_register_panel(hass: HomeAssistant) -> None:
    """Add the sidebar entry. Needs the frontend, which every real install has."""
    if hass.data.get(_REGISTERED):
        return
    if "frontend" not in hass.config.components or hass.http is None:
        _LOGGER.debug("Frontend not loaded; not adding the TV Mgmt sidebar panel")
        return

    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    version = await hass.async_add_executor_job(_version)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="TV Mgmt",
        sidebar_icon="mdi:television-shimmer",
        # The version busts the browser cache after an update.
        module_url=f"{STATIC_URL}/tv-mgmt-panel.js?v={version}",
        require_admin=True,
        config={},
    )
    hass.data[_REGISTERED] = True
