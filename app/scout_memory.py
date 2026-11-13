"""The Scout's memory (P11): a compact record of what has already aired, and the duplicate rule.

Before each request the Scout gets the most recent facts that passed the verifier before the
moment (id, minute, metric, subject, sentence) and the last sentence about each team, so it can
avoid repeating itself and refer back. The fact ids it was shown are stored in the audit trail.
"""

from __future__ import annotations

from typing import Any

from core.events import Match
from core.facts import Fact

ROUTABLE = ("verified", "corrected")


def team_of(fact: Fact, match: Match) -> str | None:
    subj = fact.claim.subject
    if subj.type == "team":
        return subj.id
    if subj.type == "player":
        return match.meta.team_of(subj.id)
    return None


def aired_before(facts: list[Fact], now: float) -> list[Fact]:
    """Facts that passed the verifier and were made at or before `now`, oldest first."""
    ok = [f for f in facts if f.verification.status in ROUTABLE and f.created_t <= now]
    return sorted(ok, key=lambda f: (f.created_t, f.fact_id))


def memory_for(facts: list[Fact], now: float, match: Match, size: int) -> dict[str, Any]:
    aired = aired_before(facts, now)
    last: dict[str, str] = {}
    for f in aired:
        team = team_of(f, match)
        if team is not None:
            last[team] = f.text.canonical_en
    return {
        "aired": [
            {
                "fact_id": f.fact_id,
                "minute": match.meta.clock.minute(f.created_t),
                "metric": f.claim.metric,
                "subject": f.claim.subject.model_dump(),
                "text": f.text.canonical_en,
            }
            for f in aired[-size:]
        ],
        "last_by_team": dict(sorted(last.items())),
    }


def memory_ids(memory: dict[str, Any]) -> list[str]:
    return [str(a["fact_id"]) for a in memory["aired"]]


def is_duplicate(fact: Fact, earlier: list[Fact]) -> bool:
    """Same sentence as an earlier fact, or the same metric and subject over an overlapping window."""
    w = fact.claim.window
    for e in earlier:
        if e.text.canonical_en == fact.text.canonical_en:
            return True
        ew = e.claim.window
        same = e.claim.metric == fact.claim.metric and e.claim.subject == fact.claim.subject
        if same and w.start_t < ew.end_t and ew.start_t < w.end_t:
            return True
    return False


def duplicates(facts: list[Fact]) -> int:
    """How many facts that passed the verifier repeat an earlier one that did."""
    aired = aired_before(facts, float("inf"))
    return sum(is_duplicate(f, aired[:i]) for i, f in enumerate(aired))
