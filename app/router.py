"""Risk router (SPEC section 10). Only verified or corrected facts are routed.

T0  plain event-log facts (goal, substitution, card) that verified cleanly: air at once.
T1  verified, no corrections, |impact| < 0.10, hedge suggests/clear/none: air after a veto window.
T2  corrected, |impact| >= 0.10, names a player negatively, or hedge early: queue for a human,
    dropped (never aired) when the expiry runs out.

What happens over time is data, not code: `routing_rules` builds a table from config, the
bundle carries it (hero.json meta.routing), and both `resolve` here and docs/js/routing.js
interpret the same table. tests/routing_cases.json runs against both.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.config import Router
from core.facts import Approval, Fact, Risk, Tier, validate_fact

AUTOPILOT_OK = "autopilot_ok"
State = Literal[
    "auto_approved", "veto_window", "queued", "vetoed", "approved", "rejected", "expired"
]
DecidedBy = Literal["auto", "producer", "autopilot"]
Decision = Literal["veto", "approve", "reject", "autopilot"]


class _Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Outcome(_Rule):
    state: State
    by: DecidedBy | None = None


class TierRule(_Rule):
    """Where a fact of this tier starts, what may change it, and what its expiry does."""

    initial: Outcome
    ttl_s: int | None = None
    decisions: dict[Decision, Outcome] = {}
    on_timeout: Outcome | None = None


class RoutingRules(_Rule):
    tiers: dict[Tier, TierRule]
    # An autopilot decision counts only for facts the router marked with this reason.
    autopilot_reason: str


def routing_rules(r: Router) -> RoutingRules:
    producer = "producer"
    return RoutingRules.model_validate(
        {
            "tiers": {
                "T0": {"initial": {"state": "auto_approved", "by": "auto"}},
                "T1": {
                    "initial": {"state": "veto_window"},
                    "ttl_s": r.veto_window_s,
                    "decisions": {"veto": {"state": "vetoed", "by": producer}},
                    "on_timeout": {"state": "auto_approved", "by": "auto"},
                },
                "T2": {
                    "initial": {"state": "queued"},
                    "ttl_s": r.t2_ttl_s,
                    "decisions": {
                        "approve": {"state": "approved", "by": producer},
                        "reject": {"state": "rejected", "by": producer},
                        "autopilot": {"state": "approved", "by": "autopilot"},
                    },
                    "on_timeout": {"state": "expired"},
                },
            },
            "autopilot_reason": AUTOPILOT_OK,
        }
    )


# ---- tiers ----------------------------------------------------------------------------


def names_player_negatively(fact: Fact, r: Router) -> bool:
    """A player is named and the claim is about something going wrong."""
    named = fact.claim.subject.type == "player" or bool(fact.evidence.players)
    if not named:
        return False
    text = fact.text.canonical_en.lower()
    words = any(re.search(rf"\b{re.escape(w)}\b", text) for w in r.negative_words)
    return fact.claim.metric in r.negative_metrics or words


def tier_for(fact: Fact, r: Router) -> tuple[Tier, list[str]]:
    """Decide the tier and the reasons for it."""
    corrected = fact.verification.status == "corrected"
    hedge = fact.uncertainty.hedge if fact.uncertainty else "none"
    impact = abs(fact.impact.delta) if fact.impact else 0.0
    if fact.claim.metric in r.t0_metrics and not corrected:
        return "T0", ["plain event-log fact"]
    reasons = []
    if corrected:
        reasons.append("verifier corrected the draft")
    if impact >= r.impact_t2:
        reasons.append(f"impact {impact:.2f} >= {r.impact_t2:.2f}")
    if names_player_negatively(fact, r):
        reasons.append("names a player negatively")
    if hedge == "early":
        reasons.append("hedge early (small sample)")
    if reasons:
        return "T2", reasons
    return "T1", [f"verified, no corrections, impact {impact:.2f}, hedge {hedge}"]


def autopilot_ok(fact: Fact) -> bool:
    """Autopilot may approve a T2 fact only if verification was clean and the hedge is clear."""
    hedge = fact.uncertainty.hedge if fact.uncertainty else None
    return fact.verification.status == "verified" and hedge == "clear"


def route(fact: Fact, r: Router) -> Fact:
    """Return a copy of the fact with risk tier and initial approval state set."""
    status = fact.verification.status
    if status not in ("verified", "corrected"):
        raise ValueError(f"{fact.fact_id} is {status}; only verified or corrected facts are routed")
    out = fact.model_copy(deep=True)
    tier, reasons = tier_for(out, r)
    if tier == "T2" and autopilot_ok(out):
        reasons.append(AUTOPILOT_OK)
    rule = routing_rules(r).tiers[tier]
    out.risk = Risk.model_validate({"tier": tier, "reasons": reasons})
    out.approval = Approval.model_validate(
        {
            "state": rule.initial.state,
            "valid_from_t": out.created_t,
            "decided_by": rule.initial.by,
            "ttl_s": rule.ttl_s,
        }
    )
    validate_fact(out)
    return out


# ---- over time ---------------------------------------------------------------------------


def resolve_state(
    rules: RoutingRules,
    tier: Tier,
    ttl_s: float,
    reasons: list[str],
    elapsed_s: float,
    decision: str | None = None,
    decision_at_s: float | None = None,
) -> tuple[str, str | None]:
    """State and who decided, `elapsed_s` seconds after a fact was routed.

    `decision` is the first decision taken (by the producer, or by autopilot when it is switched
    on, at 0 if it was already on). It counts only if the tier allows it and it came before the
    expiry; a later decision is ignored. Autopilot counts only for facts marked autopilot_ok.
    """
    rule = rules.tiers[tier]
    if rule.on_timeout is None:
        return rule.initial.state, rule.initial.by
    outcome = next((o for d, o in rule.decisions.items() if d == decision), None)
    in_time = decision_at_s is not None and decision_at_s < ttl_s and decision_at_s <= elapsed_s
    allowed = decision != "autopilot" or rules.autopilot_reason in reasons
    if outcome is not None and in_time and allowed:
        return outcome.state, outcome.by
    if elapsed_s >= ttl_s:
        return rule.on_timeout.state, rule.on_timeout.by
    return rule.initial.state, rule.initial.by


def resolve(
    rules: RoutingRules,
    fact: Fact,
    elapsed_s: float,
    decision: str | None = None,
    decision_at_s: float | None = None,
) -> tuple[str, str | None]:
    """resolve_state for a routed fact."""
    if fact.risk is None:
        raise ValueError(f"{fact.fact_id} has not been routed")
    return resolve_state(
        rules,
        fact.risk.tier,
        fact.approval.ttl_s or 0,
        fact.risk.reasons or [],
        elapsed_s,
        decision,
        decision_at_s,
    )
