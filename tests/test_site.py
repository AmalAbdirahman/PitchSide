"""Static site checks: assets exist, no network, and the bundle carries what app.js reads."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from core.config import ROOT

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


def test_app_fetches_only_local_data() -> None:
    js = (DOCS / "app.js").read_text(encoding="utf-8")
    for path in re.findall(r"getJson\('([^']+)'\)", js):
        assert (DOCS / path).is_file(), f"app.js fetches missing file {path}"


@pytest.mark.parametrize("path", PUBLISHED, ids=lambda p: p.relative_to(DOCS).as_posix())
def test_no_network_urls(path: Path) -> None:
    """No file under docs/ may reference http(s):// at all, not even an XML namespace."""
    bad = URL.findall(path.read_text(encoding="utf-8", errors="replace"))
    assert not bad, f"{path.relative_to(DOCS)} references the network: {bad}"


def test_routable_facts_have_what_the_ui_reads(hero: dict[str, Any]) -> None:
    for fact in hero["facts"]:
        approval, tier = fact["approval"], fact["risk"]["tier"]
        assert "valid_from_t" in approval
        if tier in ("T1", "T2"):
            assert approval.get("ttl_s", 0) > 0, fact["fact_id"]
        kinds = {(r["kind"], r["lang"]) for r in fact["renditions"]}
        for need in [("cue", "en"), ("cue", "es"), ("caption", "en"), ("caption", "es"), ("audio_script", "en")]:
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
