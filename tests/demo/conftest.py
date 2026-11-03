"""Fixtures for the Pantry demo-app UI tests."""

import pytest

from healorhalt.session import App

from .pantry import Pantry


@pytest.fixture
def pantry(app: App) -> Pantry:
    """The app at the login screen."""
    return Pantry(app)


@pytest.fixture
def logged_in(pantry: Pantry) -> Pantry:
    """The app signed in, on the item list."""
    pantry.log_in()
    pantry.app.wait_for_screen("list")
    return pantry
