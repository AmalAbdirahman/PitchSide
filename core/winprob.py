"""Win probability, method poisson_v1 (SPEC section 9). A model estimate, not a prediction.

Assumptions: each side scores the remaining goals as an independent Poisson
process. Its rate per minute blends the side's own xG so far with the
league mean `mu`, weighted by a prior of `k` pseudo-minutes:
    rate = (xg + k * mu) / (minutes_played + k)
Outcomes are summed up to `goal_cap` further goals per side.

poisson_v2 (P10, validation only; docs/methods/winprob.md) has the same form with three fitted
changes: k, the expected match length (stoppage time included) and a Dixon-Coles adjustment rho
for low remaining scores. The product still uses poisson_v1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.config import Winprob

Probs = tuple[float, float, float]


@dataclass(frozen=True)
class PoissonParams:
    k: float  # prior weight on the league rate, in minutes
    length: float  # minutes the match is expected to run, stoppage included
    rho: float  # Dixon-Coles low-score adjustment; 0 is none
    goal_cap: int

    @classmethod
    def v1(cls, c: Winprob) -> PoissonParams:
        return cls(c.k, c.match_minutes, 0.0, c.goal_cap)


def _pmf(lam: float, cap: int) -> list[float]:
    return [math.exp(-lam) * lam**i / math.factorial(i) for i in range(cap + 1)]


def rate(xg: float, minutes: float, mu: float, k: float) -> float:
    """Shrunk goal rate per minute for one side."""
    return (xg + k * mu) / (minutes + k)


def dixon_coles(i: int, j: int, la: float, lb: float, rho: float) -> float:
    """Dixon-Coles factor for i and j further goals: it moves probability between 0-0, 1-1 and
    the one-goal results, and leaves every other score alone."""
    factor = {(0, 0): 1 - la * lb * rho, (0, 1): 1 + la * rho, (1, 0): 1 + lb * rho}
    return max(0.0, factor.get((i, j), 1 - rho if (i, j) == (1, 1) else 1.0))


def outcome_probs(
    minutes: float,
    goals_a: int,
    goals_b: int,
    xg_a: float,
    xg_b: float,
    mu: float,
    p: PoissonParams,
) -> Probs:
    """Return (P(a wins), P(draw), P(b wins)) at the given match state."""
    remaining = max(0.0, p.length - minutes)
    la, lb = (rate(x, minutes, mu, p.k) * remaining for x in (xg_a, xg_b))
    pa, pb = _pmf(la, p.goal_cap), _pmf(lb, p.goal_cap)
    win = draw = loss = 0.0
    for i, p_i in enumerate(pa):
        for j, p_j in enumerate(pb):
            q = p_i * p_j if p.rho == 0 else p_i * p_j * dixon_coles(i, j, la, lb, p.rho)
            diff = (goals_a + i) - (goals_b + j)
            if diff > 0:
                win += q
            elif diff == 0:
                draw += q
            else:
                loss += q
    total = win + draw + loss
    return win / total, draw / total, loss / total


def win_probability(
    minutes: float,
    goals_a: int,
    goals_b: int,
    xg_a: float,
    xg_b: float,
    mu: float,
    c: Winprob,
) -> Probs:
    """poisson_v1: (P(a wins), P(draw), P(b wins)) at the given match state."""
    return outcome_probs(minutes, goals_a, goals_b, xg_a, xg_b, mu, PoissonParams.v1(c))
