"""Config and options flow for TV Mgmt."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .backends import TVBackend, create_backend
from .const import (
    CONF_ADULT_MODE_DURATION,
    CONF_ALLOWED_SOURCES,
    CONF_DAILY_BUDGET,
    CONF_ENFORCE_ON_POWER_ON,
    CONF_MAX_ATTEMPTS,
    APPLETV_MGMT_DOMAIN,
    CONF_MEDIA_PLAYER,
    CONF_MODE_SYNC_ENTITY,
    CONF_QUIET_WINDOWS,
    CONF_REVERT_DELAY,
    CONF_TARGET_SOURCE,
    CONF_WARN_MINUTES,
    DEFAULT_ADULT_MODE_DURATION,
    DEFAULT_DAILY_BUDGET,
    DEFAULT_ENFORCE_ON_POWER_ON,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_QUIET_WINDOWS,
    DEFAULT_REVERT_DELAY,
    DEFAULT_WARN_MINUTES,
    DOMAIN,
    HDMI_INPUTS,
    SECTION_INPUT_LOCK,
    SECTION_SCREEN_TIME,
    SECTION_SYNC,
)
from .manager import flatten_options
from .mode_sync import suggest_mode_select
from .quiet import parse_windows

# Extra guidance shown in the form, per adapter.
NOTES = {
    "android_tv_remote": (
        "Android TV reports the foreground app, not the HDMI port. Switch the TV "
        "to the input you want to allow before opening this form so it's listed, "
        "then pick an HDMI number as the input to force back to."
    ),
    "samsungtv": (
        "The Samsung integration can't see which input is on screen, so the lock "
        "forces the input each time the TV turns on but can't catch switches "
        "mid-session. For a full lock, use the TV's SmartThings entity instead."
    ),
}
NOTES["android_tv_adb"] = NOTES["android_tv_remote"]


def _select(options: list[str], *, multiple: bool = False) -> selector.SelectSelector:
    """Dropdown that also accepts typed values (e.g. TV was off during setup)."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=multiple,
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _number(min_: int, max_: int, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=min_, max=max_, step=1, mode=selector.NumberSelectorMode.BOX
    )
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _input_lock_schema(backend: TVBackend, d: Mapping[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    allowed = list(d.get(CONF_ALLOWED_SOURCES, []))

    if backend.reports_source:
        if not allowed and (current := backend.current_source):
            allowed = [current]
        sources = list(dict.fromkeys([*backend.source_list, *allowed]))
        targets = list(dict.fromkeys([*backend.target_list, *allowed]))
        fields[vol.Required(CONF_ALLOWED_SOURCES, default=allowed)] = _select(
            sources, multiple=True
        )
        fields[
            vol.Optional(
                CONF_TARGET_SOURCE, description={"suggested_value": d.get(CONF_TARGET_SOURCE)}
            )
        ] = _select(targets)
    else:
        fields[
            vol.Required(CONF_TARGET_SOURCE, default=d.get(CONF_TARGET_SOURCE, HDMI_INPUTS[0]))
        ] = _select(backend.target_list)

    fields[vol.Required(CONF_REVERT_DELAY, default=d.get(CONF_REVERT_DELAY, DEFAULT_REVERT_DELAY))] = _number(0, 60, "s")
    fields[
        vol.Required(
            CONF_ENFORCE_ON_POWER_ON,
            default=d.get(CONF_ENFORCE_ON_POWER_ON, DEFAULT_ENFORCE_ON_POWER_ON),
        )
    ] = selector.BooleanSelector()
    fields[vol.Required(CONF_MAX_ATTEMPTS, default=d.get(CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS))] = _number(1, 20)
    return vol.Schema(fields)


def _screen_time_schema(d: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_DAILY_BUDGET, default=d.get(CONF_DAILY_BUDGET, DEFAULT_DAILY_BUDGET)
            ): _number(0, 1440, "min"),
            vol.Required(
                CONF_WARN_MINUTES, default=d.get(CONF_WARN_MINUTES, DEFAULT_WARN_MINUTES)
            ): _number(0, 60, "min"),
            vol.Optional(
                CONF_QUIET_WINDOWS,
                description={"suggested_value": d.get(CONF_QUIET_WINDOWS, DEFAULT_QUIET_WINDOWS)},
            ): selector.TextSelector(),
            vol.Required(
                CONF_ADULT_MODE_DURATION,
                default=d.get(CONF_ADULT_MODE_DURATION, DEFAULT_ADULT_MODE_DURATION),
            ): _number(5, 720, "min"),
        }
    )


def _sync_schema(hass: HomeAssistant, tv_entity_id: str, d: Mapping[str, Any]) -> vol.Schema:
    # Suggest the matching Apple TV Mgmt profile until the user has chosen
    # (a cleared choice is stored as None and stays cleared).
    if CONF_MODE_SYNC_ENTITY in d:
        suggested = d[CONF_MODE_SYNC_ENTITY]
    else:
        suggested = suggest_mode_select(hass, tv_entity_id)
    return vol.Schema(
        {
            vol.Optional(
                CONF_MODE_SYNC_ENTITY, description={"suggested_value": suggested}
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(integration=APPLETV_MGMT_DOMAIN, domain="select")
            ),
        }
    )


def _settings_schema(
    hass: HomeAssistant, backend: TVBackend, defaults: Mapping[str, Any]
) -> vol.Schema:
    flat = flatten_options(dict(defaults))
    return vol.Schema(
        {
            vol.Required(SECTION_INPUT_LOCK): section(_input_lock_schema(backend, flat)),
            vol.Required(SECTION_SCREEN_TIME): section(_screen_time_schema(flat)),
            vol.Optional(SECTION_SYNC, default={}): section(
                _sync_schema(hass, backend.entity_id, flat)
            ),
        }
    )


def _process(
    backend: TVBackend, user_input: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate the settings form. Returns (options, errors)."""
    lock = dict(user_input.get(SECTION_INPUT_LOCK, {}))
    screen = dict(user_input.get(SECTION_SCREEN_TIME, {}))
    errors: dict[str, str] = {}

    if backend.reports_source:
        allowed = lock.get(CONF_ALLOWED_SOURCES) or []
        target = lock.get(CONF_TARGET_SOURCE)
        key_target = backend.targets_are_keys and target in HDMI_INPUTS
        if not allowed:
            errors["base"] = "no_sources"
        elif target and target not in allowed and not key_target:
            errors["base"] = "target_not_allowed"
        elif backend.targets_are_keys and not target:
            errors["base"] = "target_required"
    else:
        # Nothing to observe, so the only allowed input is the target.
        lock[CONF_ALLOWED_SOURCES] = [lock[CONF_TARGET_SOURCE]]

    try:
        windows = parse_windows(screen.get(CONF_QUIET_WINDOWS))
    except ValueError:
        errors["base"] = "bad_quiet_windows"
    else:
        screen[CONF_QUIET_WINDOWS] = ", ".join(w.format() for w in windows)

    for key in (CONF_REVERT_DELAY, CONF_MAX_ATTEMPTS):
        if key in lock:
            lock[key] = int(lock[key])
    for key in (CONF_DAILY_BUDGET, CONF_WARN_MINUTES, CONF_ADULT_MODE_DURATION):
        if key in screen:
            screen[key] = int(screen[key])

    sync = {CONF_MODE_SYNC_ENTITY: user_input.get(SECTION_SYNC, {}).get(CONF_MODE_SYNC_ENTITY) or None}

    return {SECTION_INPUT_LOCK: lock, SECTION_SCREEN_TIME: screen, SECTION_SYNC: sync}, errors


def _placeholders(backend: TVBackend) -> dict[str, str]:
    return {
        "entity": backend.entity_id,
        "current": backend.current_source or "unknown (is the TV on?)",
        "note": NOTES.get(backend.kind, ""),
    }


class TVMgmtConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._entity_id: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._entity_id = user_input[CONF_MEDIA_PLAYER]
            await self.async_set_unique_id(self._entity_id)
            self._abort_if_unique_id_configured()
            return await self.async_step_settings()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MEDIA_PLAYER): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=MP_DOMAIN)
                    )
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._entity_id is not None
        backend = create_backend(self.hass, self._entity_id)
        errors: dict[str, str] = {}

        if user_input is not None:
            options, errors = _process(backend, user_input)
            if not errors:
                state = self.hass.states.get(self._entity_id)
                return self.async_create_entry(
                    title=state.name if state else self._entity_id,
                    data={CONF_MEDIA_PLAYER: self._entity_id},
                    options=options,
                )

        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(self.hass, backend, user_input or {}),
            errors=errors,
            description_placeholders=_placeholders(backend),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TVMgmtOptionsFlow()


class TVMgmtOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        manager = getattr(entry, "runtime_data", None)
        backend = manager.backend if manager else create_backend(self.hass, entry.data[CONF_MEDIA_PLAYER])
        errors: dict[str, str] = {}

        if user_input is not None:
            options, errors = _process(backend, user_input)
            if not errors:
                return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(self.hass, backend, user_input or entry.options),
            errors=errors,
            description_placeholders=_placeholders(backend),
        )
