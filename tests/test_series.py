"""Chart series in the bundle must equal what core/ computes directly."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.series import TEAM_METRICS, build_series, lanes_at
from core.config import ROOT, load
from core.moments import compute_metric
from core.wilson import wilson
from core.winprob import win_probability

Match = dict[str, Any]
TEAMS = ("NOR", "HAR")


@pytest.fixture(scope="module")
def mu() -> float:
    stats = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))
    return float(stats["mu_xg_per_team_minute"])


@pytest.fixture(scope="module")
def series(match: Match, mu: float) -> dict[str, Any]:
    return build_series(match["events"], TEAMS, mu)


def test_grid_covers_the_match(series: dict[str, Any], match: Match) -> None:
    last = max(e["t"] for e in match["events"])
    assert series["t"][0] == 0.0 and series["t"][-1] >= last
    for col in [series["win_prob"], *series["teams"]["NOR"].values(), series["lanes"]["HAR"]]:
        assert len(col) == len(series["t"])


def test_win_prob_rows_sum_to_one_and_match_core(series: dict[str, Any], match: Match, mu: float) -> None:
    for row in series["win_prob"]:
        assert sum(row) == pytest.approx(1.0, abs=0.003)
    i = 70
    t = series["t"][i]
    goals = [sum(e["type"] == "goal" and e["team"] == tm and e["t"] <= t for e in match["events"]) for tm in TEAMS]
    xg = [sum(e["xg"] for e in match["events"] if e["type"] == "shot" and e["team"] == tm and e["t"] <= t)
          for tm in TEAMS]
    expected = win_probability(t / 60, goals[0], goals[1], xg[0], xg[1], mu)
    assert series["win_prob"][i] == pytest.approx(expected, abs=0.001)


def test_goal_moves_win_probability(series: dict[str, Any], match: Match) -> None:
    goal = next(e for e in match["events"] if e["type"] == "goal")
    before, after = int(goal["t"] // 60), int(goal["t"] // 60) + 1
    side = TEAMS.index(goal["team"])
    assert series["win_prob"][after][side] > series["win_prob"][before][side] + 0.2


@pytest.mark.parametrize("metric", TEAM_METRICS)
def test_team_stats_equal_compute_metric(series: dict[str, Any], match: Match, metric: str) -> None:
    for i in (30, 60, len(series["t"]) - 1):
        t = series["t"][i]
        m = compute_metric(metric, {"type": "team", "id": "NOR"}, {"start_t": 0.0, "end_t": t}, match["events"])
        assert m is not None
        assert series["teams"]["NOR"][metric][i] == pytest.approx(m.value, abs=0.001)


def test_lanes_carry_wilson_and_threshold_from_config(series: dict[str, Any], match: Match) -> None:
    c, v = load()["moments"], load()["verifier"]
    for i, row in enumerate(series["lanes"]["NOR"]):
        assert sum(row[lane]["k"] for lane in ("left", "centre", "right")) == row["n"]
        lo, hi = wilson(row["left"]["k"], row["n"], v["z"])
        assert (row["left"]["lo"], row["left"]["hi"]) == (round(lo, 3), round(hi, 3))
        if row["overload"]:
            assert row["hedge"] == "clear" and row[row["overload"]]["share"] >= c["overload_min_share"]
            assert row["n"] >= v["n_min"]
    assert lanes_at(match["events"], "NOR", series["t"][75]) == series["lanes"]["NOR"][75]


def test_bundle_events_keep_coordinates_and_xg() -> None:
    hero = json.loads((ROOT / "docs" / "data" / "hero.json").read_text(encoding="utf-8"))
    shots = [e for e in hero["events"] if e["type"] == "shot"]
    assert shots and all({"x", "y", "xg", "shot_speed"} <= set(e) for e in shots)
    assert all({"x", "y", "end_x", "end_y"} <= set(e) for e in hero["events"] if e["type"] == "pass")
    assert "series" in hero
