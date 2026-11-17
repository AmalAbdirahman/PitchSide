"""Browser smoke test of the static site (marked e2e, skipped by a plain `pytest`).

Serves docs/ on a free local port, plays the match to full time at 4x, vetoes a T1 fact,
approves the first T2 fact and lets the second expire, then checks the counters, the log and the
console. It also runs tests/routing_cases.json through docs/js/routing.js inside the browser.

Run: pip install -e ".[dev,e2e]" && playwright install chromium && pytest -m e2e
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from core.config import ROOT

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.e2e

DOCS = ROOT / "docs"
HERO = json.loads((DOCS / "data" / "hero.json").read_text(encoding="utf-8"))
T2 = [f["fact_id"] for f in HERO["facts"] if f["risk"]["tier"] == "T2"]


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(DOCS)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture()
def page(site: str) -> Iterator[Any]:
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(reduced_motion="reduce")
        pg = context.new_page()
        pg.problems = []
        pg.on("console", lambda m: m.type == "error" and pg.problems.append(m.text))
        pg.on("pageerror", lambda e: pg.problems.append(str(e)))
        pg.goto(site)
        pg.wait_for_selector("#qhead b")
        yield pg
        browser.close()


def _flow(page: Any) -> dict[str, int]:
    tiles = page.eval_on_selector_all(
        "#flow div",
        "els => els.map(e => [e.querySelector('span').textContent, e.querySelector('b').textContent])",
    )
    return {name: int(value) for name, value in tiles}


def test_play_to_full_time_with_decisions(page: Any) -> None:
    assert len(T2) == 2, "the scenario needs two T2 facts: one to approve, one to let expire"
    page.click('[data-speed="4"]')
    page.click("#play")
    page.locator('#rows button[data-act="veto"]').first.click(timeout=20_000)
    page.locator(f'#rows button[data-act="approve"][data-id="{T2[0]}"]').click(timeout=20_000)
    page.wait_for_selector('#play[aria-label="Play"]', timeout=90_000)  # full time
    page.wait_for_function("document.querySelector('#qhead b').textContent === '0'", timeout=30_000)

    log = page.inner_text("#log")
    assert f"Producer approved {T2[0]}" in log
    assert f"{T2[1]} expired unanswered" in log
    assert "Producer vetoed" in log
    flow = _flow(page)
    assert flow["Vetoed or rejected by producer"] == 1
    assert flow["Expired, never aired"] == 1
    assert flow["Drafts"] == len(HERO["facts"]) + len(HERO["log"])
    assert flow["Aired"] == len(HERO["facts"]) - 2
    assert "within 12 s" in page.inner_text("#rule-t2")
    assert page.problems == []


def test_routing_cases_in_the_browser(page: Any) -> None:
    cases = json.loads((ROOT / "tests" / "routing_cases.json").read_text(encoding="utf-8"))
    failed = page.evaluate(
        """async ([cases, rules]) => {
          const { resolve } = await import('./js/routing.js');
          return cases.filter(c => {
            const reasons = c.autopilot_ok ? [rules.autopilot_reason] : [];
            const got = resolve(rules, c.tier, c.ttl_s, reasons, c.elapsed_s, c.decision, c.decision_at_s);
            return got.state !== c.expect.state || got.by !== c.expect.by;
          }).map(c => c.id);
        }""",
        [cases, HERO["meta"]["routing"]],
    )
    assert failed == []
    assert page.problems == []


def _token(pg: Any, name: str) -> str:
    js = "t => getComputedStyle(document.documentElement).getPropertyValue(t).trim()"
    return str(pg.evaluate(js, name)).upper()


def _theme(pg: Any) -> str:
    return str(pg.evaluate("document.documentElement.dataset.theme || 'system'"))


def _pitch_fill(pg: Any) -> str:
    return str(pg.get_attribute("#pitch rect", "fill")).upper()


def _bug_score_bg(pg: Any) -> str:
    return str(pg.evaluate("getComputedStyle(document.querySelector('#bug-s')).backgroundColor"))


def test_theme_toggle_switches_redraws_and_is_remembered(site: str) -> None:
    """R1: System -> Light -> Dark -> System. The pitch SVG redraws from the tokens; the on-air
    score bug keeps its dark tokens in both themes; the choice survives a reload."""
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(reduced_motion="reduce", color_scheme="light")
        pg = _watch(context.new_page())
        pg.goto(site)
        pg.wait_for_selector("#qhead b")
        assert _theme(pg) == "system" and _token(pg, "--bg") == "#F5F6F8"  # the system is light
        assert _pitch_fill(pg) == _token(pg, "--pitch")
        bug = _bug_score_bg(pg)
        pg.click("#theme")
        assert _theme(pg) == "light" and pg.inner_text("#theme-v") == "Light"
        pg.click("#theme")
        assert _theme(pg) == "dark" and _token(pg, "--bg") == "#0B0D10"
        assert _pitch_fill(pg) == "#17261C"  # redrawn with the dark pitch token
        assert _bug_score_bg(pg) == bug  # on-air graphics do not change with the theme
        pg.reload()
        pg.wait_for_selector("#qhead b")
        assert _theme(pg) == "dark" and _pitch_fill(pg) == "#17261C"
        pg.click("#theme")
        assert _theme(pg) == "system" and _token(pg, "--bg") == "#F5F6F8"
        browser.close()
        assert pg.problems == []


def _watch(pg: Any) -> Any:
    pg.problems = []
    pg.on("console", lambda m: m.type == "error" and pg.problems.append(m.text))
    pg.on("pageerror", lambda e: pg.problems.append(str(e)))
    return pg


def test_overlay_follows_the_control_room(site: str) -> None:
    """P13: two tabs of one browser; the overlay draws what the control room puts on air."""
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            reduced_motion="reduce", viewport={"width": 1920, "height": 1080}
        )
        overlay = _watch(context.new_page())
        overlay.goto(site + "overlay.html")
        assert overlay.is_visible("#hint")
        assert (
            overlay.evaluate("getComputedStyle(document.body).backgroundColor")
            == "rgba(0, 0, 0, 0)"
        )
        room = _watch(context.new_page())
        room.goto(site)
        room.wait_for_selector("#qhead b")
        room.click('[data-speed="4"]')
        room.click("#play")
        overlay.wait_for_selector("#graphic .g", timeout=30_000)
        assert not overlay.is_visible("#hint")
        assert HERO["meta"]["home"] in overlay.inner_text("#bug")
        on_air = room.inner_text("#lt-t")
        assert on_air and on_air in overlay.inner_text("#graphic").replace("\n", " ")
        browser.close()
        assert overlay.problems == [] and room.problems == []


@pytest.mark.parametrize(
    ("layout", "css"),
    [
        ("lower_third", ".g-lower"),
        ("ticker", ".g-ticker"),
        ("stat_comparison", ".g-compare"),
        ("player_card", ".g-player"),
    ],
)
def test_overlay_demo_draws_each_layout(page: Any, site: str, layout: str, css: str) -> None:
    page.goto(f"{site}overlay.html?demo={layout}")
    page.wait_for_selector(css)
    assert page.is_visible("#bug") and not page.is_visible("#hint")
    assert page.problems == []
