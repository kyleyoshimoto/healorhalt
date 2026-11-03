"""Pantry demo-app flows shared by the UI tests."""

from healorhalt.locator import L
from healorhalt.session import App

USERNAME = "demo"
PASSWORD = "pantry123"
SEEDED = ["Rice", "Eggs", "Flour", "Milk", "Apples"]


class Pantry:
    """Common Pantry flows, built on the app fixture."""

    def __init__(self, app: App) -> None:
        self.app = app

    def log_in(self, username: str = USERNAME, password: str = PASSWORD) -> None:
        self.app.wait_for_screen("login")
        self.app.type(L("login.username"), username)
        self.app.type(L("login.password"), password)
        self.app.tap(L("login.submit"))

    def row_names(self) -> list[str]:
        rows = self.app.find_all(L("list.rows"))
        return [str(row.get_attribute("name")).removeprefix("item.row.") for row in rows]

    def open_item(self, name: str) -> None:
        self.app.tap(L("list.row", name=name))
        self.app.wait_for_screen("detail")

    def open_settings(self) -> None:
        self.app.tap(L("list.settings"))
        self.app.wait_for_screen("settings")

    def back_to_list(self) -> None:
        self.app.driver.back()
        self.app.wait_for_screen("list")

    def add_item(self, name: str, quantity: str) -> None:
        self.app.tap(L("list.add"))
        self.app.wait_for_screen("add")
        self.app.type(L("add.name"), name)
        self.app.type(L("add.quantity"), quantity)
        self.app.tap(L("add.save"))
        self.app.wait_gone(L("add.name"))
        self.app.wait_for_screen("list")
