"""Static site checks: assets exist, no network, and the bundle carries what the modules read."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from core.config import ROOT, load

DOCS = ROOT / "docs"
PUBLISHED = sorted(p for p in DOCS.rglob("*") if p.is_file())  # everything GitHub Pages serves
URL = re.compile(r"https?://[^\s\"'<>)]+")


@pytest.fixture(scope="module")
def hero() -> dict[str, Any]:
    return json.loads((DOCS / "data" / "hero.json").read_text(encoding="utf-8"))


def test_index_references_exist() -> None:
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"#]+)"', html)
    assert refs, "index.html references no files"
    for ref in refs:
        assert (DOCS / ref).is_file(), f"index.html references missing file {ref}"


MODULES = sorted((DOCS / "js").glob("*.js"))
IMPORT = re.compile(r"""^import\s[^'"]*['"]([^'"]+)['"]""", re.MULTILINE)


def test_index_loads_the_entry_module() -> None:
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="js/main.js"></script>' in html


def test_app_fetches_only_local_data() -> None:
    fetched = [
        path
        for module in MODULES
        for path in re.findall(r"getJson\('([^']+)'\)", module.read_text(encoding="utf-8"))
    ]
    assert fetched, "no module fetches the bundle"
    for path in fetched:
        assert (DOCS / path).is_file(), f"the site fetches missing file {path}"


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_module_imports_resolve(module: Path) -> None:
    for spec in IMPORT.findall(module.read_text(encoding="utf-8")):
        assert spec.startswith("./"), f"{module.name} imports {spec}; only local modules allowed"
        assert (module.parent / spec).is_file(), f"{module.name} imports missing {spec}"


def test_front_end_reads_routing_rules_from_the_bundle() -> None:
    # The timeouts and the autopilot rule come from meta.routing, not from literals in the code.
    js = "\n".join(m.read_text(encoding="utf-8") for m in MODULES)
    assert "meta.routing" in js
    assert "autopilot_ok" not in js
    assert not re.search(r"(?<![-\w])(ttl|left)\s*[:=]\s*\d", js)  # not CSS padding-left:0


LIVE_JS = DOCS / "js" / "ask_live.js"
LIVE_URL = f"http://127.0.0.1:{load().ask.live.port}"  # R6: the one allowed address


@pytest.mark.parametrize("path", PUBLISHED, ids=lambda p: p.relative_to(DOCS).as_posix())
def test_no_network_urls(path: Path) -> None:
    """No file under docs/ may reference http(s):// at all, not even an XML namespace. The one
    exception (R6) is the local Ask server, on 127.0.0.1 at config.yaml's port, in ask_live.js."""
    allowed = [LIVE_URL] if path == LIVE_JS else []
    bad = [u for u in URL.findall(path.read_text(encoding="utf-8", errors="replace"))]
    assert [u for u in bad if u not in allowed] == [], f"{path.relative_to(DOCS)}: {bad}"


def test_the_live_url_is_used_once_and_matches_config() -> None:
    js = LIVE_JS.read_text(encoding="utf-8")
    assert URL.findall(js) == [LIVE_URL]
    assert f"export const LIVE_URL = '{LIVE_URL}';" in js


def _body_of(js: str, name: str) -> str:
    """The text of `function name(...) {...}` (top-level, closing brace at column 0)."""
    m = re.search(rf"^(?:export )?(?:async )?function {name}\(.*?^\}}", js, re.S | re.M)
    assert m, f"no function {name}"
    return m.group(0)


def test_the_page_calls_the_local_server_only_after_a_click() -> None:
    """fetch() appears only for the bundle's own files (getJson; overlay.js's hero.json) and in
    ask_live.js's callLocal;
    callLocal runs only from connect and ask, which run only from the Live tab's click and the
    question form's submit, both wired in wireLive (which itself makes no request)."""
    for module in MODULES:
        js = module.read_text(encoding="utf-8")
        n = len(re.findall(r"\bfetch\(", js))
        expected = {"data.js": 1, "overlay.js": 1, "ask_live.js": 1}.get(module.name, 0)
        assert n == expected, f"{module.name} calls fetch {n} times"
    overlay = (DOCS / "js" / "overlay.js").read_text(encoding="utf-8")
    assert "fetch('data/hero.json')" in overlay
    js = LIVE_JS.read_text(encoding="utf-8")
    funcs = re.findall(r"^(?:export )?(?:async )?function (\w+)", js, re.M)
    assert "fetch(" in _body_of(js, "callLocal")
    callers = {f for f in funcs if f != "callLocal" and "callLocal(" in _body_of(js, f)}
    assert callers == {"connect", "ask"}
    assert js.count("callLocal(") == 3  # its definition and one call in each caller
    assert len(re.findall(r"\bconnect\(", js)) == 2  # definition, click handler
    wire = _body_of(js, "wireLive")
    assert "$('#ask-live').addEventListener('click', () => {" in wire and "connect()" in wire
    assert "$('#ask-form').addEventListener('submit', ask)" in wire
    assert len(re.findall(r"[(, ]ask\)", js)) == 1  # ask is passed only to the submit listener
    main = (DOCS / "js" / "main.js").read_text(encoding="utf-8")
    assert "wireLive()" in main and "callLocal" not in main


def test_routable_facts_have_what_the_ui_reads(hero: dict[str, Any]) -> None:
    for fact in hero["facts"]:
        approval, tier = fact["approval"], fact["risk"]["tier"]
        assert "valid_from_t" in approval
        if tier in ("T1", "T2"):
            assert approval.get("ttl_s", 0) > 0, fact["fact_id"]
        kinds = {(r["kind"], r["lang"]) for r in fact["renditions"]}
        for need in [
            ("cue", "en"),
            ("cue", "es"),
            ("caption", "en"),
            ("caption", "es"),
            ("audio_script", "en"),
        ]:
            assert need in kinds, f"{fact['fact_id']} lacks {need}"
        assert all(r["numbers_preserved"] for r in fact["renditions"]), fact["fact_id"]


def test_rejected_drafts_carry_a_reason(hero: dict[str, Any]) -> None:
    for fact in hero["log"]:
        assert fact["verification"]["status"] == "rejected"
        assert fact["verification"]["reject_reason"]


def test_cue_feed_has_t0_t1_in_both_languages_and_no_t2(hero: dict[str, Any]) -> None:
    cues = json.loads((DOCS / "data" / "cues.json").read_text(encoding="utf-8"))
    langs: dict[str, set[str]] = {}
    for cue in cues:
        langs.setdefault(cue["fact_id"], set()).add(cue["lang"])
    for fact in hero["facts"]:
        expected = {"en", "es"} if fact["risk"]["tier"] in ("T0", "T1") else None
        assert langs.get(fact["fact_id"]) == expected, fact["fact_id"]


def test_shell_has_the_sidebar_header_and_kpi_row() -> None:
    """R2 (design/redesign_mockup.png): three view tabs, the Replay group, header pills and the
    five KPI cards, and (R5) the Ask PitchSide entry and panel with its build-time label."""
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    tabs = re.findall(r'role="tab"[^>]*data-view="(\w+)"', html)
    assert tabs == ["bc", "an", "au"]
    assert "Suggested answers were generated at build time and checked by the verifier" in html
    assert "Live questions run locally" in html and "chatbot" not in html.lower()
    assert re.search(r'id="ask-text"[^>]*disabled', html)  # until the local server answers
    assert re.search(r'id="ask-send"[^>]*disabled', html)
    assert not re.search(r'id="ask-live"[^>]*disabled', html)
    for id_ in [
        "go-ask",
        "ask",
        "go-cues",
        "go-ev",
        "go-set",
        "built",
        "speed",
        "depth",
        "lang",
        "auto",
        "theme",
        "dl",
    ]:
        assert f'id="{id_}"' in html, id_
    cards = re.findall(r'<div class="kpi" id="kpi-(\w+)"', html)
    assert cards == ["npxg", "tilt", "wp", "ver", "air"]
    assert "model estimate" in html
