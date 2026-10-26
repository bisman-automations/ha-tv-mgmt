"""Screenshot the sidebar app with sample data, to check panel changes.

Run from the repo root: python scripts/panel-preview/shoot.py [out_dir]
Needs Playwright with Chromium. Serves the repo over HTTP (browsers won't
load ES modules from file://), opens scripts/panel-preview/index.html, which
fakes Home Assistant with sample TVs, and saves each tab at phone and desktop
width in light and dark themes. Fails if the page logs any error.

The page takes query parameters: theme=light|dark, single=1 (one TV),
empty=1 (no TVs), denied=1 (a user without access), nohome=1 (no whole-home limit), loading=1 (every
request stays loading) and loading=tabs (the TVs load, each tab's data stays
loading), to see the placeholders; saved as loading-*.png.

With --docs, it instead refreshes the README screenshots, docs/images/panel-<tab>.png
for every tab (DOC_SHOTS): an admin's view, so all six tabs show, at 390x900 and 4/3
scale (520x1200). Set CHROME to a Chrome or Chromium binary to use it instead of
Playwright's own.
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

TABS = ["dashboard", "activity", "analytics", "limits", "lights", "access"]
SIZES = {"phone": (390, 900), "desktop": (1440, 1000)}
DOCS = ROOT / "docs" / "images"
# README screenshots, docs/images/panel-<tab>.png: the first row light, the
# second dark, so both themes show.
DOC_SHOTS = {
    "dashboard": "light", "activity": "light", "analytics": "light",
    "limits": "dark", "lights": "dark", "access": "dark",
}


def _docs(browser, base: str, errors: list[str]) -> None:
    """Save each tab as it appears in the README: a 390x900 phone at 4/3 scale (520x1200)."""
    for tab, theme in DOC_SHOTS.items():
        page = browser.new_page(viewport={"width": 390, "height": 900}, device_scale_factor=4 / 3)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"{base}?theme={theme}")
        # The first TV open, as on the dashboard after tapping it.
        page.evaluate("localStorage.setItem('tv-mgmt-expanded', JSON.stringify(['fam']))")
        page.reload()
        page.wait_for_timeout(1200)
        panel = page.locator("tv-mgmt-panel")
        panel.locator(f"nav button[data-tab='{tab}']").click()
        page.wait_for_timeout(800)
        width = page.evaluate("document.documentElement.scrollWidth")
        nav = page.evaluate(
            "(() => { const n = document.querySelector('tv-mgmt-panel').shadowRoot.querySelector('nav');"
            " return n.scrollWidth - n.clientWidth; })()"
        )
        if width > 390 or nav > 0:
            errors.append(f"docs {tab}: page or tabs scroll sideways ({width}px, tabs +{nav}px)")
        page.screenshot(path=DOCS / f"panel-{tab}.png")
        page.close()


def main() -> int:
    docs = "--docs" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a != "--docs"]
    out = DOCS if docs else Path(args[0] if args else "panel-shots")
    out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/scripts/panel-preview/index.html"
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None)
        if docs:
            _docs(browser, base, errors)
        for size, (w, h) in ({} if docs else SIZES).items():
            for theme in ("light", "dark"):
                page = browser.new_page(viewport={"width": w, "height": h})
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("dialog", lambda d: d.accept())
                page.goto(f"{base}?theme={theme}")
                # TV cards start collapsed; open the first so the dashboard shot shows everything.
                page.evaluate("localStorage.setItem('tv-mgmt-expanded', JSON.stringify(['fam']))")
                page.reload()
                page.wait_for_timeout(1200)
                panel = page.locator("tv-mgmt-panel")
                for tab in TABS:
                    panel.locator(f"nav button[data-tab='{tab}']").click()
                    page.wait_for_timeout(500)
                    width = page.evaluate("document.documentElement.scrollWidth")
                    if width > w:
                        errors.append(f"{tab} {size}: page scrolls sideways ({width}px)")
                    page.screenshot(path=out / f"{tab}-{size}-{theme}.png", full_page=True)
                page.close()
                # The placeholders shown while each tab loads.
                page = browser.new_page(viewport={"width": w, "height": h})
                page.on("pageerror", lambda e: errors.append(str(e)))
                for mode, tabs in (("1", ["dashboard"]), ("tabs", ["activity", "analytics", "limits", "lights"])):
                    page.goto(f"{base}?theme={theme}&loading={mode}")
                    page.wait_for_timeout(600)
                    panel = page.locator("tv-mgmt-panel")
                    for tab in tabs:
                        panel.locator(f"nav button[data-tab='{tab}']").click()
                        page.wait_for_timeout(300)
                        page.screenshot(path=out / f"loading-{tab}-{size}-{theme}.png")
                page.close()
        browser.close()
    server.shutdown()
    for error in errors:
        print("ERROR:", error)
    print(f"Saved screenshots to {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
