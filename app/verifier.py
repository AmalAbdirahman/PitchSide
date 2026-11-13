"""Deterministic verifier (SPEC sections 7 and 8). Code decides truth.

`verify(fact, match, settings)` returns a new fact with a verification record:
verified (all checks passed untouched), corrected (passed after deterministic
fixes), rejected (a check failed) or unverifiable (the metric cannot be recomputed).

This module only orchestrates: the checks are in app/checks.py (pure) and the
wording fixes in app/corrections.py. The metric definitions are in core/metrics.py.
"""

from __future__ import annotations

from app.checks import (
    assess_sample,
    check_entities,
    check_evidence,
    check_numbers,
    check_recompute,
    check_sample,
    make_check,
)
from app.corrections import hedge_text, strip_intent
from core.config import MetricContext, Settings
from core.events import Match
from core.facts import Check, Correction, Fact, Verification, validate_fact
from core.metrics import REGISTRY, compute_metric

__all__ = ["validate_fact", "verify"]


def _status(checks: list[Check], corrections: list[Correction], computable: bool) -> str:
    failed = [c.name for c in checks if not c.passed]
    if not computable:
        return "rejected" if any(n != "recompute" for n in failed) else "unverifiable"
    if failed:
        return "rejected"
    return "corrected" if corrections else "verified"


def _record(checks: list[Check], corrections: list[Correction], computable: bool) -> Verification:
    status = _status(checks, corrections, computable)
    failed = "; ".join(c.detail or "" for c in checks if not c.passed)
    return Verification.model_validate(
        {
            "status": status,
            "checks": checks,
            "corrections": corrections or None,
            "reject_reason": failed if status in ("rejected", "unverifiable") else None,
        }
    )


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
        make_check(
            "intent_stripped", True, "intent words removed" if corrections else "no intent words"
        ),
        check_evidence(out, by_id),
        check_entities(out, by_id, match, v),
    ]
    claim = out.claim
    d = REGISTRY.get(claim.metric)
    m = compute_metric(claim.metric, claim.subject, claim.window, known, MetricContext.of(s))
    if m is not None and d is not None and d.checks_sample and m.n == 0:
        m = None  # a share of nothing cannot be stated
    if m is None or d is None:
        detail = f"{claim.metric} cannot be recomputed from the event log here"
        checks.append(make_check("recompute", False, detail))
    else:
        checks.append(check_recompute(claim, m, d, v))
        sample = assess_sample(claim, m, d, v)
        out.uncertainty = sample.uncertainty()
        fix = hedge_text(out, m, sample, match, v)
        if fix is not None:
            out.text.canonical_en, out.text.numbers = fix.text, fix.numbers
            corrections.append(fix.correction)
        reason = fix.correction.reason if fix else None
        checks += [check_sample(sample, reason), check_numbers(out, m, d, by_id, match, v)]
    out.verification = _record(checks, corrections, m is not None)
    validate_fact(out)
    return out
