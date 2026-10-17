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
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

TABS = ["dashboard", "activity", "analytics", "limits", "access"]
SIZES = {"phone": (390, 900), "desktop": (1440, 1000)}


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "panel-shots")
    out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/scripts/panel-preview/index.html"
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, (w, h) in SIZES.items():
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
                for mode, tabs in (("1", ["dashboard"]), ("tabs", ["activity", "analytics", "limits"])):
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
