"""Fixtures for tests that run inside Home Assistant."""

import pathlib

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component


REPO_COMPONENTS = str(pathlib.Path(__file__).parents[2] / "custom_components")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    # The test harness ships its own custom_components package; add ours.
    import custom_components

    if REPO_COMPONENTS not in custom_components.__path__:
        custom_components.__path__.append(REPO_COMPONENTS)
    yield


@pytest.fixture
async def calls(hass):
    """Record TV service calls.

    Load media_player first so its real services don't replace the mocks.
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "media_player", {})
    return {
        "select_source": async_mock_service(hass, "media_player", "select_source"),
        "turn_off": async_mock_service(hass, "media_player", "turn_off"),
    }


@pytest.fixture
async def box_calls(hass, entity_registry: er.EntityRegistry, device_registry: dr.DeviceRegistry):
    """An Apple TV device with a media player and remote, like HA's integration."""
    atv_entry = MockConfigEntry(domain="apple_tv")
    atv_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=atv_entry.entry_id, identifiers={("apple_tv", "atv1")}, name="Living Room Apple TV"
    )
    entity_registry.async_get_or_create(
        "media_player", "apple_tv", "atv1", config_entry=atv_entry, device_id=device.id,
        suggested_object_id="living_room_apple_tv",
    )
    entity_registry.async_get_or_create(
        "remote", "apple_tv", "atv1", config_entry=atv_entry, device_id=device.id,
        suggested_object_id="living_room_apple_tv",
    )
    assert await async_setup_component(hass, "remote", {})
    return {"remote": async_mock_service(hass, "remote", "send_command")}
