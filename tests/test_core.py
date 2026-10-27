from __future__ import annotations

import pytest

from core.config import parse
from core.shares import lane_shares
from core.wilson import hedge_for, wilson
from core.winprob import win_probability
from core.xg import xg

MU = 0.0155


def test_win_probability_late_goal_moves_more_than_early_goal() -> None:
    def delta(minute: float) -> float:
        before = win_probability(minute, 0, 0, 0.015 * minute, 0.015 * minute, MU)[0]
        after = win_probability(minute, 1, 0, 0.015 * minute, 0.015 * minute, MU)[0]
        return after - before

    assert delta(85) > delta(10) > 0


def test_win_probability_sums_to_one() -> None:
    assert sum(win_probability(30, 1, 1, 0.6, 0.4, MU)) == pytest.approx(1.0)


def test_wilson_known_value() -> None:
    lo, hi = wilson(7, 10)
    assert lo == pytest.approx(0.3968, abs=1e-3)
    assert hi == pytest.approx(0.8922, abs=1e-3)


def test_hedge_rules() -> None:
    assert hedge_for(4, 0.1, 0.9, 1 / 3, 6) == "early"
    assert hedge_for(20, 0.5, 0.9, 1 / 3, 6) == "clear"
    assert hedge_for(20, 0.2, 0.6, 1 / 3, 6) == "suggests"


def test_xg_falls_with_distance_and_is_plausible() -> None:
    assert xg(99, 34) > xg(94, 34) > xg(85, 34)
    assert 0.3 < xg(99, 34) < 0.5
    assert xg(94, 34) == pytest.approx(0.20, abs=0.03)


def test_lane_shares_sum_to_one() -> None:
    events = [{"id": i, "type": "shot", "team": "A", "x": 95, "y": y} for i, y in enumerate((5, 30, 60, 10))]
    shares, n = lane_shares(events, "A")
    assert n == 4
    assert sum(shares.values()) == pytest.approx(1.0)
    assert shares["left"] == 0.5


def test_config_parser_subset() -> None:
    cfg = parse("a: 1\nb:\n  c: [1, 2.5]\n  d:\n    - 'x y'\n    - z\ne: true  # note\n")
    assert cfg == {"a": 1, "b": {"c": [1, 2.5], "d": ["x y", "z"]}, "e": True}
