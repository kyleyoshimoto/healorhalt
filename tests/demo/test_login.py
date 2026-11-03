import pytest

from healorhalt.locator import L

from .pantry import Pantry

pytestmark = pytest.mark.sim


def test_valid_login_shows_items(pantry: Pantry) -> None:
    pantry.log_in()
    pantry.app.wait_for_screen("list")
    assert pantry.app.label(L("list.count")) == "5 items"


def test_wrong_password_shows_error(pantry: Pantry) -> None:
    pantry.log_in(password="wrong")
    assert pantry.app.label(L("login.error")) == "Incorrect username or password"
    pantry.app.wait_for_screen("login")


def test_sign_in_needs_both_fields(pantry: Pantry) -> None:
    app = pantry.app
    app.wait_for_screen("login")
    assert not app.is_enabled(L("login.submit"))
    app.type(L("login.username"), "demo")
    assert not app.is_enabled(L("login.submit"))
    app.type(L("login.password"), "pantry123")
    assert app.is_enabled(L("login.submit"))
