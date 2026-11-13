"""Beta-Binomial hedge for share claims (P9).

A lane's attack share gets a Beta prior centred on the league's share for that lane, with weight
`prior_strength` pseudo-attacks. After k of n attacks down the lane the posterior is
Beta(a + k, b + n - k). The claim may be stated plainly ("clear") when the posterior probability
that the share is above the league share plus `min_effect` is at least `clear_prob`; with fewer
than n_min attacks it is "early"; otherwise "suggests". Wilson (core/wilson.py) stays available
for comparison. Method and the false-alarm study: docs/methods/hedging.md.

The Beta CDF is the regularised incomplete beta function, computed with the continued fraction
of Numerical Recipes (section 6.4) in plain Python, so no scipy at run time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.config import Hedge as HedgeConfig

_EPS, _TINY, _MAX_ITER = 1e-15, 1e-300, 500


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, _MAX_ITER + 1):
        m2 = 2 * m
        for num in (
            m * (b - m) * x / ((qam + m2) * (a + m2)),
            -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)),
        ):
            d = 1 + num * d
            d = 1 / (d if abs(d) > _TINY else _TINY)
            c = 1 + num / c
            c = c if abs(c) > _TINY else _TINY
            h *= d * c
        if abs(d * c - 1) < _EPS:
            return h
    raise ValueError(f"incomplete beta did not converge for a={a}, b={b}, x={x}")


def beta_cdf(x: float, a: float, b: float) -> float:
    """P(X <= x) for X ~ Beta(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """Inverse of beta_cdf, by bisection (the CDF is increasing)."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if beta_cdf(mid, a, b) < q else (lo, mid)
    return (lo + hi) / 2


@dataclass(frozen=True)
class Posterior:
    a: float
    b: float

    def prob_above(self, t: float) -> float:
        return 1 - beta_cdf(t, self.a, self.b)

    def interval(self, level: float) -> tuple[float, float]:
        """Equal-tailed credible interval."""
        tail = (1 - level) / 2
        return beta_ppf(tail, self.a, self.b), beta_ppf(1 - tail, self.a, self.b)


def posterior(k: int, n: int, prior_share: float, strength: float) -> Posterior:
    """Beta posterior for a share after k of n, from a prior with mean prior_share."""
    return Posterior(prior_share * strength + k, (1 - prior_share) * strength + n - k)


def beta_hedge(k: int, n: int, prior_share: float, h: HedgeConfig, n_min: int) -> str:
    """early / suggests / clear for a claim that a share is above the league share."""
    if n < n_min:
        return "early"
    p = posterior(k, n, prior_share, h.prior_strength).prob_above(prior_share + h.min_effect)
    return "clear" if p >= h.clear_prob else "suggests"
