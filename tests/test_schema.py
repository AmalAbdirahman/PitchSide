from __future__ import annotations

import json
from typing import Any

from app.drafter import draft_facts
from app.verifier import validate_fact, verify
from core.config import ROOT
from core.moments import detect_moments


def test_schema_validates_every_fact(match: dict[str, Any], seeded: list[dict[str, Any]]) -> None:
    mu = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))["mu_xg_per_team_minute"]
    drafts = draft_facts(detect_moments(match["events"], ("NOR", "HAR"), mu), match)
    facts = drafts + [c["fact"] for c in seeded]
    assert facts
    for fact in facts:
        validate_fact(fact)
        validate_fact(verify(fact, match))


def test_drafter_tags_every_injected_category(match: dict[str, Any]) -> None:
    mu = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))["mu_xg_per_team_minute"]
    drafts = draft_facts(detect_moments(match["events"], ("NOR", "HAR"), mu), match)
    injected = [f for f in drafts if f["injected"]]
    assert len(injected) >= 6
    assert all(verify(f, match)["verification"]["status"] != "verified" for f in injected)
