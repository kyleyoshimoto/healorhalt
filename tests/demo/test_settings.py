import pytest

from healorhalt.locator import L

from .pantry import SEEDED, Pantry

pytestmark = pytest.mark.sim


def test_hide_quantities(logged_in: Pantry) -> None:
    app = logged_in.app
    logged_in.open_settings()
    app.toggle(L("settings.showQuantities"))
    assert app.value(L("settings.showQuantities")) == "0"
    logged_in.back_to_list()
    assert app.label(L("list.row", name="Eggs")) == "Eggs"


def test_sort_alphabetically(logged_in: Pantry) -> None:
    app = logged_in.app
    logged_in.open_settings()
    app.toggle(L("settings.sortAlphabetically"))
    assert app.value(L("settings.sortAlphabetically")) == "1"
    logged_in.back_to_list()
    assert logged_in.row_names() == sorted(SEEDED)


def test_settings_kept_after_leaving(logged_in: Pantry) -> None:
    app = logged_in.app
    logged_in.open_settings()
    app.toggle(L("settings.showQuantities"))
    logged_in.back_to_list()
    logged_in.open_settings()
    assert app.value(L("settings.showQuantities")) == "0"
    assert app.value(L("settings.sortAlphabetically")) == "0"


def test_log_out(logged_in: Pantry) -> None:
    logged_in.open_settings()
    logged_in.app.tap(L("settings.logout"))
    logged_in.app.wait_for_screen("login")
