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
    h = Hedge(prior_strength=10, clear_prob=0.95, min_effect=0.0)
    assert beta_hedge(4, 5, 0.36, h, n_min=6) == "early"
    assert beta_hedge(18, 20, 0.36, h, n_min=6) == "clear"
    assert beta_hedge(4, 8, 0.36, h, n_min=6) == "suggests"
    strict = Hedge(prior_strength=10, clear_prob=0.95, min_effect=0.3)
    assert beta_hedge(14, 20, 0.36, h, 6) == "clear"
    assert beta_hedge(14, 20, 0.36, strict, 6) == "suggests", "a minimum effect makes clear harder"


def test_hedge_config_is_loaded() -> None:
    assert 0 < S.hedge.clear_prob < 1 and S.hedge.prior_strength > 0
