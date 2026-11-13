"""Fitting and scoring a logistic xG model, in plain Python (a handful of features needs no numpy).

- fit_logistic: maximum likelihood by Newton-Raphson, with a tiny L2 penalty that only keeps
  the steps well posed. Standard errors come from the inverse Hessian at the optimum.
- log_loss, brier: proper scoring rules for probabilities.
- reliability: equal-count bins of the predictions, mean predicted against observed rate.
- split_by_match: whole matches to train or test, so shots of one match never sit on both sides.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

Matrix = list[list[float]]
EPS = 1e-12


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def inverse(a: Matrix) -> Matrix:
    """Inverse of a small square matrix by Gauss-Jordan elimination with partial pivoting."""
    n = len(a)
    m = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < EPS:
            raise ValueError("matrix is singular")
        m[col], m[pivot] = m[pivot], m[col]
        div = m[col][col]
        m[col] = [v / div for v in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [v - f * w for v, w in zip(m[r], m[col], strict=True)]
    return [row[n:] for row in m]


@dataclass(frozen=True)
class Fit:
    coef: list[float]
    se: list[float]
    iterations: int


def _gradient_hessian(
    rows: Sequence[Sequence[float]], ys: Sequence[int], coef: list[float], ridge: float
) -> tuple[list[float], Matrix]:
    k = len(coef)
    grad = [-ridge * c for c in coef]
    hess = [[ridge * float(i == j) for j in range(k)] for i in range(k)]
    for row, y in zip(rows, ys, strict=True):
        p = sigmoid(_dot(coef, row))
        w = p * (1 - p)
        for i in range(k):
            grad[i] += (y - p) * row[i]
            for j in range(i + 1):
                hess[i][j] += w * row[i] * row[j]
    for i in range(k):
        for j in range(i):
            hess[j][i] = hess[i][j]
    return grad, hess


def fit_logistic(
    rows: Sequence[Sequence[float]], ys: Sequence[int], max_iter: int, ridge: float
) -> Fit:
    """Coefficients maximising the (lightly penalised) log likelihood."""
    coef, iterations = [0.0] * len(rows[0]), 0
    while iterations < max_iter:
        iterations += 1
        grad, hess = _gradient_hessian(rows, ys, coef, ridge)
        step = [_dot(r, grad) for r in inverse(hess)]
        coef = [c + s for c, s in zip(coef, step, strict=True)]
        if max(abs(s) for s in step) < 1e-10:
            break
    _, hess = _gradient_hessian(rows, ys, coef, ridge)
    se = [math.sqrt(v[i]) for i, v in enumerate(inverse(hess))]
    return Fit(coef, se, iterations)


def log_loss(p: Sequence[float], y: Sequence[int]) -> float:
    total = 0.0
    for pi, yi in zip(p, y, strict=True):
        q = min(max(pi, EPS), 1 - EPS)
        total -= math.log(q) if yi else math.log(1 - q)
    return total / len(p)


def brier(p: Sequence[float], y: Sequence[int]) -> float:
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y, strict=True)) / len(p)


@dataclass(frozen=True)
class Bin:
    n: int
    mean_pred: float
    observed: float


def reliability(p: Sequence[float], y: Sequence[int], bins: int) -> list[Bin]:
    """Predictions sorted and cut into `bins` groups of (nearly) equal size."""
    pairs = sorted(zip(p, y, strict=True))
    out = []
    for b in range(bins):
        chunk = pairs[b * len(pairs) // bins : (b + 1) * len(pairs) // bins]
        if chunk:
            n = len(chunk)
            out.append(Bin(n, sum(c[0] for c in chunk) / n, sum(c[1] for c in chunk) / n))
    return out


def split_by_match(
    match_ids: Sequence[int], test_fraction: float, seed: int
) -> tuple[set[int], set[int]]:
    """(train, test) match ids. The same ids and seed always give the same split."""
    ids = sorted(set(match_ids))
    test = set(random.Random(seed).sample(ids, round(len(ids) * test_fraction)))
    return set(ids) - test, test
