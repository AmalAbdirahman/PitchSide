"""Deterministic corrections the verifier may make to a draft's wording.

Each function returns the new text (and numbers) with a Correction record, or
nothing. None of them change the fact; the orchestrator in app/verifier.py does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.checks import Sample
from core.config import Verifier
from core.events import Match
from core.facts import Correction, Fact, TextNumber
from core.metrics import Metric
from core.moments import ordinal

HEDGE_LEVEL = {"early": 0, "suggests": 1, "clear": 2, "none": 2}
FIELD = "text.canonical_en"


@dataclass(frozen=True)
class TextFix:
    text: str
    numbers: list[TextNumber]
    correction: Correction


def strip_intent(text: str, patterns: tuple[str, ...]) -> tuple[str, list[Correction]]:
    """Remove words asserting intent. Returns new text and corrections."""
    new = text
    for pat in patterns:
        new = re.sub(pat, "", new, flags=re.IGNORECASE)
    new = re.sub(r"\s+([.,:;])", r"\1", re.sub(r"\s{2,}", " ", new)).strip()
    if new == text:
        return text, []
    reason = "intent: words asserting intent removed; only the observable part is kept"
    return new, [Correction(field=FIELD, from_=text, to=new, reason=reason)]


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
    since = match.meta.clock.minute(claim.window.start_t)
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


def hedge_text(fact: Fact, m: Metric, sample: Sample, match: Match, v: Verifier) -> TextFix | None:
    """Restate a too-small sample as a plain count, or soften wording stronger than the hedge."""
    if sample.hedge == "none":
        return None
    text = fact.text.canonical_en
    if m.n < v.n_min:
        new, nums = _count_text(fact, m, match)
        reason = (
            f"sample_size: n={m.n} is below {v.n_min}; restated as a plain count with hedge early"
        )
    elif _strength(text, v) > HEDGE_LEVEL[sample.hedge]:
        new, nums = _soften(text, sample.hedge, match), fact.text.numbers
        reason = f"hedge: wording was stronger than '{sample.hedge}' allows"
    else:
        return None
    return TextFix(new, nums, Correction(field=FIELD, from_=text, to=new, reason=reason))
