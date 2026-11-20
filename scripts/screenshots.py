"""README screenshots (R7): the control room at about 67', dark and light, and at phone width.

Serves docs/ on a free local port, plays the replay at 4x to the target minute, pauses, opens two
suggested Ask PitchSide questions and saves PNGs to docs/img/. Rerun after any visible change.

Run: pip install -e ".[e2e]" && python -m playwright install chromium
     python scripts/screenshots.py
"""

from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT  # noqa: E402

DOCS = ROOT / "docs"
OUT = DOCS / "img"
TARGET = "67:00"  # the moment design/redesign_mockup.png shows
QUESTIONS = ("q3", "q1")  # who scored (63'), Varga's shots after 60' (66')
PHONE = {"width": 390, "height": 844}
SHOTS = [  # name, theme, viewport, element to scroll to (None: the top of the page)
    ("control-room-dark", "dark", {"width": 1440, "height": 900}, None),
    ("control-room-light", "light", {"width": 1440, "height": 900}, None),
    ("control-room-phone", "dark", PHONE, None),
    ("control-room-phone-ask", "dark", PHONE, "#ask"),
]
REACHED = """target => {
  const [m, s] = document.querySelector('#clock').textContent.split(':').map(Number);
  const [tm, ts] = target.split(':').map(Number);
  return m * 60 + s >= tm * 60 + ts;
}"""


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass


def shoot(
    pw: Any, site: str, name: str, scheme: str, viewport: dict[str, int], at: str | None
) -> None:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto(site)
    pg.wait_for_selector("#qhead b")
    while pg.get_attribute("#speed", "data-speed") != "4":
        pg.click("#speed")
    pg.click("#play")
    pg.wait_for_function(REACHED, arg=TARGET, timeout=120_000, polling=50)
    pg.click("#play")
    for q in QUESTIONS:
        pg.click(f'#ask-qs button[data-q="{q}"]')
    if at is None:
        pg.evaluate("window.scrollTo(0, 0)")
    else:
        pg.eval_on_selector(at, "e => e.scrollIntoView({block: 'start'})")
    path = OUT / f"{name}.png"
    pg.screenshot(path=str(path))
    browser.close()
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> None:
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(DOCS)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        with sync_playwright() as pw:
            for name, scheme, viewport, at in SHOTS:
                shoot(pw, site, name, scheme, viewport, at)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
