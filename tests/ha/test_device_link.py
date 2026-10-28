from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.tv_mgmt as tv_mgmt
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from tests.ha.test_integration import DOMAIN, TV, set_tv, setup

# Home Assistant 2026.8 gives each device one config entry. Devices from
# different integrations that share an identifier show as linked devices.


def test_legacy_device_link_is_gone() -> None:
    """The pre-2026.8 via_device / config_entries path is not in the integration."""
    assert not hasattr(tv_mgmt, "_link_by_config_entry")
    assert not hasattr(tv_mgmt, "_shared_devices_model")


async def test_tv_without_device(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    assert entry.runtime_data is not None


def _tv_device(hass, device_registry, entity_registry):
    tv_entry = MockConfigEntry(domain="androidtv_remote")
    tv_entry.add_to_hass(hass)
    tv_device = device_registry.async_get_or_create(
        config_entry_id=tv_entry.entry_id,
        identifiers={("androidtv_remote", "aa:bb")},
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff")},
        name="Family Room TV",
    )
    entity_registry.async_get_or_create(
        "media_player",
        "androidtv_remote",
        "aa:bb",
        config_entry=tv_entry,
        device_id=tv_device.id,
        suggested_object_id="family_room_tv",
    )
    return tv_entry, tv_device


def _profile_device(device_registry, entry):
    return device_registry.async_get_device_by_identifier(
        ("tv_mgmt", entry.entry_id), entry.entry_id
    )


def _linked(device_registry, device):
    return {
        d.id
        for d in device_registry.async_get_devices(
            identifiers=device.identifiers, connections=device.connections
        )
        if d.id != device.id
    }


async def test_linked_devices(
    hass: HomeAssistant,
    calls,
    box_calls,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    from tests.ha.test_apple_tv import set_box, setup_box

    tv_entry, tv_device = _tv_device(hass, device_registry, entity_registry)
    [atv_device] = device_registry.async_get_devices(identifiers={("apple_tv", "atv1")})
    set_tv(hass)
    set_box(hass)
    entry = await setup_box(hass)

    profile = _profile_device(device_registry, entry)
    assert profile.name == "TV Mgmt — Family Room TV"
    # The profile is linked by shared identifiers, not nested via the TV.
    assert profile.via_device_id is None
    assert profile.config_entry_id == entry.entry_id
    # TV Mgmt shows under the TV's and the Apple TV's Linked devices, and they under its.
    assert _linked(device_registry, profile) == {tv_device.id, atv_device.id}
    assert profile.id in _linked(
        device_registry, device_registry.async_get(tv_device.id)
    )
    assert profile.id in _linked(
        device_registry, device_registry.async_get(atv_device.id)
    )
    # The TV's and the Apple TV's devices are untouched and still their integrations' own.
    tv_device = device_registry.async_get(tv_device.id)
    assert tv_device.config_entry_id == tv_entry.entry_id
    assert tv_device.identifiers == {("androidtv_remote", "aa:bb")}
    # Looking up the TV's identifier from its own integration still finds the TV.
    assert (
        device_registry.async_get_device_by_identifier(
            ("androidtv_remote", "aa:bb"), tv_entry.entry_id
        ).id
        == tv_device.id
    )
    # Entities stay on TV Mgmt's device.
    entity = entity_registry.async_get("select.tv_mgmt_family_room_tv_mode")
    assert entity.device_id == profile.id

    # Reloading keeps the links and doesn't add devices.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert dr.async_entries_for_config_entry(device_registry, entry.entry_id) == [
        _profile_device(device_registry, entry)
    ]
    assert _linked(device_registry, _profile_device(device_registry, entry)) == {
        tv_device.id,
        atv_device.id,
    }

    # Unlinking the Apple TV in settings drops that link.
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    options = {
        **entry.options,
        "apple_tv": {**entry.options["apple_tv"], "streaming_player": None},
    }
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert _linked(device_registry, _profile_device(device_registry, entry)) == {
        tv_device.id
    }
    assert device_registry.async_get(atv_device.id) is not None


async def test_removes_stale_copy_and_keeps_the_tv(
    hass: HomeAssistant,
    calls,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A device left on this profile by the 2026.8 split is removed.

    The TV's own device stays on the TV's integration. It is not moved onto
    the profile with new_config_entry_id.
    """
    tv_entry, tv_device = _tv_device(hass, device_registry, entity_registry)
    set_tv(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Family Room TV",
        version=2,
        unique_id=TV,
        data={"media_player": TV},
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
        },
    )
    entry.add_to_hass(hass)
    stale = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("tv_mgmt", "stale-copy")},
        name="Stale copy",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert device_registry.async_get(stale.id) is None
    tv_device = device_registry.async_get(tv_device.id)
    assert tv_device is not None
    assert tv_device.config_entry_id == tv_entry.entry_id
    profile = _profile_device(device_registry, entry)
    assert profile is not None
    assert profile.via_device_id is None
    assert ("androidtv_remote", "aa:bb") in profile.identifiers
