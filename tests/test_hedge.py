"""Beta-Binomial hedge (core/hedge.py): the Beta CDF against exact values, posterior and rule."""

from __future__ import annotations

import math

import pytest

from core.config import Hedge, load
from core.hedge import beta_cdf, beta_hedge, beta_ppf, posterior

S = load()


def exact_cdf(x: float, a: int, b: int) -> float:
    """For whole a and b: I_x(a, b) = P(Binomial(a + b - 1, x) >= a)."""
    n = a + b - 1
    return sum(math.comb(n, j) * x**j * (1 - x) ** (n - j) for j in range(a, n + 1))


@pytest.mark.parametrize("a,b", [(1, 1), (2, 1), (1, 2), (5, 3), (3, 12), (40, 25), (120, 90)])
@pytest.mark.parametrize("x", [0.01, 0.2, 0.333, 0.5, 0.71, 0.99])
def test_cdf_matches_the_binomial_sum(a: int, b: int, x: float) -> None:
    assert beta_cdf(x, a, b) == pytest.approx(exact_cdf(x, a, b), abs=1e-12)


def test_cdf_edges_and_symmetry() -> None:
    assert (beta_cdf(0, 2.5, 3.5), beta_cdf(1, 2.5, 3.5)) == (0.0, 1.0)
    assert beta_cdf(0.5, 7.3, 7.3) == pytest.approx(0.5)
    assert beta_cdf(0.3, 2.2, 4.7) == pytest.approx(1 - beta_cdf(0.7, 4.7, 2.2))


def test_ppf_inverts_the_cdf() -> None:
    for q in (0.025, 0.5, 0.975):
        assert beta_cdf(beta_ppf(q, 4.6, 9.4), 4.6, 9.4) == pytest.approx(q, abs=1e-9)


def test_posterior_adds_counts_to_the_prior() -> None:
    p = posterior(k=6, n=10, prior_share=0.3, strength=10)
    assert (p.a, p.b) == pytest.approx((9.0, 11.0))
    lo, hi = p.interval(0.95)
    assert lo < 9 / 20 < hi


def test_hedge_rule() -> None:
    h = Hedge(prior_strength=10, clear_prob=0.95, min_effect=0.0, clustered=False)
    assert beta_hedge(4, 5, 0.36, h, 6, 2.0) == "early"
    assert beta_hedge(18, 20, 0.36, h, 6, 2.0) == "clear"
    assert beta_hedge(4, 8, 0.36, h, 6, 2.0) == "suggests"
    strict = h.model_copy(update={"min_effect": 0.3})
    assert beta_hedge(14, 20, 0.36, h, 6, 2.0) == "clear"
    assert beta_hedge(14, 20, 0.36, strict, 6, 2.0) == "suggests", "a minimum effect is harder"


def test_clustering_counts_fewer_independent_attacks() -> None:
    plain = Hedge(prior_strength=10, clear_prob=0.95, min_effect=0.0, clustered=False)
    clustered = plain.model_copy(update={"clustered": True})
    assert beta_hedge(9, 14, 0.31, plain, 6, 2.0) == "clear"
    assert beta_hedge(9, 14, 0.31, clustered, 6, 2.0) == "suggests", "worth 4.5 of 7"
    assert beta_hedge(9, 14, 0.31, clustered, 6, 1.0) == "clear", "deff 1 changes nothing"
    assert beta_hedge(5, 5, 0.31, clustered, 6, 2.0) == "early", "n_min applies to raw n"


def test_design_effect_is_the_variance_inflation_of_lane_counts() -> None:
    from core.events import Event
    from core.shares import attack_design_effect

    def att(i: int, t: float, y: float) -> Event:
        return Event(
            id=i, t=t, period=1, type="pass", team="A", player_id="A_1",
            x=60, y=30, end_x=95, end_y=y,
        )  # fmt: skip

    shares = {"left": 0.5, "centre": 0.0, "right": 0.5}
    # Two windows of four attacks, all left then all right: each lane is off by 2 in each window,
    # (4 + 4) * 2 / (4 * 0.25 * 2 * 2) = 4, as for blocks of four attacks that always agree.
    clustered = [att(i, i, 5) for i in range(4)] + [att(9 + i, 100 + i, 60) for i in range(4)]
    assert attack_design_effect([clustered], S.pitch, shares, 100) == pytest.approx(4)
    # Two left and two right in each window: less spread than independence, floored at 1.
    mixed = [att(i, i, 5 if i % 2 else 60) for i in range(4)]
    assert attack_design_effect([mixed], S.pitch, shares, 100) == 1.0


def test_hedge_config_is_loaded() -> None:
    assert 0 < S.hedge.clear_prob < 1 and S.hedge.prior_strength > 0


def test_study_judges_with_the_product_wilson_rule_and_every_candidate() -> None:
    from app.drafter import share_hedge
    from core.sources import League
    from scripts.eval_hedging import rules, strong

    league = League(0.014, {"left": 0.31, "centre": 0.36, "right": 0.33}, 3.5)
    judge = rules(S, league)
    assert set(judge) == {"wilson"} | {c.name for c in S.hedge_eval.candidates}
    for k, n in [(4, 6), (5, 6), (9, 14), (12, 20), (15, 20)]:
        product = share_hedge(k, n, 100 * S.verifier.lane_baseline, S.verifier) == "clear"
        assert judge["wilson"](k, n, "left") is product
    s = strong(S)
    assert s.sim.overload_lane_weights == S.hedge_eval.strong_lane_weights
    assert S.sim.overload_lane_weights != s.sim.overload_lane_weights, "settings are not mutated"
