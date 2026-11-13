"""The meaning check (P12): does the sentence say what the claim says?

The Reader agent reads only the sentence and the roster and writes down the claim it understood
(core/facts.Reading). This module compares that reading with the Scout's structured claim, in
code. The numbers check already proves the sentence's numbers are right; this check catches a
sentence whose numbers are right but whose meaning is not ("69% of centre attacks" against "69%
of attacks went through the centre"): another metric, another subject, another window or another
value.

A window the sentence does not state ("Harlow had 0.86 xG.") passes: the sentence is vaguer than
the claim, not different from it.
"""

from __future__ import annotations

from app.checks import make_check
from core.config import Verifier
from core.events import Clock
from core.facts import Check, Claim, Reading
from core.metrics import REGISTRY


def _subject_problem(r: Reading, claim: Claim) -> str | None:
    got, want = (r.subject.type, r.subject.id), (claim.subject.type, claim.subject.id)
    return None if got == want else f"subject {got[0]} {got[1]}, claim {want[0]} {want[1]}"


def _window_problem(r: Reading, claim: Claim, clock: Clock) -> str | None:
    w, start, end = r.window, claim.window.start_t, claim.window.end_t
    if w.kind == "unstated":
        return None
    if w.kind == "first_half":
        ok = end <= clock.half_t
    elif w.kind == "second_half":
        ok = start >= clock.half_t
    else:  # since_minute / at_minute: the sentence names the window's first minute
        ok = w.minute is not None and w.minute == clock.minute(start)
    if ok:
        return None
    said = w.kind.replace("_", " ") + (f" {w.minute}" if w.minute is not None else "")
    return f"window '{said}', claim from minute {clock.minute(start)} to {clock.minute(end)}"


def _value_problem(r: Reading, claim: Claim, v: Verifier) -> str | None:
    d = REGISTRY.get(claim.metric)
    if d is None or d.value_matches(r.value, claim.value, v):
        return None
    return f"value {r.value}, claim {claim.value}"


def meaning_problems(r: Reading, claim: Claim, clock: Clock, v: Verifier) -> list[str]:
    """Every way the reading differs from the claim; empty if they agree."""
    if r.metric is None:
        return ["the sentence states no listed metric"]
    if r.metric != claim.metric:
        return [f"metric {r.metric}, claim {claim.metric}"]
    found = (
        _subject_problem(r, claim),
        _window_problem(r, claim, clock),
        _value_problem(r, claim, v),
    )
    return [p for p in found if p is not None]


def check_meaning(r: Reading, claim: Claim, clock: Clock, v: Verifier) -> Check:
    problems = meaning_problems(r, claim, clock, v)
    detail = "the Reader read the claim back" if not problems else "; ".join(problems)
    return make_check("meaning_matches", not problems, f"meaning: {detail}")
