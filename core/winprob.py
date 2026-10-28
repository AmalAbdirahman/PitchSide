"""Win probability, method poisson_v1 (SPEC section 9). A model estimate, not a prediction.

Assumptions: each side scores the remaining goals as an independent Poisson
process. Its rate per minute blends the side's own xG so far with the
league mean `mu`, weighted by a prior of `k` pseudo-minutes:
    rate = (xg + k * mu) / (minutes_played + k)
Outcomes are summed up to `goal_cap` further goals per side.
"""
from __future__ import annotations

import math

from core.config import load


def _pmf(lam: float, cap: int) -> list[float]:
    return [math.exp(-lam) * lam**i / math.factorial(i) for i in range(cap + 1)]


def rate(xg: float, minutes: float, mu: float, k: float) -> float:
    """Shrunk goal rate per minute for one side."""
    return (xg + k * mu) / (minutes + k)


def win_probability(
    minutes: float, goals_a: int, goals_b: int, xg_a: float, xg_b: float, mu: float
) -> tuple[float, float, float]:
    """Return (P(a wins), P(draw), P(b wins)) at the given match state."""
    c = load()["winprob"]
    remaining = max(0.0, c["match_minutes"] - minutes)
    pa = _pmf(rate(xg_a, minutes, mu, c["k"]) * remaining, c["goal_cap"])
    pb = _pmf(rate(xg_b, minutes, mu, c["k"]) * remaining, c["goal_cap"])
    win = draw = loss = 0.0
    for i, p_i in enumerate(pa):
        for j, p_j in enumerate(pb):
            diff = (goals_a + i) - (goals_b + j)
            if diff > 0:
                win += p_i * p_j
            elif diff == 0:
                draw += p_i * p_j
            else:
                loss += p_i * p_j
    total = win + draw + loss
    return win / total, draw / total, loss / total
