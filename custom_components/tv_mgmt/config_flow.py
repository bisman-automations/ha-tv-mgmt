"""Config and options flow for TV Management."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import DOMAIN as MP_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .backends import TVBackend, create_backend
from .const import (
    CONF_ALLOWED_SOURCES,
    CONF_ENFORCE_ON_POWER_ON,
    CONF_MAX_ATTEMPTS,
    CONF_MEDIA_PLAYER,
    CONF_REVERT_DELAY,
    CONF_TARGET_SOURCE,
    DEFAULT_ENFORCE_ON_POWER_ON,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_REVERT_DELAY,
    DOMAIN,
    HDMI_INPUTS,
)

# Extra guidance shown in the form, per adapter.
NOTES = {
    "generic": "",
    "android_tv_remote": (
        "Android TV reports the foreground app, not the HDMI port. Switch the TV "
        "to the input you want to allow *before* opening this form so its app "
        "package is listed. Pick an HDMI number as the input to force back to."
    ),
    "android_tv_adb": (
        "Android TV reports the foreground app, not the HDMI port. Switch the TV "
        "to the input you want to allow *before* opening this form so its app "
        "package is listed. Pick an HDMI number as the input to force back to."
    ),
    "samsungtv": (
        "The Samsung integration can't see which input is on screen, so this "
        "forces the input each time the TV turns on but can't catch switches "
        "mid-session. For a full lock, set this up on the TV's SmartThings "
        "entity instead."
    ),
}


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


def _lock_schema(backend: TVBackend, defaults: Mapping[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    allowed_default = list(defaults.get(CONF_ALLOWED_SOURCES, []))

    if backend.reports_source:
        current = backend.current_source
        if not allowed_default and current:
            allowed_default = [current]
        sources = list(dict.fromkeys([*backend.source_list, *allowed_default]))
        targets = list(dict.fromkeys([*backend.target_list, *allowed_default]))
        fields[vol.Required(CONF_ALLOWED_SOURCES, default=allowed_default)] = _select(
            sources, multiple=True
        )
        fields[
            vol.Optional(
                CONF_TARGET_SOURCE,
                description={"suggested_value": defaults.get(CONF_TARGET_SOURCE)},
            )
        ] = _select(targets)
    else:
        fields[
            vol.Required(
                CONF_TARGET_SOURCE, default=defaults.get(CONF_TARGET_SOURCE, HDMI_INPUTS[0])
            )
        ] = _select(backend.target_list)

    fields[
        vol.Required(
            CONF_REVERT_DELAY, default=defaults.get(CONF_REVERT_DELAY, DEFAULT_REVERT_DELAY)
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=60, step=1, unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    fields[
        vol.Required(
            CONF_ENFORCE_ON_POWER_ON,
            default=defaults.get(CONF_ENFORCE_ON_POWER_ON, DEFAULT_ENFORCE_ON_POWER_ON),
        )
    ] = selector.BooleanSelector()
    fields[
        vol.Required(
            CONF_MAX_ATTEMPTS, default=defaults.get(CONF_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS)
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=20, step=1, mode=selector.NumberSelectorMode.BOX)
    )
    return vol.Schema(fields)


def _process(backend: TVBackend, user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate the lock form and return (options, errors)."""
    if not backend.reports_source:
        # Nothing to observe, so the only allowed input is the target.
        return {**user_input, CONF_ALLOWED_SOURCES: [user_input[CONF_TARGET_SOURCE]]}, {}

    allowed = user_input.get(CONF_ALLOWED_SOURCES) or []
    target = user_input.get(CONF_TARGET_SOURCE)
    if not allowed:
        return user_input, {CONF_ALLOWED_SOURCES: "no_sources"}
    key_target = backend.targets_are_keys and target in HDMI_INPUTS
    if target and target not in allowed and not key_target:
        return user_input, {CONF_TARGET_SOURCE: "target_not_allowed"}
    if backend.targets_are_keys and not target:
        return user_input, {CONF_TARGET_SOURCE: "target_required"}
    return user_input, {}


def _placeholders(backend: TVBackend) -> dict[str, str]:
    return {
        "entity": backend.entity_id,
        "current": backend.current_source or "unknown (is the TV on?)",
        "note": NOTES.get(backend.kind, ""),
    }


class TVMgmtConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._entity_id: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._entity_id = user_input[CONF_MEDIA_PLAYER]
            await self.async_set_unique_id(self._entity_id)
            self._abort_if_unique_id_configured()
            return await self.async_step_lock()

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

    async def async_step_lock(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._entity_id is not None
        backend = create_backend(self.hass, self._entity_id)
        errors: dict[str, str] = {}

        if user_input is not None:
            options, errors = _process(backend, user_input)
            if not errors:
                state = self.hass.states.get(self._entity_id)
                title = state.name if state else self._entity_id
                return self.async_create_entry(
                    title=title, data={CONF_MEDIA_PLAYER: self._entity_id}, options=options
                )

        return self.async_show_form(
            step_id="lock",
            data_schema=_lock_schema(backend, user_input or {}),
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
        guard = getattr(entry, "runtime_data", None)
        backend = guard.backend if guard else create_backend(self.hass, entry.data[CONF_MEDIA_PLAYER])
        errors: dict[str, str] = {}

        if user_input is not None:
            options, errors = _process(backend, user_input)
            if not errors:
                return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_lock_schema(backend, user_input or entry.options),
            errors=errors,
            description_placeholders=_placeholders(backend),
        )
