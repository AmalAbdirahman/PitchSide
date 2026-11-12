from __future__ import annotations

import json

from app.drafter import draft_facts
from app.verifier import validate_fact, verify
from core.config import ROOT, load
from core.events import Match
from core.facts import Fact
from core.moments import detect_moments
from tests.conftest import SeededClaim

S = load()


def _drafts(match: Match) -> list[Fact]:
    mu = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))[
        "mu_xg_per_team_minute"
    ]
    return draft_facts(detect_moments(match.events, ("NOR", "HAR"), mu, S), match, S)


def test_schema_validates_every_fact(match: Match, seeded: list[SeededClaim]) -> None:
    facts = _drafts(match) + [c.fact for c in seeded]
    assert facts
    for fact in facts:
        validate_fact(fact)
        validate_fact(verify(fact, match, S))


def test_drafter_tags_every_injected_category(match: Match) -> None:
    injected = [f for f in _drafts(match) if f.injected]
    assert len(injected) >= 6
    assert all(verify(f, match, S).verification.status != "verified" for f in injected)
