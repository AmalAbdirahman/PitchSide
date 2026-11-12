"""Tier routing table (SPEC section 10) and expiry rules."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.router import AUTOPILOT_OK, resolve, route
from app.verifier import verify
from core.config import load
from core.events import Match
from core.facts import Correction, Fact, Impact
from tests.conftest import seeded_of

S = load()


def _good(metric: str, match: Match, hedge: str | None = None) -> Fact:
    for entry in seeded_of("good"):
        out = verify(entry.fact, match, S)
        out_hedge = out.uncertainty.hedge if out.uncertainty else None
        if out.claim.metric.startswith(metric) and (hedge is None or out_hedge == hedge):
            return out
    raise LookupError(metric)


def _corrected(f: Fact) -> Fact:
    f.verification.status = "corrected"
    f.verification.corrections = [
        Correction(field="text.canonical_en", from_="a", to="b", reason="intent")
    ]
    return f


def _impact(delta: float) -> Callable[[Fact], Fact]:
    def apply(f: Fact) -> Fact:
        f.impact = Impact(delta=delta, method="poisson_v1")
        return f

    return apply


def _negative_player(f: Fact) -> Fact:
    f.evidence.players = ["HAR_04"]
    f.text.canonical_en = "V. Seddon made an error."
    return f


def _early(f: Fact) -> Fact:
    assert f.uncertainty is not None
    f.uncertainty.hedge = "early"
    return f


def _same(f: Fact) -> Fact:
    return f


# (case id, base metric, base hedge, tweak, expected tier)
TABLE = [
    ("goal_is_T0", "goals", None, _same, "T0"),
    ("substitution_is_T0", "substitutions", None, _same, "T0"),
    ("card_is_T0", "cards", None, _same, "T0"),
    ("goal_big_impact_still_T0", "goals", None, _impact(0.3), "T0"),
    ("corrected_goal_is_T2", "goals", None, _corrected, "T2"),
    ("clean_share_clear_is_T1", "attack_share_", "clear", _same, "T1"),
    ("clean_share_suggests_is_T1", "attack_share_", "suggests", _same, "T1"),
    ("plain_count_hedge_none_is_T1", "xg_sum", None, _same, "T1"),
    ("small_impact_is_T1", "attack_share_", "clear", _impact(0.09), "T1"),
    ("corrected_is_T2", "attack_share_", "clear", _corrected, "T2"),
    ("impact_at_threshold_is_T2", "attack_share_", "clear", _impact(0.10), "T2"),
    ("negative_impact_is_T2", "turnovers_own_third", None, _impact(-0.15), "T2"),
    ("names_player_negatively_is_T2", "xg_sum", None, _negative_player, "T2"),
    ("hedge_early_is_T2", "attack_share_", "clear", _early, "T2"),
]


@pytest.mark.parametrize("case,metric,hedge,tweak,tier", TABLE, ids=[t[0] for t in TABLE])
def test_tier_routing_table(
    case: str,
    metric: str,
    hedge: str | None,
    tweak: Callable[[Fact], Fact],
    tier: str,
    match: Match,
) -> None:
    fact = tweak(_good(metric, match, hedge))
    out = route(fact, S.router)
    assert out.risk is not None
    assert out.risk.tier == tier, out.risk
    assert out.approval.state == {"T0": "auto_approved", "T1": "veto_window", "T2": "queued"}[tier]


@pytest.mark.parametrize("category", ["wrong_number", "future_event"])
def test_rejected_never_reaches_router(category: str, match: Match) -> None:
    out = verify(seeded_of(category)[0].fact, match, S)
    assert out.verification.status == "rejected"
    with pytest.raises(ValueError):
        route(out, S.router)


def test_unverifiable_never_reaches_router(match: Match) -> None:
    fact = seeded_of("good")[0].fact
    fact.claim.metric = "duels_lost"
    with pytest.raises(ValueError):
        route(verify(fact, match, S), S.router)


def _reasons(f: Fact) -> list[str]:
    assert f.risk is not None
    return f.risk.reasons or []


def test_autopilot_only_for_clean_clear_t2(match: Match) -> None:
    clean = route(_impact(0.2)(_good("attack_share_", match, "clear")), S.router)
    assert clean.risk is not None and clean.risk.tier == "T2"
    assert AUTOPILOT_OK in _reasons(clean)
    corrected = route(_corrected(_good("attack_share_", match, "clear")), S.router)
    assert AUTOPILOT_OK not in _reasons(corrected)
    assert resolve(corrected, 1, autopilot=True) == ("queued", None)
    assert resolve(clean, 1, autopilot=True) == ("approved", "autopilot")


def test_t1_airs_after_veto_window_unless_vetoed(match: Match) -> None:
    f = route(_good("attack_share_", match, "clear"), S.router)
    assert f.approval.ttl_s == 3
    assert resolve(f, 2.9) == ("veto_window", None)
    assert resolve(f, 3.0) == ("auto_approved", "auto")
    assert resolve(f, 5, "veto", 2.0) == ("vetoed", "producer")
    assert resolve(f, 5, "veto", 3.5) == ("auto_approved", "auto")  # too late to veto


def test_ttl_expiry_drops_t2(match: Match) -> None:
    f = route(_corrected(_good("attack_share_", match, "clear")), S.router)
    assert f.approval.ttl_s == 12
    assert resolve(f, 11.9) == ("queued", None)
    assert resolve(f, 12.0) == ("expired", None)
    assert resolve(f, 20, "approve", 13.0) == ("expired", None)  # late approval never airs
    assert resolve(f, 20, "approve", 4.0) == ("approved", "producer")
    assert resolve(f, 20, "reject", 4.0) == ("rejected", "producer")
