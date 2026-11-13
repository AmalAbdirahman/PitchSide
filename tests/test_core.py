from __future__ import annotations

import json
from dataclasses import replace

import pytest

from core.config import ROOT, load
from core.events import Event
from core.shares import lane_shares
from core.wilson import hedge_for, wilson
from core.winprob import win_probability
from core.xg import FEATURES, ShotFeatures, xg, xg_of

MU = 0.0155

S = load()


def test_win_probability_late_goal_moves_more_than_early_goal() -> None:
    def delta(minute: float) -> float:
        before = win_probability(minute, 0, 0, 0.015 * minute, 0.015 * minute, MU, S.winprob)[0]
        after = win_probability(minute, 1, 0, 0.015 * minute, 0.015 * minute, MU, S.winprob)[0]
        return after - before

    assert delta(85) > delta(10) > 0


def test_win_probability_sums_to_one() -> None:
    assert sum(win_probability(30, 1, 1, 0.6, 0.4, MU, S.winprob)) == pytest.approx(1.0)


def test_wilson_known_value() -> None:
    lo, hi = wilson(7, 10)
    assert lo == pytest.approx(0.3968, abs=1e-3)
    assert hi == pytest.approx(0.8922, abs=1e-3)


def test_hedge_rules() -> None:
    assert hedge_for(4, 0.1, 0.9, 1 / 3, 6) == "early"
    assert hedge_for(20, 0.5, 0.9, 1 / 3, 6) == "clear"
    assert hedge_for(20, 0.2, 0.6, 1 / 3, 6) == "suggests"


def test_xg_falls_with_distance_and_is_plausible() -> None:
    assert xg(99, 34, S.pitch, S.xg) > xg(94, 34, S.pitch, S.xg) > xg(85, 34, S.pitch, S.xg)
    assert 0.05 < xg(94, 34, S.pitch, S.xg) < 0.5
    assert xg(70, 10, S.pitch, S.xg) < 0.03


def test_xg_features_move_it_the_fitted_way() -> None:
    foot = ShotFeatures(97.0, 34.0)
    base = xg_of(foot, S.pitch, S.xg)
    assert xg_of(replace(foot, header=True), S.pitch, S.xg) < base
    assert xg_of(replace(foot, under_pressure=True), S.pitch, S.xg) < base
    assert xg_of(replace(foot, penalty=True), S.pitch, S.xg) == S.xg.penalty


def test_config_coefficients_are_the_recorded_fit() -> None:
    # Provenance: config.yaml must hold exactly what scripts/fit_xg.py wrote to data/xg_fit.json.
    record = json.loads((ROOT / "data" / "xg_fit.json").read_text(encoding="utf-8"))
    for name in FEATURES:
        assert getattr(S.xg, name) == record["coefficients"][name]["value"], name
    assert S.xg.penalty == record["penalty"]["rate"]


def test_lane_shares_sum_to_one() -> None:
    events = [
        Event(id=i, t=60.0 * i, period=1, type="shot", team="A", player_id="A_09", x=95, y=y)
        for i, y in enumerate((5, 30, 60, 10))
    ]
    shares, n = lane_shares(events, "A", S.pitch)
    assert n == 4
    assert sum(shares.values()) == pytest.approx(1.0)
    assert shares["left"] == 0.5
