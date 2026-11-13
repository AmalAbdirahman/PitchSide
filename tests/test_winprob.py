"""Win probability (poisson_v1 and v2) and its validation helpers (P10)."""

from __future__ import annotations

import pytest

from core.config import load
from core.events import Clock, Event
from core.winprob import PoissonParams, dixon_coles, outcome_probs, win_probability
from core.winprob_eval import (
    State,
    bootstrap_diff,
    brier,
    calibration,
    diff_bucket,
    fit_v2,
    outcome_rates,
    phase_bucket,
    regulation_end,
    snapshot_t,
    states,
)

S = load()
V1 = PoissonParams.v1(S.winprob)


def _ev(i: int, t: float, kind: str, team: str, xg: float | None = None) -> Event:
    return Event(
        id=i, t=t, period=1, type=kind, team=team, player_id=f"{team}_9", x=95, y=34, xg=xg
    )


def _state(goals: tuple[int, int], outcome: int, match_id: str = "m", minute: int = 60) -> State:
    return State(match_id, minute, 60.0 * minute, goals, (0.5, 0.5), outcome)


def test_v1_is_outcome_probs_with_v1_params() -> None:
    args = (55.0, 1, 0, 0.9, 0.4, 0.014)
    assert win_probability(*args, S.winprob) == outcome_probs(*args, V1)
    assert sum(outcome_probs(*args, V1)) == pytest.approx(1.0)


def test_dixon_coles_only_touches_low_scores() -> None:
    assert dixon_coles(2, 0, 0.8, 0.7, 0.2) == 1.0
    assert dixon_coles(1, 1, 0.8, 0.7, 0.2) == pytest.approx(0.8)
    assert dixon_coles(0, 0, 0.8, 0.7, 0.2) == pytest.approx(1 - 0.8 * 0.7 * 0.2)
    level = (60.0, 0, 0, 0.6, 0.6, 0.014)
    plain = outcome_probs(*level, V1)
    fewer_draws = outcome_probs(*level, PoissonParams(V1.k, V1.length, 0.2, V1.goal_cap))
    assert fewer_draws[1] < plain[1]


def test_a_longer_match_leaves_more_time_to_change_the_result() -> None:
    lead = (80.0, 1, 0, 1.0, 1.0, 0.014)
    longer = PoissonParams(V1.k, 98.0, 0.0, V1.goal_cap)
    assert outcome_probs(*lead, longer)[0] < outcome_probs(*lead, V1)[0]


def test_snapshots_use_the_official_clock_and_the_result_after_90() -> None:
    clock = Clock(period_starts=[0, 2900, 5800, 6800], end_t=7800)
    assert snapshot_t(clock, 30) == 1800 and snapshot_t(clock, 60) == 2900 + 900
    assert regulation_end(clock) == 5800
    events = [
        _ev(1, 100, "shot", "A", 0.3),
        _ev(2, 100, "goal", "A"),
        _ev(3, 3000, "goal", "B"),
        _ev(4, 6000, "goal", "B"),  # extra time: not part of the 90-minute result
    ]
    rows = states(events, ("A", "B"), clock, "x", [0, 30, 60])
    assert [r.goals for r in rows] == [(0, 0), (1, 0), (1, 1)]
    assert rows[1].xg == (0.3, 0.0)
    assert {r.outcome for r in rows} == {1}


def test_scores_and_buckets() -> None:
    assert brier((1.0, 0.0, 0.0), 0) == 0.0
    assert brier((1 / 3, 1 / 3, 1 / 3), 1) == pytest.approx(2 / 3)
    assert [diff_bucket(g) for g in [(0, 0), (2, 1), (0, 3), (4, 1)]] == [
        "level",
        "+1",
        "-2 or less",
        "+2 or more",
    ]
    assert (
        phase_bucket(0, [0, 30, 60, 90]) == "0-30" and phase_bucket(85, [0, 30, 60, 90]) == "60-90"
    )


def test_outcome_rates_count_matches_not_snapshots() -> None:
    rows = [_state((0, 0), 0, "a", m) for m in (0, 30, 60)] + [_state((0, 0), 1, "b")]
    assert outcome_rates(rows) == (0.5, 0.5, 0.0)


def test_calibration_buckets_compare_mean_prediction_with_outcomes() -> None:
    rows = [_state((1, 0), 0, "a"), _state((1, 0), 1, "b"), _state((0, 0), 1, "c")]
    table = calibration(rows, lambda r: (0.8, 0.2, 0.0), [0, 45, 90])
    lead = next(b for b in table if b.state == "+1")
    assert (lead.phase, lead.n, lead.matches) == ("45-90", 2, 2)
    assert lead.predicted == (0.8, 0.2, 0.0) and lead.observed == (0.5, 0.5, 0.0)


def test_bootstrap_is_seeded_and_brackets_the_difference() -> None:
    rows = [_state((0, 0), i % 3, str(i)) for i in range(30)]
    sure, even = (lambda r: (1.0, 0.0, 0.0)), (lambda r: (1 / 3, 1 / 3, 1 / 3))
    lo, hi = bootstrap_diff(rows, sure, even, 200, 1)
    assert (lo, hi) == bootstrap_diff(rows, sure, even, 200, 1)
    mean = sum(brier(sure(r), r.outcome) - brier(even(r), r.outcome) for r in rows) / len(rows)
    assert lo <= mean <= hi


def test_fit_v2_picks_the_grid_point_with_the_lowest_training_brier() -> None:
    # Level at 60' and every match drawn: the fit should favour fewer goals, i.e. more draws.
    rows = [_state((0, 0), 1, str(i)) for i in range(20)]
    p = fit_v2(rows, 0.014, 90.0, [20.0, 1000.0], [-0.1, 0.0, 0.1], V1.goal_cap)
    assert p.length == 90.0 and p.rho == -0.1
