from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from tests.ha.test_integration import set_tv, setup

# Home Assistant 2026.8 gave each device a single config entry; devices from
# different integrations that share an identifier show as linked devices.
NEW_MODEL = hasattr(dr.DeviceRegistry, "async_get_devices")
old_model_only = pytest.mark.skipif(NEW_MODEL, reason="Home Assistant before 2026.8")
new_model_only = pytest.mark.skipif(not NEW_MODEL, reason="Home Assistant 2026.8 or newer")



@old_model_only
async def test_links_to_tv_device_both_ways(
    hass: HomeAssistant, calls, device_registry: dr.DeviceRegistry, entity_registry: er.EntityRegistry
) -> None:
    tv_entry = MockConfigEntry(domain="androidtv_remote")
    tv_entry.add_to_hass(hass)
    tv_device = device_registry.async_get_or_create(
        config_entry_id=tv_entry.entry_id,
        identifiers={("androidtv_remote", "aa:bb")},
        name="Family Room TV",
    )
    entity_registry.async_get_or_create(
        "media_player", "androidtv_remote", "aa:bb", config_entry=tv_entry,
        device_id=tv_device.id, suggested_object_id="family_room_tv",
    )
    set_tv(hass)
    entry = await setup(hass)

    # TV Mgmt's device is connected via the TV's device...
    [profile_device] = [
        d for d in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        if d.id != tv_device.id
    ]
    assert profile_device.name == "TV Mgmt — Family Room TV"
    assert profile_device.via_device_id == tv_device.id
    # ...and the TV's device lists TV Mgmt.
    tv_device = device_registry.async_get(tv_device.id)
    assert entry.entry_id in tv_device.config_entries
    assert tv_entry.entry_id in tv_device.config_entries

    # Reloading doesn't duplicate anything.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(dr.async_entries_for_config_entry(device_registry, entry.entry_id)) == 2

    # Removing the profile removes the link but keeps the TV's device.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    tv_device = device_registry.async_get(tv_device.id)
    assert tv_device is not None
    assert entry.entry_id not in tv_device.config_entries


async def test_tv_without_device(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    entry = await setup(hass)
    assert entry.runtime_data is not None


@old_model_only
async def test_links_apple_tv_device(
    hass: HomeAssistant, calls, box_calls, device_registry: dr.DeviceRegistry
) -> None:
    from tests.ha.test_apple_tv import set_box, setup_box

    set_tv(hass)
    set_box(hass)
    entry = await setup_box(hass)
    atv_device = device_registry.async_get_device(identifiers={("apple_tv", "atv1")})
    assert entry.entry_id in atv_device.config_entries
    assert len(atv_device.config_entries) == 2

    # Unlinking the Apple TV in settings lets its device go, but keeps the device.
    options = {**entry.options, "apple_tv": {**entry.options["apple_tv"], "streaming_player": None}}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    atv_device = device_registry.async_get(atv_device.id)
    assert atv_device is not None
    assert entry.entry_id not in atv_device.config_entries
    # TV Mgmt's own device stays.
    assert device_registry.async_get_device(identifiers={("tv_mgmt", entry.entry_id)}) is not None


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
        "media_player", "androidtv_remote", "aa:bb", config_entry=tv_entry,
        device_id=tv_device.id, suggested_object_id="family_room_tv",
    )
    return tv_entry, tv_device


def _profile_device(device_registry, entry):
    return device_registry.async_get_device_by_identifier(("tv_mgmt", entry.entry_id), entry.entry_id)


def _linked(device_registry, device):
    return {
        d.id
        for d in device_registry.async_get_devices(identifiers=device.identifiers, connections=device.connections)
        if d.id != device.id
    }


@new_model_only
async def test_linked_devices(
    hass: HomeAssistant, calls, box_calls, device_registry: dr.DeviceRegistry, entity_registry: er.EntityRegistry
) -> None:
    from tests.ha.test_apple_tv import set_box, setup_box

    tv_entry, tv_device = _tv_device(hass, device_registry, entity_registry)
    [atv_device] = device_registry.async_get_devices(identifiers={("apple_tv", "atv1")})
    set_tv(hass)
    set_box(hass)
    entry = await setup_box(hass)

    profile = _profile_device(device_registry, entry)
    assert profile.name == "TV Mgmt — Family Room TV"
    # TV Mgmt shows under the TV's and the Apple TV's Linked devices, and they under its.
    assert _linked(device_registry, profile) == {tv_device.id, atv_device.id}
    assert profile.id in _linked(device_registry, device_registry.async_get(tv_device.id))
    assert profile.id in _linked(device_registry, device_registry.async_get(atv_device.id))
    # The TV's and the Apple TV's devices are untouched and still their integrations' own.
    tv_device = device_registry.async_get(tv_device.id)
    assert tv_device.config_entry_id == tv_entry.entry_id
    assert tv_device.identifiers == {("androidtv_remote", "aa:bb")}
    # Looking up the TV's identifier from its own integration still finds the TV.
    assert device_registry.async_get_device_by_identifier(
        ("androidtv_remote", "aa:bb"), tv_entry.entry_id
    ).id == tv_device.id
    # Entities stay on TV Mgmt's device.
    entity = entity_registry.async_get("select.tv_mgmt_family_room_tv_mode")
    assert entity.device_id == profile.id

    # Reloading keeps the links and doesn't add devices.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert dr.async_entries_for_config_entry(device_registry, entry.entry_id) == [_profile_device(device_registry, entry)]
    assert _linked(device_registry, _profile_device(device_registry, entry)) == {tv_device.id, atv_device.id}

    # Unlinking the Apple TV in settings drops that link.
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    options = {**entry.options, "apple_tv": {**entry.options["apple_tv"], "streaming_player": None}}
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    assert _linked(device_registry, _profile_device(device_registry, entry)) == {tv_device.id}
    assert device_registry.async_get(atv_device.id) is not None
