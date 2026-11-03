import pytest

from healorhalt.locator import L

from .pantry import Pantry

pytestmark = pytest.mark.sim


def test_search_filters_items(logged_in: Pantry) -> None:
    logged_in.app.type(L("list.search"), "E")
    assert logged_in.row_names() == ["Rice", "Eggs", "Apples"]


def test_search_without_matches_shows_empty_state(logged_in: Pantry) -> None:
    logged_in.app.type(L("list.search"), "zzz")
    assert logged_in.app.exists(L("list.empty"), timeout=5)
    assert logged_in.row_names() == []
