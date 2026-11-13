"""Pure verifier checks. Each one reads a fact and the event log and returns a Check.

Nothing here changes the fact; corrections live in app/corrections.py and the
orchestrator in app/verifier.py applies them and records both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.names import name_problems, named_players, roster
from core.config import Verifier
from core.events import Event, Match
from core.facts import Check, Claim, Fact, TextNumber, Uncertainty
from core.metrics import Metric, MetricDef
from core.wilson import hedge_for, wilson

NUMBER = re.compile(r"\d+(?:\.\d+)?")
ById = dict[int, Event]


def make_check(name: str, passed: bool, detail: str) -> Check:
    return Check.model_validate({"name": name, "passed": passed, "detail": detail})


def cited_events(fact: Fact, by_id: ById) -> list[Event]:
    return [by_id[i] for i in fact.evidence.event_ids if i in by_id]


# ---- evidence and entities ----------------------------------------------------------


def check_evidence(fact: Fact, by_id: ById) -> Check:
    w, created, problems = fact.claim.window, fact.created_t, []
    for i in fact.evidence.event_ids:
        e = by_id.get(i)
        if e is None:
            problems.append(f"event {i} does not exist")
        elif e.t > created:
            problems.append(f"event {i} at t={e.t} is after the claim was made (t={created})")
        elif not w.start_t <= e.t <= w.end_t:
            problems.append(f"event {i} at t={e.t} is outside the claim window")
    if w.end_t > created:
        problems.append(f"claim window ends at t={w.end_t}, after the claim was made (t={created})")
    return make_check(
        "evidence_exists",
        not problems,
        "; ".join(problems) or "all cited events exist, in window, in the past",
    )


def check_entities(fact: Fact, by_id: ById, match: Match, v: Verifier) -> Check:
    cited = cited_events(fact, by_id)
    teams = {e.team for e in cited}
    players = {e.player_id for e in cited} | {e.player_off for e in cited if e.player_off}
    subj, problems = fact.claim.subject, []
    if subj.type == "team" and subj.id not in teams:
        problems.append(f"team {subj.id} does not appear in the cited events")
    if subj.type == "player" and subj.id not in players:
        problems.append(f"player {subj.id} does not appear in the cited events")
    for pid in fact.evidence.players or []:
        if pid not in players:
            problems.append(f"named player {pid} does not appear in the cited events")
    text, squad = fact.text.canonical_en, roster(match.meta)
    named = named_players(text, squad)
    for p in squad:
        if p.pid in named and p.pid not in players:
            problems.append(f"text names {p.full} ({p.pid}), who is not in the cited events")
    teams_named = {t.name for t in match.meta.teams.values()}
    problems += name_problems(text, squad, teams_named, v.name_max_edits, v.name_min_length)
    return make_check(
        "entities", not problems, "; ".join(problems) or "teams and players match the cited events"
    )


# ---- recompute and numbers ----------------------------------------------------------


def check_recompute(claim: Claim, m: Metric, d: MetricDef, v: Verifier) -> Check:
    obs, val, cmp = m.value, claim.value, claim.comparator
    if cmp == "eq":
        ok = d.value_matches(val, obs, v)
    elif cmp == "lt":
        ok = obs < val
    else:
        ok = obs > val
    return make_check(
        "recompute", ok, f"claimed {cmp} {val}, recomputed {round(obs, 3)} from {m.n} events"
    )


def _number_ok(
    num: TextNumber, fact: Fact, m: Metric, d: MetricDef, by_id: ById, match: Match, v: Verifier
) -> bool:
    key, val, claim = num.key, num.value, fact.claim
    cited = cited_events(fact, by_id)
    if key == "value":
        return claim.comparator != "eq" or d.value_matches(val, m.value, v)
    if key in ("n", "k"):
        return bool(val == getattr(m, key))
    if key == "minute":
        return any(match.meta.clock.minute(e.t) == val for e in cited)
    if key == "since_minute":
        return match.meta.clock.minute(claim.window.start_t) == val
    if key.startswith("score_") and cited:
        team, t = key.removeprefix("score_"), max(e.t for e in cited)
        return val == sum(e.type == "goal" and e.team == team and e.t <= t for e in match.events)
    return False


def check_numbers(
    fact: Fact, m: Metric, d: MetricDef, by_id: ById, match: Match, v: Verifier
) -> Check:
    listed, problems = fact.text.numbers, []
    for token in NUMBER.findall(fact.text.canonical_en):
        if not any(abs(float(token) - n.value) < 1e-9 for n in listed):
            problems.append(f"{token} in the text is not in text.numbers")
    for num in listed:
        if not _number_ok(num, fact, m, d, by_id, match, v):
            problems.append(f"{num.key}={num.value} does not match the event log")
    return make_check(
        "numbers_match", not problems, "; ".join(problems) or "every number traced to the event log"
    )


# ---- sample size --------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    """What the evidence supports: a plain value, or a share with a Wilson interval and hedge."""

    n: int
    hedge: str
    lo: float | None = None
    hi: float | None = None

    def uncertainty(self) -> Uncertainty:
        if self.lo is None or self.hi is None:
            return Uncertainty(n=self.n, ci_method="none", hedge="none")
        return Uncertainty.model_validate(
            {
                "n": self.n,
                "ci_method": "wilson",
                "ci_low": round(self.lo, 4),
                "ci_high": round(self.hi, 4),
                "hedge": self.hedge,
            }
        )


def assess_sample(claim: Claim, m: Metric, d: MetricDef, v: Verifier) -> Sample:
    if not d.checks_sample:
        return Sample(n=m.n, hedge="none")
    lo, hi = wilson(m.k, m.n, v.z)
    base = claim.baseline
    hedge = hedge_for(m.n, lo, hi, None if base is None else base / 100, v.n_min)
    return Sample(n=m.n, hedge=hedge, lo=lo, hi=hi)


def check_sample(sample: Sample, correction_reason: str | None) -> Check:
    """Always passes: a weak sample is corrected (hedged or restated), not rejected."""
    if correction_reason is not None:
        return make_check("sample_size", True, correction_reason)
    if sample.lo is None or sample.hi is None:
        return make_check("sample_size", True, "plain value from the event log; no inference")
    return make_check(
        "sample_size",
        True,
        f"n={sample.n}, Wilson [{sample.lo:.2f}, {sample.hi:.2f}], hedge {sample.hedge}",
    )
