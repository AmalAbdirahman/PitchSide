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


def _fast(pg: Any) -> None:
    """The Replay pill steps 1x -> 2x -> 4x."""
    while pg.get_attribute("#speed", "data-speed") != "4":
        pg.click("#speed")


def test_play_to_full_time_with_decisions(page: Any) -> None:
    assert len(T2) == 2, "the scenario needs two T2 facts: one to approve, one to let expire"
    _fast(page)
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
        light_pitch = _pitch_fill(pg)
        assert light_pitch == _token(pg, "--pitch")
        bug = _bug_score_bg(pg)
        pg.click("#theme")
        assert _theme(pg) == "light" and pg.inner_text("#theme-v") == "Light"
        pg.click("#theme")
        assert _theme(pg) == "dark" and _token(pg, "--bg") == "#0B0D10"
        assert _pitch_fill(pg) == _token(pg, "--pitch") != light_pitch  # redrawn, dark token
        assert _bug_score_bg(pg) == bug  # on-air graphics do not change with the theme
        pg.reload()
        pg.wait_for_selector("#qhead b")
        assert _theme(pg) == "dark" and _pitch_fill(pg) == _token(pg, "--pitch")
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
        _fast(room)
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


def test_shell_views_keys_and_kpis(page: Any) -> None:
    """R2: sidebar tabs and keys 1-3 switch views; the KPI row follows the replay clock."""
    page.click("#v-an")
    assert page.is_visible("#view-an") and not page.is_visible("#view-bc")
    page.keyboard.press("3")
    assert page.get_attribute("#v-au", "aria-selected") == "true" and page.is_visible("#view-au")
    page.keyboard.press("1")
    assert page.is_visible("#view-bc")
    assert page.inner_text("#built") == HERO["meta"]["built"]
    assert page.inner_text("#k-npxg-h") == "0.00" and page.inner_text("#k-ver") == "0"
    _fast(page)
    page.click("#play")
    page.wait_for_function("document.querySelector('#k-ver').textContent !== '0'", timeout=30_000)
    page.click("#play")
    assert page.inner_text("#k-wp").endswith("%")
    assert page.inner_text("#k-ver-n").startswith("/ ")
    assert page.locator("#sp-wp path").count() == 1
    page.click("#go-set")
    assert page.is_visible("#setdlg") and page.is_visible("#follow")
    page.keyboard.press("Escape")
    page.click("#go-ev")
    assert page.is_visible("#drawer")
    assert page.problems == []


@pytest.mark.parametrize(("width", "side"), [(1000, "icons"), (390, "bottom")])
def test_narrow_layouts_keep_the_nav_and_never_scroll_sideways(
    site: str, width: int, side: str
) -> None:
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            reduced_motion="reduce", viewport={"width": width, "height": 860}
        )
        pg = _watch(context.new_page())
        pg.goto(site)
        pg.wait_for_selector("#qhead b")
        assert pg.evaluate("document.documentElement.scrollWidth") <= width
        label = pg.locator("#v-an span").bounding_box()
        assert label is not None and label["width"] <= 1  # collapsed to the icon, kept for readers
        assert pg.get_by_role("tab", name="Analysis").count() == 1  # still named for screen readers
        box = pg.locator(".side").bounding_box()
        if side == "bottom":
            assert box is not None and box["y"] > 700  # pinned to the bottom of the viewport
        pg.click("#v-an")
        assert pg.is_visible("#view-an")
        browser.close()
        assert pg.problems == []


SOLID = """.bug,.lt{background:var(--air-panel)!important;backdrop-filter:none!important;
-webkit-backdrop-filter:none!important}"""
FRAMES = """ms => new Promise(done => {
  const t = []; let last = performance.now(); const end = last + ms;
  requestAnimationFrame(function f(now) { t.push(now - last); last = now;
    if (now < end) requestAnimationFrame(f); else done(t); });
})"""


def _frame_times(site: str, glass: bool) -> list[float]:
    """Frame times (ms) over 4 s of replay at 4x with a lower third on air."""
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        pg.goto(site)
        pg.wait_for_selector("#qhead b")
        if not glass:
            pg.add_style_tag(content=SOLID)
        _fast(pg)
        pg.click("#play")
        pg.wait_for_selector("#lt.show", timeout=30_000)
        blur = pg.evaluate("getComputedStyle(document.querySelector('#lt')).backdropFilter")
        assert ("blur" in blur) == glass, blur
        times: list[float] = pg.evaluate(FRAMES, 4000)
        browser.close()
    return sorted(times[1:])


def test_glass_frame_time_at_4x(site: str) -> None:
    """R4: frame time with the glass on, against the same page with solid overlays."""
    stats = {}
    for glass in (False, True):
        t = _frame_times(site, glass)
        stats[glass] = (sum(t) / len(t), t[len(t) // 2], t[int(len(t) * 0.95)], len(t))
    for glass, (mean, p50, p95, n) in stats.items():
        print(f"glass={glass}: mean {mean:.2f} ms, p50 {p50:.2f}, p95 {p95:.2f}, {n} frames")
    assert stats[True][0] <= stats[False][0] * 1.5 + 2, "glass makes frames noticeably slower"


ASK = json.loads((DOCS / "data" / "ask.json").read_text(encoding="utf-8"))["answers"]


def test_ask_panel_opens_questions_with_the_clock_and_shows_checked_answers(page: Any) -> None:
    """R5: suggested questions open at their minute; an answer shows ticks, chips and the count,
    or the refusal, or that it was not built. The free-text box stays disabled."""
    first = min(ASK, key=lambda a: a["at_t"])
    button = f'#ask-qs button[data-q="{first["question_id"]}"]'
    assert page.is_disabled(button) and page.is_disabled("#ask-text")
    assert page.locator("#askt tbody tr").count() == len(ASK)
    page.click("#go-ask")
    assert page.evaluate("document.activeElement.id") == "ask"
    _fast(page)
    page.click("#play")
    page.wait_for_selector(f"{button}:not([disabled])", timeout=60_000)
    page.click("#play")
    page.click(button)
    answer = page.locator("#ask-thread .a").last
    assert first["question"] in page.inner_text("#ask-thread")
    if first["status"] == "verified":
        n = len(first["numbers"])
        assert answer.locator(".tick").count() >= n > 0
        assert f"{n}/{n} numbers verified" in answer.inner_text()
        if first["evidence_ids"]:
            answer.locator("button[data-ev]").first.click()
            assert page.is_visible("#drawer")
    elif first["status"] == "refused":
        assert "can't verify" in answer.inner_text()
    else:
        assert "No answer has been built" in answer.inner_text()
    assert page.problems == []


def test_live_tab_is_the_only_request_to_the_local_server(site: str) -> None:
    """R6: loading and playing makes no request to 127.0.0.1:<live port>; clicking Live (local)
    does. With no server it shows how to start one; with one (mocked Analyst, this page's origin
    allowed) it enables the box and shows a checked answer."""
    import socket

    from app.ask_live import Gate, LiveService, make_server
    from core.config import load
    from tests.test_ask import FACTS, MATCH, Mock, refuse

    s = load()
    live_origin = f"http://127.0.0.1:{s.ask.live.port}"
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", s.ask.live.port)) == 0:
            pytest.skip("something already listens on the live port")
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page()
        seen: list[str] = []
        pg.on("request", lambda r: r.url.startswith(live_origin) and seen.append(r.url))
        pg.goto(site)
        pg.wait_for_selector("#qhead b")
        pg.click("#play")
        pg.wait_for_timeout(1500)
        assert seen == [] and pg.is_disabled("#ask-text")
        pg.click("#ask-live")
        pg.wait_for_function(
            "document.querySelector('#ask-hint').textContent.includes('ask_server')"
        )
        assert seen == [live_origin + "/health"] and pg.is_disabled("#ask-text")

        live = s.ask.live.model_copy(update={"allowed_origins": (site.rstrip("/"),)})
        svc = LiveService(MATCH, FACTS, s, Mock(refuse), Gate(live.timeout_s))
        srv = make_server(svc, live, "127.0.0.1", live.port)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            pg.click("#ask-live")
            pg.wait_for_selector("#ask-status:not([hidden])")
            assert pg.text_content("#ask-status") == "Live · running on this machine"
            pg.fill("#ask-text", "How far did the right back run?")
            pg.press("#ask-text", "Enter")
            pg.wait_for_selector('#ask-live-thread .a[data-status="refused"]')
            assert pg.is_hidden("#ask-qs-part") and pg.is_enabled("#ask-text")
            pg.click("#ask-built")
            assert pg.is_visible("#ask-qs-part") and pg.is_disabled("#ask-text")
        finally:
            srv.shutdown()
            srv.server_close()
        browser.close()
