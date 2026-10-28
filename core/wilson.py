"""Wilson score interval and the hedge rule from SPEC section 8."""
from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval for k successes out of n. Returns (low, high) in 0..1."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def hedge_for(n: int, low: float, high: float, baseline: float | None, n_min: int) -> str:
    """Allowed language strength for a share claim.

    n below n_min -> early. Interval excludes the baseline -> clear.
    Interval overlaps it -> suggests. No baseline -> clear.
    """
    if n < n_min:
        return "early"
    if baseline is None or not (low <= baseline <= high):
        return "clear"
    return "suggests"
