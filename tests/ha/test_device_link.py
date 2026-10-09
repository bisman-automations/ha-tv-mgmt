from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from tests.ha.test_integration import set_tv, setup



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
