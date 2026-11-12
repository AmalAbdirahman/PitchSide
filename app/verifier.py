"""Deterministic verifier (SPEC sections 7 and 8). Code decides truth.

`verify(fact, match, settings)` returns a new fact with a verification record:
verified (all checks passed untouched), corrected (passed after deterministic
fixes), rejected (a check failed) or unverifiable (the metric cannot be recomputed).
"""

from __future__ import annotations

import re

from core.config import Settings, Verifier
from core.events import Event, Match
from core.facts import (
    Check,
    Claim,
    Correction,
    Fact,
    TextNumber,
    Uncertainty,
    Verification,
    validate_fact,
)
from core.moments import Metric, compute_metric, minute, ordinal
from core.wilson import hedge_for, wilson

__all__ = ["validate_fact", "verify"]

COUNT_METRICS = {"goals", "shots", "turnovers_own_third", "substitutions", "cards"}
SHARE_METRICS = {"attack_share_left", "attack_share_centre", "attack_share_right", "pass_accuracy"}
HEDGE_LEVEL = {"early": 0, "suggests": 1, "clear": 2, "none": 2}
NUMBER = re.compile(r"\d+(?:\.\d+)?")
ById = dict[int, Event]


def _check(name: str, passed: bool, detail: str) -> Check:
    return Check.model_validate({"name": name, "passed": passed, "detail": detail})


def tolerance(metric: str, v: Verifier) -> float:
    if metric in COUNT_METRICS:
        return 0.0
    if metric in SHARE_METRICS:
        return v.tol_share_pp
    return v.tol_xg if metric == "xg_sum" else v.tol_speed


# ---- individual checks -------------------------------------------------------


def strip_intent(text: str, patterns: tuple[str, ...]) -> tuple[str, list[Correction]]:
    """Remove words asserting intent. Returns new text and corrections."""
    new = text
    for pat in patterns:
        new = re.sub(pat, "", new, flags=re.IGNORECASE)
    new = re.sub(r"\s+([.,:;])", r"\1", re.sub(r"\s{2,}", " ", new)).strip()
    if new == text:
        return text, []
    reason = "intent: words asserting intent removed; only the observable part is kept"
    return new, [Correction(field="text.canonical_en", from_=text, to=new, reason=reason)]


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
    return _check(
        "evidence_exists",
        not problems,
        "; ".join(problems) or "all cited events exist, in window, in the past",
    )


def _cited(fact: Fact, by_id: ById) -> list[Event]:
    return [by_id[i] for i in fact.evidence.event_ids if i in by_id]


def check_entities(fact: Fact, by_id: ById, match: Match) -> Check:
    cited = _cited(fact, by_id)
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
    for pid, name in match.meta.names().items():
        if name in fact.text.canonical_en and pid not in players:
            problems.append(f"text names {name} ({pid}), who is not in the cited events")
    return _check(
        "entities", not problems, "; ".join(problems) or "teams and players match the cited events"
    )


def check_recompute(claim: Claim, m: Metric, v: Verifier) -> Check:
    obs, val, cmp = m.value, claim.value, claim.comparator
    if cmp == "eq":
        ok = abs(obs - val) <= tolerance(claim.metric, v) + 1e-9
    elif cmp == "lt":
        ok = obs < val
    else:
        ok = obs > val
    return _check(
        "recompute", ok, f"claimed {cmp} {val}, recomputed {round(obs, 3)} from {m.n} events"
    )


def _number_ok(
    num: TextNumber, fact: Fact, m: Metric, by_id: ById, match: Match, v: Verifier
) -> bool:
    key, val, claim = num.key, num.value, fact.claim
    cited = _cited(fact, by_id)
    if key == "value":
        tol = tolerance(claim.metric, v)
        return claim.comparator != "eq" or abs(val - m.value) <= tol + 1e-9
    if key in ("n", "k"):
        return bool(val == getattr(m, key))
    if key == "minute":
        return any(minute(e.t) == val for e in cited)
    if key == "since_minute":
        return minute(claim.window.start_t) == val
    if key.startswith("score_") and cited:
        team, t = key.removeprefix("score_"), max(e.t for e in cited)
        return val == sum(e.type == "goal" and e.team == team and e.t <= t for e in match.events)
    return False


def check_numbers(fact: Fact, m: Metric, by_id: ById, match: Match, v: Verifier) -> Check:
    listed, problems = fact.text.numbers, []
    for token in NUMBER.findall(fact.text.canonical_en):
        if not any(abs(float(token) - n.value) < 1e-9 for n in listed):
            problems.append(f"{token} in the text is not in text.numbers")
    for num in listed:
        if not _number_ok(num, fact, m, by_id, match, v):
            problems.append(f"{num.key}={num.value} does not match the event log")
    return _check(
        "numbers_match", not problems, "; ".join(problems) or "every number traced to the event log"
    )


# ---- sample size and hedge ----------------------------------------------------


def _strength(text: str, v: Verifier) -> int:
    low = text.lower()
    if any(w in low for w in v.early_markers):
        return 0
    return 1 if any(w in low for w in v.suggest_markers) else 2


def _count_text(fact: Fact, m: Metric, match: Match) -> tuple[str, list[TextNumber]]:
    claim = fact.claim
    team = match.meta.team_name(claim.subject.id)
    if claim.metric == "pass_accuracy":
        return f"Early sign: {team} completed {m.k} of {m.n} passes in this spell.", [
            TextNumber(key="k", value=m.k),
            TextNumber(key="n", value=m.n),
        ]
    since = minute(claim.window.start_t)
    lane = claim.metric.removeprefix("attack_share_")
    return (
        f"Early sign: {m.k} of {m.n} {team} attacks have gone down the {lane} since the {ordinal(since)} minute.",
        [
            TextNumber(key="k", value=m.k),
            TextNumber(key="n", value=m.n),
            TextNumber(key="since_minute", value=since),
        ],
    )


def _soften(text: str, hedge: str, match: Match) -> str:
    if hedge == "early":
        return f"Early sign: {text}"
    starts_with_name = any(text.startswith(t.name) for t in match.meta.teams.values())
    return f"The numbers suggest that {text if starts_with_name else text[0].lower() + text[1:]}"


def calibrate(fact: Fact, m: Metric, match: Match, v: Verifier) -> tuple[Check, list[Correction]]:
    """Set uncertainty and hedge; rewrite text that sounds stronger than the evidence."""
    claim, text = fact.claim, fact.text.canonical_en
    if claim.metric not in SHARE_METRICS:
        fact.uncertainty = Uncertainty(n=m.n, ci_method="none", hedge="none")
        return _check("sample_size", True, "plain value from the event log; no inference"), []
    lo, hi = wilson(m.k, m.n, v.z)
    base = claim.baseline
    hedge = hedge_for(m.n, lo, hi, None if base is None else base / 100, v.n_min)
    fact.uncertainty = Uncertainty.model_validate(
        {
            "n": m.n,
            "ci_method": "wilson",
            "ci_low": round(lo, 4),
            "ci_high": round(hi, 4),
            "hedge": hedge,
        }
    )
    if m.n < v.n_min:
        new, nums = _count_text(fact, m, match)
        reason = (
            f"sample_size: n={m.n} is below {v.n_min}; restated as a plain count with hedge early"
        )
    elif _strength(text, v) > HEDGE_LEVEL[hedge]:
        new, nums = _soften(text, hedge, match), fact.text.numbers
        reason = f"hedge: wording was stronger than '{hedge}' allows"
    else:
        return _check(
            "sample_size", True, f"n={m.n}, Wilson [{lo:.2f}, {hi:.2f}], hedge {hedge}"
        ), []
    fact.text.canonical_en, fact.text.numbers = new, nums
    return (
        _check("sample_size", True, reason),
        [Correction(field="text.canonical_en", from_=text, to=new, reason=reason)],
    )


# ---- entry point ------------------------------------------------------------------


def _status(checks: list[Check], corrections: list[Correction], computable: bool) -> str:
    failed = [c.name for c in checks if not c.passed]
    if not computable:
        return "rejected" if any(n != "recompute" for n in failed) else "unverifiable"
    if failed:
        return "rejected"
    return "corrected" if corrections else "verified"


def verify(fact: Fact, match: Match, s: Settings) -> Fact:
    """Run every check on a copy of the fact and return it with a verification record."""
    v = s.verifier
    out = fact.model_copy(deep=True)
    by_id = {e.id: e for e in match.events}
    known = [e for e in match.events if e.t <= out.created_t]
    if out.text.draft_en is None:
        out.text.draft_en = out.text.canonical_en
    out.text.canonical_en, corrections = strip_intent(out.text.canonical_en, v.intent_patterns)
    checks = [
        _check(
            "intent_stripped", True, "intent words removed" if corrections else "no intent words"
        ),
        check_evidence(out, by_id),
        check_entities(out, by_id, match),
    ]
    claim = out.claim
    m = compute_metric(claim.metric, claim.subject, claim.window, known, s.pitch)
    if m is not None and claim.metric in SHARE_METRICS and m.n == 0:
        m = None  # a share of nothing cannot be stated
    if m is None:
        checks.append(
            _check(
                "recompute", False, f"{claim.metric} cannot be recomputed from the event log here"
            )
        )
    else:
        checks.append(check_recompute(claim, m, v))
        sample, fixes = calibrate(out, m, match, v)
        checks += [sample, check_numbers(out, m, by_id, match, v)]
        corrections += fixes
    status = _status(checks, corrections, m is not None)
    failed = "; ".join(c.detail or "" for c in checks if not c.passed)
    out.verification = Verification.model_validate(
        {
            "status": status,
            "checks": checks,
            "corrections": corrections or None,
            "reject_reason": failed if status in ("rejected", "unverifiable") else None,
        }
    )
    validate_fact(out)
    return out
