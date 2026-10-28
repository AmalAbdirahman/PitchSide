"""Risk router (SPEC section 10). Only verified or corrected facts are routed.

T0  plain event-log facts (goal, substitution, card) that verified cleanly: air at once.
T1  verified, no corrections, |impact| < 0.10, hedge suggests/clear/none: air after a veto window.
T2  corrected, |impact| >= 0.10, names a player negatively, or hedge early: queue for a human,
    dropped (never aired) when the expiry runs out.
`resolve` is the reference for what happens over time; the static UI mirrors it.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from app.verifier import validate_fact
from core.config import load

Fact = dict[str, Any]
AUTOPILOT_OK = "autopilot_ok"


def names_player_negatively(fact: Fact) -> bool:
    """A player is named and the claim is about something going wrong."""
    r = load()["router"]
    named = fact["claim"]["subject"]["type"] == "player" or bool(fact["evidence"].get("players"))
    if not named:
        return False
    text = fact["text"]["canonical_en"].lower()
    words = any(re.search(rf"\b{re.escape(w)}\b", text) for w in r["negative_words"])
    return fact["claim"]["metric"] in r["negative_metrics"] or words


def tier_for(fact: Fact) -> tuple[str, list[str]]:
    """Decide the tier and the reasons for it."""
    r, v = load()["router"], fact["verification"]
    corrected = v["status"] == "corrected"
    hedge = fact.get("uncertainty", {}).get("hedge", "none")
    impact = abs(fact.get("impact", {}).get("delta", 0.0))
    if fact["claim"]["metric"] in r["t0_metrics"] and not corrected:
        return "T0", ["plain event-log fact"]
    reasons = []
    if corrected:
        reasons.append("verifier corrected the draft")
    if impact >= r["impact_t2"]:
        reasons.append(f"impact {impact:.2f} >= {r['impact_t2']:.2f}")
    if names_player_negatively(fact):
        reasons.append("names a player negatively")
    if hedge == "early":
        reasons.append("hedge early (small sample)")
    if reasons:
        return "T2", reasons
    return "T1", [f"verified, no corrections, impact {impact:.2f}, hedge {hedge}"]


def autopilot_ok(fact: Fact) -> bool:
    """Autopilot may approve a T2 fact only if verification was clean and the hedge is clear."""
    return fact["verification"]["status"] == "verified" and fact.get("uncertainty", {}).get("hedge") == "clear"


def route(fact: Fact) -> Fact:
    """Return a copy of the fact with risk tier and initial approval state set."""
    status = fact["verification"]["status"]
    if status not in ("verified", "corrected"):
        raise ValueError(f"{fact['fact_id']} is {status}; only verified or corrected facts are routed")
    r = load()["router"]
    out = copy.deepcopy(fact)
    tier, reasons = tier_for(out)
    if tier == "T2" and autopilot_ok(out):
        reasons.append(AUTOPILOT_OK)
    out["risk"] = {"tier": tier, "reasons": reasons}
    approval: dict[str, Any] = {"state": {"T0": "auto_approved", "T1": "veto_window", "T2": "queued"}[tier],
                                "valid_from_t": out["created_t"]}
    if tier == "T0":
        approval["decided_by"] = "auto"
    else:
        approval["ttl_s"] = r["veto_window_s"] if tier == "T1" else r["t2_ttl_s"]
    out["approval"] = approval
    validate_fact(out)
    return out


def resolve(fact: Fact, elapsed_s: float, decision: str | None = None, decision_at_s: float | None = None,
            autopilot: bool = False) -> tuple[str, str | None]:
    """Approval state after `elapsed_s` seconds on air-side, given an optional producer decision.

    Returns (state, decided_by). Decisions made after the window or expiry are ignored.
    """
    tier, ttl = fact["risk"]["tier"], fact["approval"].get("ttl_s", 0)
    in_time = decision is not None and decision_at_s is not None and decision_at_s < ttl and decision_at_s <= elapsed_s
    if tier == "T0":
        return "auto_approved", "auto"
    if tier == "T1":
        if in_time and decision == "veto":
            return "vetoed", "producer"
        return ("auto_approved", "auto") if elapsed_s >= ttl else ("veto_window", None)
    if in_time and decision in ("approve", "reject"):
        return ("approved" if decision == "approve" else "rejected"), "producer"
    if autopilot and AUTOPILOT_OK in fact["risk"]["reasons"]:
        return "approved", "autopilot"
    return ("expired", None) if elapsed_s >= ttl else ("queued", None)
