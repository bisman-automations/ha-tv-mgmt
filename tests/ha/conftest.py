"""Fixtures for tests that run inside Home Assistant."""

import pathlib

import pytest


REPO_COMPONENTS = str(pathlib.Path(__file__).parents[2] / "custom_components")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    # The test harness ships its own custom_components package; add ours.
    import custom_components

    if REPO_COMPONENTS not in custom_components.__path__:
        custom_components.__path__.append(REPO_COMPONENTS)
    yield
