"""Diagnostics for TV Mgmt."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import TVMgmtConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TVMgmtConfigEntry
) -> dict[str, Any]:
    manager = entry.runtime_data
    backend = manager.backend
    tv_state = hass.states.get(manager.tv_entity_id)
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "profile_state": asdict(manager.state),
        "decision": asdict(manager.decision),
        "adapter": {
            "kind": backend.kind,
            "reports_source": backend.reports_source,
            "is_on": backend.is_on,
            "current_source": backend.current_source,
            "source_list": backend.source_list,
            "target_list": backend.target_list,
        },
        "input_lock": {
            "allowed": manager.guard.allowed_sources,
            "target": manager.guard.target_source,
            "paused_reason": manager.guard.paused_reason,
        },
        "quiet_windows": [w.format() for w in manager.quiet_windows],
        "tv_entity_state": tv_state.as_dict() if tv_state else None,
    }
