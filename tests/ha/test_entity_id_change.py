"""Changing an entity's ID keeps it running (Home Assistant 2026.11+)."""

import importlib
import inspect
import pkgutil

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from tests.ha.test_integration import set_tv, setup

ADD_REMOVE = (
    "async_prepare_to_add_to_hass",
    "async_added_to_hass",
    "async_will_remove_from_hass",
)


def test_every_add_remove_class_has_the_hook() -> None:
    """Home Assistant re-adds any class that overrides these without the hook."""
    import custom_components.tv_mgmt as package

    classes = set()
    for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, Entity) and cls.__module__.startswith(package.__name__):
                classes.add(cls)
    assert classes
    missing = sorted(
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in classes
        if any(name in cls.__dict__ for name in ADD_REMOVE)
        and "async_entity_id_changed" not in cls.__dict__
    )
    assert not missing, f"missing async_entity_id_changed: {missing}"


@pytest.mark.skipif(
    not hasattr(Entity, "async_entity_id_changed"),
    reason="Home Assistant 2026.11 and newer change entity IDs in place",
)
async def test_renamed_entity_keeps_working(hass: HomeAssistant, calls) -> None:
    set_tv(hass)
    await setup(hass)
    old_id = "switch.tv_mgmt_family_room_tv_input_lock"
    new_id = "switch.renamed_input_lock"
    component = hass.data["entity_components"]["switch"]
    entity = component.get_entity(old_id)
    assert entity is not None
    await hass.services.async_call("switch", "turn_off", {"entity_id": old_id}, True)
    assert hass.states.get(old_id).state == "off"

    er.async_get(hass).async_update_entity(old_id, new_entity_id=new_id)
    await hass.async_block_till_done()

    assert hass.states.get(old_id) is None
    assert hass.states.get(new_id).state == "off"
    # Changed in place, not removed and added again.
    assert component.get_entity(new_id) is entity

    await hass.services.async_call("switch", "turn_on", {"entity_id": new_id}, True)
    await hass.async_block_till_done()
    assert hass.states.get(new_id).state == "on"
    assert hass.states.get(old_id) is None
