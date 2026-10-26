"""Click through the sidebar app's buttons against the sample backend.

Run from the repo root: python scripts/panel-preview/check.py
Needs Playwright with Chromium (CHROME picks a Chrome binary). The sample
backend in index.html answers like the real one (a light zone's id is "id",
unknown keys and zones are refused), so this catches the panel sending what
Home Assistant would reject. Runs with the TV blocked for bedtime and a live
update every 50 ms, as on a real evening with lots of media players. Exits 1
on any failure.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import sys
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def _checks(page, panel, fail) -> None:
    def wait_for(js: str, what: str) -> None:
        try:
            page.wait_for_function(js, timeout=3000)
        except Exception:  # noqa: BLE001 - report and carry on
            fail(what)

    # Dashboard: the TV is off for bedtime; its lights still turn off from here.
    page.evaluate("localStorage.setItem('tv-mgmt-expanded', JSON.stringify(['fam']))")
    page.reload()
    page.wait_for_timeout(1200)
    tv = panel.locator("#tvr-fam")
    if tv.locator("button[data-key='power']").inner_text().strip() != "Turn TV on":
        fail("the TV's power button doesn't say Turn TV on")
    lights = tv.locator("button[data-tv-lights-off]")
    if not lights.count():
        fail("no Turn TV lights off button next to the TV's power button")
    else:
        lights.click()
        wait_for("window.lastLightsOff && window.lastLightsOff.entry_id === 'fam'", "Turn TV lights off sent nothing")
        wait_for(
            "document.querySelector('tv-mgmt-panel').shadowRoot.querySelector('#tvr-fam').textContent.includes('TV lights off')",
            "no note after turning the TV lights off",
        )

    # Lights tab, every zone command, while the page keeps redrawing.
    panel.locator("nav button[data-tab='lights']").click()
    page.wait_for_timeout(800)
    zone = "Behind the TV"
    panel.locator(f"button[aria-label='Remove {zone}']").wait_for(timeout=3000)
    panel.locator("button[data-zone-act='resync']").first.click()
    wait_for("window.lastZoneAction && window.lastZoneAction.zone_id === 'a1'", "Look again didn't name the zone")

    panel.locator("button[data-zone-edit]").first.click()
    if not panel.locator("#zone-form").count():
        fail("Change didn't open the zone")
    else:
        panel.locator("#zf-name").fill("Behind the set")
        panel.locator("#zone-form button[type=submit]").click()
        wait_for("window.lastZone && window.lastZone.zone.id === 'a1'", "saving a zone didn't send its id")
        wait_for(
            "document.querySelector('tv-mgmt-panel').shadowRoot.textContent.includes('Light zone saved')",
            "saving a zone didn't say it was saved",
        )
        zone = "Behind the set"

    panel.locator("input[data-zone-enable='a1']").click(force=True)
    wait_for("window.lastZone && window.lastZone.zone.enabled === false", "the zone's switch didn't save")

    # A remove that fails says so in the Light sync card, and the zone stays.
    page.evaluate("window.failNext = 'zone/remove'")
    panel.locator(f"button[aria-label='Remove {zone}']").click()
    panel.locator("button[data-zone-remove-yes='a1']").click()
    wait_for(
        "[...document.querySelector('tv-mgmt-panel').shadowRoot.querySelectorAll('.card .error')]"
        ".some((e) => e.textContent.includes(\"Couldn't do that now\"))",
        "a failed remove didn't say so next to the zones",
    )
    if not panel.locator("[data-zone-edit='a1']").count():
        fail("a failed remove hid the zone")

    panel.locator(f"button[aria-label='Remove {zone}']").click()
    panel.locator("button[data-zone-remove-yes='a1']").click()
    wait_for("window.lastZoneRemove && window.lastZoneRemove.zone_id === 'a1' && !window.failNext", "Remove didn't remove the zone")
    wait_for(
        "!document.querySelector('tv-mgmt-panel').shadowRoot.querySelector(\"[data-zone-edit='a1']\")",
        "the removed zone is still showing",
    )


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/scripts/panel-preview/index.html"
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None)
        for w in (360, 390):
            page = browser.new_page(viewport={"width": w, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("dialog", lambda d: (errors.append(f"unexpected dialog: {d.message}"), d.dismiss()))
            page.goto(f"{base}?blocked=1&busy=1")
            page.wait_for_timeout(800)
            panel = page.locator("tv-mgmt-panel")
            page.set_default_timeout(3000)
            try:
                _checks(page, panel, lambda what, w=w: errors.append(f"{w}px: {what}"))
            except Exception as err:  # noqa: BLE001 - a missing button or a step that timed out
                errors.append(f"{w}px: stopped: {str(err).splitlines()[0]}")
            width = page.evaluate("document.documentElement.scrollWidth")
            if width > w:
                errors.append(f"{w}px: page scrolls sideways ({width}px)")
            page.close()
        browser.close()
    server.shutdown()
    for error in errors:
        print("ERROR:", error)
    print("Panel checks:", "failed" if errors else "passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
