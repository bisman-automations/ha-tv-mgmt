"""Fixtures for tests that run inside Home Assistant."""

import pathlib

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service


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
