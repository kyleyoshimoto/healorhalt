"""Save page-source XML of each demo screen, unmutated and under each mutation.

The XML goes to tests/unit/fixtures/xml/ for the Tier 1 scoring tests, which then run without a
simulator. Needs Appium running and a built app (make app). Rerun after changing the demo app.
"""

import time
from pathlib import Path

from healorhalt.session import App, boot_simulator, ios_session, resolve_udid

BUNDLE = "com.healorhalt.pantry"
APP = Path("apps/demo-ios/build/Build/Products/Debug-iphonesimulator/Pantry.app")
OUT = Path("tests/unit/fixtures/xml")

# Screen captured for each case; "base" captures every screen.
CASES = {
    "base": ["login", "list", "add", "detail", "settings"],
    "M01": ["login"],
    "M02": ["add"],
    "M03": ["list"],
    "M04": ["detail"],
    "M05": ["detail"],
    "M06": ["settings"],
    "M07": ["list"],
    "M08": ["login"],
    "M09": ["settings"],
    "M10": ["list"],
}


def tap(app: App, *ids: str) -> None:
    """Tap the first of `ids` present; mutations rename some of them."""
    for element_id in ids:
        found = app.driver.find_elements("accessibility id", element_id)
        if found:
            found[0].click()
            time.sleep(2)
            return
    raise LookupError(f"none of {ids} found")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    udid = resolve_udid("iPhone 17", "26.4")
    boot_simulator(udid)
    with ios_session(udid, BUNDLE, app=APP) as driver:
        app = App(driver, BUNDLE)
        for case, screens in CASES.items():
            app.launch(mutation=None if case == "base" else case)
            time.sleep(2)

            def save(screen: str, case: str = case, screens: list[str] = screens) -> None:
                if screen in screens:
                    (OUT / f"{case}_{screen}.xml").write_text(driver.page_source, encoding="utf-8")
                    print("saved", case, screen)

            save("login")
            if screens == ["login"]:
                continue
            driver.find_element("accessibility id", "login.username").send_keys("demo")
            driver.find_element("accessibility id", "login.password").send_keys("pantry123")
            tap(app, "login.submit", "login.signIn")
            save("list")
            if "add" in screens:
                tap(app, "list.add")
                save("add")
                tap(app, "add.cancel")
            if "detail" in screens:
                tap(app, "item.row.Eggs")
                save("detail")
                driver.back()
                time.sleep(2)
            if "settings" in screens:
                tap(app, "list.settings", "list.preferences")
                save("settings")


if __name__ == "__main__":
    main()
