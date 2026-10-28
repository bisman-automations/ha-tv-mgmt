"""Repairs when a profile's TV, Apple TV, speaker or light is removed or renamed."""

from __future__ import annotations

from custom_components.tv_mgmt.access import async_get_access
from custom_components.tv_mgmt.const import DOMAIN
from custom_components.tv_mgmt.diagnostics import async_get_config_entry_diagnostics
from custom_components.tv_mgmt.light_sync.storage import DATA_LIGHT_SYNC
from homeassistant.components.repairs import DOMAIN as REPAIRS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component
from tests.ha.light_sync.conftest import (
    LIGHT_COLOR,
    LIGHT_WHITE,
    MEDIA_PLAYER,
    ZONE_ID,
    zone_config,
)
from tests.ha.test_apple_tv import BOX, set_box, setup_box
from tests.ha.test_integration import TV, set_tv, setup


def _issues(issue_registry: ir.IssueRegistry) -> list[ir.IssueEntry]:
    return [
        issue
        for issue in issue_registry.issues.values()
        if issue.domain == DOMAIN and issue.translation_key.startswith("entity_")
    ]


async def _repairs(hass: HomeAssistant):
    assert await async_setup_component(hass, REPAIRS, {})
    return hass.data[REPAIRS]["flow_manager"]


async def _start(flow, issue_id: str, user_id: str | None = None):
    """Start a fix flow on 2026.8 (issue id in data) and newer (in context)."""
    context: dict[str, str] = {"issue_id": issue_id}
    if user_id is not None:
        context["user_id"] = user_id
    return await flow.async_init(DOMAIN, context=context, data={"issue_id": issue_id})


def _register(
    entity_registry: er.EntityRegistry, domain: str, object_id: str, platform: str
) -> str:
    entry = entity_registry.async_get_or_create(
        domain,
        platform,
        object_id,
        suggested_object_id=object_id,
    )
    return entry.entity_id


async def test_removed_tv_raises_a_fixable_repair(
    hass: HomeAssistant,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Removing the TV's media player raises an error repair."""
    assert (
        _register(entity_registry, "media_player", "family_room_tv", "androidtv") == TV
    )
    set_tv(hass)
    entry = await setup(hass)
    assert _issues(issue_registry) == []

    entity_registry.async_remove(TV)
    await hass.async_block_till_done()

    issues = _issues(issue_registry)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.is_fixable
    assert issue.severity == ir.IssueSeverity.ERROR
    assert issue.translation_key == "entity_removed"
    assert issue.data["entry_id"] == entry.entry_id
    assert issue.data["entity_id"] == TV
    assert issue.data["location"] == "data"
    assert issue.translation_placeholders["profile"] == "Family Room TV"
    assert issue.translation_placeholders["role"] == "TV"


async def test_renamed_apple_tv_suggests_the_new_entity(
    hass: HomeAssistant,
    calls,
    box_calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Renaming the Apple TV raises a repair with the new id filled in."""
    set_tv(hass)
    set_box(hass)
    entry = await setup_box(hass)
    assert entity_registry.async_get(BOX) is not None

    entity_registry.async_update_entity(BOX, new_entity_id="media_player.den_apple_tv")
    await hass.async_block_till_done()

    issue = next(
        item for item in _issues(issue_registry) if item.data["location"] == "apple_tv"
    )
    assert issue.translation_key == "entity_renamed"
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.data["entity_id"] == BOX
    assert issue.data["suggested"] == "media_player.den_apple_tv"
    assert issue.translation_placeholders["suggested"] == "media_player.den_apple_tv"
    assert entry.runtime_data.box.entity_id == BOX  # still the stored id


async def test_fix_flow_replaces_the_apple_tv(
    hass: HomeAssistant,
    calls,
    box_calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The repair picks the renamed Apple TV and clears the issue."""
    set_tv(hass)
    set_box(hass)
    entry = await setup_box(hass)
    entity_registry.async_update_entity(BOX, new_entity_id="media_player.den_apple_tv")
    hass.states.async_set(
        "media_player.den_apple_tv",
        "idle",
        {"friendly_name": "Den Apple TV"},
    )
    await hass.async_block_till_done()
    issue = next(
        item for item in _issues(issue_registry) if item.data["location"] == "apple_tv"
    )

    flow = await _repairs(hass)
    result = await _start(flow, issue.issue_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await flow.async_configure(
        result["flow_id"],
        {"replacement": "media_player.den_apple_tv", "remove": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.options["apple_tv"]["streaming_player"] == "media_player.den_apple_tv"
    assert entry.runtime_data.box.entity_id == "media_player.den_apple_tv"
    assert issue_registry.async_get_issue(DOMAIN, issue.issue_id) is None


async def test_fix_flow_removes_a_tv_light(
    hass: HomeAssistant,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Removing a TV light takes it off the profile without reloading."""
    light = "light.tv_backlight"
    assert _register(entity_registry, "light", "tv_backlight", "hue") == light
    hass.states.async_set(light, "off", {"friendly_name": "TV backlight"})
    set_tv(hass)
    entry = await setup(
        hass,
        options={
            "input_lock": {
                "allowed_sources": ["HDMI 2"],
                "revert_delay": 1,
                "enforce_on_power_on": True,
                "max_attempts": 5,
            },
            "screen_time": {
                "daily_budget": 0,
                "warn_minutes": 5,
                "quiet_windows": "",
                "adult_mode_duration": 120,
            },
            "lights": {"tv_lights": [light], "tv_lights_follow": True},
        },
    )
    entity_registry.async_remove(light)
    await hass.async_block_till_done()
    issue = next(
        item for item in _issues(issue_registry) if item.data["entity_id"] == light
    )
    assert issue.data["location"] == "tv_lights"
    assert issue.translation_placeholders["role"] == "light"

    reloads: list[str] = []
    real = hass.config_entries.async_reload

    async def _counting(entry_id: str) -> bool:
        reloads.append(entry_id)
        return await real(entry_id)

    hass.config_entries.async_reload = _counting
    flow = await _repairs(hass)
    result = await _start(flow, issue.issue_id)
    result = await flow.async_configure(result["flow_id"], {"remove": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.options["lights"]["tv_lights"] == []
    assert entry.runtime_data.tv_lights == []
    assert reloads == []
    assert issue_registry.async_get_issue(DOMAIN, issue.issue_id) is None


async def test_removing_the_tv_deletes_the_profile(
    hass: HomeAssistant,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Confirming removal of the TV deletes the profile."""
    assert (
        _register(entity_registry, "media_player", "family_room_tv", "androidtv") == TV
    )
    set_tv(hass)
    entry = await setup(hass)
    entity_registry.async_remove(TV)
    await hass.async_block_till_done()
    issue = _issues(issue_registry)[0]

    flow = await _repairs(hass)
    result = await _start(flow, issue.issue_id)
    result = await flow.async_configure(result["flow_id"], {"remove": True})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_remove"
    result = await flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert issue_registry.async_get_issue(DOMAIN, issue.issue_id) is None


async def test_entity_coming_back_clears_the_repair(
    hass: HomeAssistant,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Putting the same entity back clears the repair without a fix flow."""
    assert (
        _register(entity_registry, "media_player", "family_room_tv", "androidtv") == TV
    )
    set_tv(hass)
    await setup(hass)
    entity_registry.async_remove(TV)
    await hass.async_block_till_done()
    assert _issues(issue_registry)

    # The same entity id is registered again. The state is cleared first so
    # the id is free, the way removing an entity frees it.
    hass.states.async_remove(TV)
    assert (
        _register(entity_registry, "media_player", "family_room_tv", "androidtv") == TV
    )
    set_tv(hass)
    await hass.async_block_till_done()
    assert _issues(issue_registry) == []


async def test_unrelated_entity_is_ignored(
    hass: HomeAssistant,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    set_tv(hass)
    await setup(hass)
    other = _register(entity_registry, "light", "kitchen", "hue")
    entity_registry.async_remove(other)
    await hass.async_block_till_done()
    assert _issues(issue_registry) == []


async def test_removed_speaker_and_zone_entities(
    hass: HomeAssistant,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A warning speaker and a zone's light and extra player each get a repair."""
    speaker = "media_player.room_speaker"
    assert (
        _register(entity_registry, "media_player", "room_speaker", "sonos") == speaker
    )
    assert _register(entity_registry, "light", "tv_backlight", "hue") == LIGHT_COLOR
    assert _register(entity_registry, "light", "floor_lamp", "hue") == LIGHT_WHITE
    assert (
        _register(
            entity_registry, "media_player", "living_room_player", "music_assistant"
        )
        == MEDIA_PLAYER
    )
    set_tv(hass)
    hass.states.async_set(speaker, "idle", {"friendly_name": "Room speaker"})
    hass.states.async_set(LIGHT_COLOR, "off")
    hass.states.async_set(LIGHT_WHITE, "off")
    hass.states.async_set(MEDIA_PLAYER, "idle")
    entry = await setup(
        hass,
        options={
            "input_lock": {
                "allowed_sources": ["HDMI 2"],
                "revert_delay": 1,
                "enforce_on_power_on": True,
                "max_attempts": 5,
            },
            "screen_time": {
                "daily_budget": 0,
                "warn_minutes": 5,
                "quiet_windows": "",
                "adult_mode_duration": 120,
            },
            "announcements": {
                "announce_tts": "tts.piper",
                "announce_players": [speaker],
            },
        },
    )
    await hass.data[DATA_LIGHT_SYNC].config.async_save_zone(
        entry.entry_id, zone_config()
    )
    await entry.runtime_data.light_sync.async_save_zone(
        hass.data[DATA_LIGHT_SYNC].config.zone(entry.entry_id, ZONE_ID)
    )
    from custom_components.tv_mgmt.repairs import async_reconcile_entities

    async_reconcile_entities(hass)
    assert _issues(issue_registry) == []

    entity_registry.async_remove(speaker)
    entity_registry.async_remove(LIGHT_COLOR)
    entity_registry.async_remove(MEDIA_PLAYER)
    await hass.async_block_till_done()
    by_entity = {issue.data["entity_id"]: issue for issue in _issues(issue_registry)}
    assert set(by_entity) == {speaker, LIGHT_COLOR, MEDIA_PLAYER}
    assert by_entity[speaker].data["location"] == "announce"
    assert by_entity[speaker].translation_placeholders["role"] == "speaker"
    assert by_entity[LIGHT_COLOR].data["location"] == "zone_light"
    assert by_entity[LIGHT_COLOR].data["zone_id"] == ZONE_ID
    assert by_entity[MEDIA_PLAYER].data["location"] == "zone_player"

    # Replacing the zone light updates storage and the running zone.
    flow = await _repairs(hass)
    assert (
        _register(entity_registry, "light", "other_lamp", "hue") == "light.other_lamp"
    )
    hass.states.async_set("light.other_lamp", "off")
    result = await _start(flow, by_entity[LIGHT_COLOR].issue_id)
    result = await flow.async_configure(
        result["flow_id"], {"replacement": "light.other_lamp", "remove": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    zone = hass.data[DATA_LIGHT_SYNC].config.zone(entry.entry_id, ZONE_ID)
    assert zone["lights"] == ["light.other_lamp", LIGHT_WHITE]
    assert (
        issue_registry.async_get_issue(DOMAIN, by_entity[LIGHT_COLOR].issue_id) is None
    )

    # Removing the speaker drops it. Diagnostics still hide that name.
    result = await _start(flow, by_entity[speaker].issue_id)
    result = await flow.async_configure(result["flow_id"], {"remove": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.options["announcements"]["announce_players"] == []
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "room_speaker" not in repr(diag)
    assert "sams" not in repr(diag)


async def test_missing_entity_on_startup(
    hass: HomeAssistant, calls, issue_registry: ir.IssueRegistry
) -> None:
    """A TV that isn't in Home Assistant at startup raises a repair."""
    entry = await setup(hass)
    issues = _issues(issue_registry)
    assert len(issues) == 1
    assert issues[0].data["entity_id"] == TV
    assert issues[0].data["entry_id"] == entry.entry_id


async def test_child_cannot_fix_a_repair_and_a_parent_can(
    hass: HomeAssistant,
    hass_admin_user,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Access matches the sidebar: a child is refused, an allowed parent is not."""
    light = "light.tv_backlight"
    assert _register(entity_registry, "light", "tv_backlight", "hue") == light
    hass.states.async_set(light, "off")
    set_tv(hass)
    await setup(
        hass,
        options={
            "input_lock": {
                "allowed_sources": ["HDMI 2"],
                "revert_delay": 1,
                "enforce_on_power_on": True,
                "max_attempts": 5,
            },
            "screen_time": {
                "daily_budget": 0,
                "warn_minutes": 5,
                "quiet_windows": "",
                "adult_mode_duration": 120,
            },
            "lights": {"tv_lights": [light]},
        },
    )
    entity_registry.async_remove(light)
    await hass.async_block_till_done()
    issue = _issues(issue_registry)[0]
    child = await hass.auth.async_create_user("Kid", group_ids=["system-users"])
    parent = await hass.auth.async_create_user("Parent", group_ids=["system-users"])
    assert not child.is_admin
    assert not parent.is_admin
    await (await async_get_access(hass)).async_set([parent.id])

    flow = await _repairs(hass)
    refused = await _start(flow, issue.issue_id, child.id)
    assert refused["type"] is FlowResultType.ABORT
    assert refused["reason"] == "not_allowed"
    assert issue_registry.async_get_issue(DOMAIN, issue.issue_id) is not None

    allowed = await _start(flow, issue.issue_id, parent.id)
    assert allowed["type"] is FlowResultType.FORM


async def test_fix_flow_rejects_a_bad_replacement(
    hass: HomeAssistant,
    calls,
    box_calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The form stays up when the choice is empty, the old id, or the wrong kind."""
    set_tv(hass)
    set_box(hass)
    await setup_box(hass)
    entity_registry.async_update_entity(BOX, new_entity_id="media_player.den_apple_tv")
    await hass.async_block_till_done()
    issue = next(
        item for item in _issues(issue_registry) if item.data["location"] == "apple_tv"
    )
    flow = await _repairs(hass)
    first = await _start(flow, issue.issue_id)

    empty = await flow.async_configure(first["flow_id"], {"remove": False})
    assert empty["type"] is FlowResultType.FORM
    assert empty["errors"]["base"] == "pick_or_remove"

    same = await flow.async_configure(
        empty["flow_id"], {"replacement": BOX, "remove": False}
    )
    assert same["errors"]["base"] == "still_missing"

    # A light is rejected by the media-player selector before our own check.
    # A media player from another integration is the same domain, so the
    # selector allows it and the Apple TV check refuses it.
    assert (
        _register(entity_registry, "media_player", "other_box", "androidtv")
        == "media_player.other_box"
    )
    hass.states.async_set("media_player.other_box", "idle")
    not_apple = await flow.async_configure(
        same["flow_id"], {"replacement": "media_player.other_box", "remove": False}
    )
    assert not_apple["errors"]["base"] == "not_apple_tv"
    assert issue_registry.async_get_issue(DOMAIN, issue.issue_id) is not None


async def test_tv_replacement_rejected_when_another_profile_has_it(
    hass: HomeAssistant,
    calls,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    assert (
        _register(entity_registry, "media_player", "family_room_tv", "androidtv") == TV
    )
    assert (
        _register(entity_registry, "media_player", "other_tv", "androidtv")
        == "media_player.other_tv"
    )
    set_tv(hass)
    hass.states.async_set(
        "media_player.other_tv",
        "on",
        {"friendly_name": "Other TV", "source_list": ["HDMI 1"]},
    )
    await setup(hass)
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    other = MockConfigEntry(
        domain=DOMAIN,
        title="Other TV",
        unique_id="media_player.other_tv",
        data={"media_player": "media_player.other_tv"},
        options={},
    )
    other.add_to_hass(hass)
    entity_registry.async_remove(TV)
    await hass.async_block_till_done()
    issue = next(
        item for item in _issues(issue_registry) if item.data["entity_id"] == TV
    )
    flow = await _repairs(hass)
    result = await _start(flow, issue.issue_id)
    result = await flow.async_configure(
        result["flow_id"],
        {"replacement": "media_player.other_tv", "remove": False},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "already_configured"
