import pytest

from healorhalt.locator import L

from .pantry import SEEDED, Pantry

pytestmark = pytest.mark.sim


def test_seeded_items_are_listed(logged_in: Pantry) -> None:
    assert logged_in.row_names() == SEEDED


def test_add_item(logged_in: Pantry) -> None:
    logged_in.add_item("Pasta", "3")
    assert logged_in.app.label(L("list.row", name="Pasta")) == "Pasta, 3"
    assert logged_in.app.label(L("list.count")) == "6 items"


def test_cancel_add_item(logged_in: Pantry) -> None:
    app = logged_in.app
    app.tap(L("list.add"))
    app.wait_for_screen("add")
    app.type(L("add.name"), "Pasta")
    app.tap(L("add.cancel"))
    app.wait_gone(L("add.name"))
    assert logged_in.row_names() == SEEDED
    assert app.label(L("list.count")) == "5 items"


def test_item_detail_shows_name_and_quantity(logged_in: Pantry) -> None:
    logged_in.open_item("Eggs")
    assert logged_in.app.label(L("detail.name")) == "Eggs"
    assert logged_in.app.label(L("detail.quantity")) == "12"


def test_edit_item_quantity(logged_in: Pantry) -> None:
    app = logged_in.app
    logged_in.open_item("Eggs")
    app.tap(L("detail.edit"))
    app.type(L("detail.quantityField"), "10")
    app.tap(L("detail.save"))
    assert app.label(L("detail.quantity")) == "10"
    logged_in.back_to_list()
    assert app.label(L("list.row", name="Eggs")) == "Eggs, 10"


def test_delete_item(logged_in: Pantry) -> None:
    app = logged_in.app
    logged_in.open_item("Milk")
    app.tap(L("detail.delete"))
    app.tap(L("detail.confirmDelete"))
    app.wait_for_screen("list")
    assert logged_in.row_names() == ["Rice", "Eggs", "Flour", "Apples"]
    assert app.label(L("list.count")) == "4 items"
