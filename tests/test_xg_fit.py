"""Logistic fit, scores, reliability table and the match split used to fit xG."""

from __future__ import annotations

import math
import random

import pytest

from core.config import load
from core.xg import FEATURES, ShotFeatures, design_row
from core.xg_fit import (
    brier,
    fit_logistic,
    inverse,
    log_loss,
    reliability,
    sigmoid,
    split_by_match,
)

S = load()


def test_fit_recovers_known_coefficients() -> None:
    rng, true = random.Random(1), [-1.0, 0.8, -1.5]
    rows, ys = [], []
    for _ in range(6000):
        row = [1.0, rng.uniform(-2, 2), float(rng.random() < 0.3)]
        rows.append(row)
        ys.append(int(rng.random() < sigmoid(sum(b * v for b, v in zip(true, row, strict=True)))))
    fit = fit_logistic(rows, ys, max_iter=50, ridge=1e-6)
    for est, se, b in zip(fit.coef, fit.se, true, strict=True):
        assert abs(est - b) < 3 * se, (fit.coef, fit.se)
    assert fit.iterations < 15


def test_fit_gives_the_mean_rate_with_only_an_intercept() -> None:
    ys = [1] * 30 + [0] * 70
    fit = fit_logistic([[1.0]] * 100, ys, max_iter=50, ridge=0.0)
    assert sigmoid(fit.coef[0]) == pytest.approx(0.3, abs=1e-9)


def test_inverse() -> None:
    a = [[4.0, 1.0], [2.0, 3.0]]
    inv = inverse(a)
    assert inv == [
        [pytest.approx(0.3), pytest.approx(-0.1)],
        [pytest.approx(-0.2), pytest.approx(0.4)],
    ]


def test_scores_on_hand_values() -> None:
    p, y = [0.8, 0.1], [1, 0]
    assert log_loss(p, y) == pytest.approx(-(math.log(0.8) + math.log(0.9)) / 2)
    assert brier(p, y) == pytest.approx((0.04 + 0.01) / 2)


def test_reliability_bins_have_equal_counts_and_cover_everything() -> None:
    p = [i / 100 for i in range(100)]
    y = [int(v > 0.5) for v in p]
    bins = reliability(p, y, 10)
    assert [b.n for b in bins] == [10] * 10
    assert bins[0].mean_pred == pytest.approx(0.045)
    assert bins[0].observed == 0 and bins[-1].observed == 1


def test_split_by_match_is_disjoint_and_repeatable() -> None:
    ids = list(range(100, 200))
    train, test = split_by_match(ids, 0.25, 7)
    assert train.isdisjoint(test) and train | test == set(ids)
    assert len(test) == 25
    assert split_by_match(list(reversed(ids)), 0.25, 7) == (train, test)


def test_design_row_matches_feature_names() -> None:
    row = design_row(ShotFeatures(x=94.0, y=34.0, header=True, under_pressure=True), S.pitch, 7.32)
    named = dict(zip(FEATURES, row, strict=True))
    assert named["intercept"] == 1.0 and named["header"] == 1.0 and named["free_kick"] == 0.0
    assert named["dist"] == pytest.approx(11.0)
    assert named["under_pressure"] == 1.0
