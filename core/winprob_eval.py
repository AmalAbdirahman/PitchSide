"""Validation of the win-probability model (P10): game states, scores and calibration tables.

A game state is a snapshot of a match at an official minute: the score and the xG so far. Its
outcome is the result at the end of regulation (period 2), which is what poisson_v1 forecasts
(match_minutes = 90); extra time and shoot-outs are ignored. Snapshots are taken at official
minutes, so a real match's second-half snapshot at minute 60 lies 15 minutes after the second
half starts, whatever the first half's stoppage time.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core.events import Clock, Event, Match
from core.winprob import PoissonParams, Probs, outcome_probs

OUTCOMES = ("home", "draw", "away")


@dataclass(frozen=True)
class State:
    match_id: str
    minute: int  # official minute at the snapshot (0 = kick-off)
    t: float  # match time in seconds (core/events.Clock)
    goals: tuple[int, int]  # home, away, so far
    xg: tuple[float, float]  # home, away, so far
    outcome: int  # index into OUTCOMES, at the end of regulation


Predictor = Callable[[State], Probs]


def regulation_end(clock: Clock) -> float:
    """Match time at which the second half ends: the start of extra time, or the final whistle."""
    return clock.period_starts[2] if len(clock.period_starts) > 2 else clock.end_t


def snapshot_t(clock: Clock, minute: int) -> float:
    """Match time at the start of an official minute; minutes from 45 count from the second half."""
    if minute < 45 or len(clock.period_starts) < 2:
        return 60.0 * minute
    return clock.period_starts[1] + 60.0 * (minute - 45)


def _so_far(
    events: Sequence[Event], teams: tuple[str, str], t: float
) -> tuple[tuple[int, int], tuple[float, float]]:
    """Goals and xG per side from events strictly before match time t."""
    past = [e for e in events if e.t < t]

    def goals(tm: str) -> int:
        return sum(e.type == "goal" and e.team == tm for e in past)

    def xg(tm: str) -> float:
        return sum(e.xg or 0.0 for e in past if e.type == "shot" and e.team == tm)

    return (goals(teams[0]), goals(teams[1])), (xg(teams[0]), xg(teams[1]))


def outcome_at(events: Sequence[Event], teams: tuple[str, str], t: float) -> int:
    """0 home win, 1 draw, 2 away win, on goals before match time t."""
    (h, a), _ = _so_far(events, teams, t)
    return 0 if h > a else 1 if h == a else 2


def match_states(match: Match, minutes: Sequence[int]) -> list[State]:
    teams = (match.meta.home, match.meta.away)
    return states(match.events, teams, match.meta.clock, match.meta.match_id, minutes)


def states(
    events: Sequence[Event],
    teams: tuple[str, str],
    clock: Clock,
    match_id: str,
    minutes: Sequence[int],
) -> list[State]:
    """One state per official minute, all sharing the match's result at the end of regulation."""
    result = outcome_at(events, teams, regulation_end(clock) + 0.001)
    out = []
    for m in minutes:
        t = snapshot_t(clock, m)
        goals, xg = _so_far(events, teams, t)
        out.append(State(match_id, m, t, goals, xg, result))
    return out


def brier(p: Probs, outcome: int) -> float:
    """Multi-class Brier score: sum of squared errors over the three outcomes (0 is perfect)."""
    return sum((q - (i == outcome)) ** 2 for i, q in enumerate(p))


def log_loss(p: Probs, outcome: int, floor: float = 1e-12) -> float:
    return -math.log(max(p[outcome], floor))


def outcome_rates(rows: Sequence[State]) -> Probs:
    """Share of home wins, draws and away wins over distinct matches (the pre-match constant)."""
    by_match = {r.match_id: r.outcome for r in rows}
    n = len(by_match)
    h, d, a = (sum(o == i for o in by_match.values()) / n for i in range(3))
    return h, d, a


def diff_bucket(goals: tuple[int, int]) -> str:
    """Home goal difference, capped at two either way."""
    d = max(-2, min(2, goals[0] - goals[1]))
    return {-2: "-2 or less", 2: "+2 or more"}.get(d, f"{d:+d}" if d else "level")


def phase_bucket(minute: int, edges: Sequence[int]) -> str:
    """Label of the time phase holding the minute, from edges like [0, 30, 60, 90]."""
    i = max(k for k, e in enumerate(edges[:-1]) if minute >= e)
    return f"{edges[i]}-{edges[i + 1]}"


def mean_score(
    rows: Sequence[State], pred: Predictor, score: Callable[[Probs, int], float]
) -> float:
    return sum(score(pred(r), r.outcome) for r in rows) / len(rows)


def bootstrap_diff(
    rows: Sequence[State], a: Predictor, b: Predictor, n: int, seed: int
) -> tuple[float, float]:
    """95% interval of mean Brier(a) - Brier(b), resampling whole matches (states within a match
    are not independent)."""
    per: dict[str, list[float]] = {}
    for r in rows:
        per.setdefault(r.match_id, []).append(brier(a(r), r.outcome) - brier(b(r), r.outcome))
    ids, rng, stats = sorted(per), random.Random(seed), []
    for _ in range(n):
        pick = [per[rng.choice(ids)] for _ in ids]
        stats.append(sum(map(sum, pick)) / sum(map(len, pick)))
    stats.sort()
    return stats[int(0.025 * n)], stats[min(n - 1, int(0.975 * n))]


@dataclass(frozen=True)
class Bucket:
    state: str  # home goal difference (diff_bucket)
    phase: str  # minutes (phase_bucket)
    n: int  # snapshots
    matches: int
    predicted: Probs  # mean predicted (home, draw, away)
    observed: Probs  # share of snapshots that ended home win, draw, away win


def calibration(rows: Sequence[State], pred: Predictor, phases: Sequence[int]) -> list[Bucket]:
    """Mean prediction against observed results, by goal difference and time phase."""
    groups: dict[tuple[str, str], list[State]] = {}
    for r in rows:
        groups.setdefault((phase_bucket(r.minute, phases), diff_bucket(r.goals)), []).append(r)
    out = []
    for (phase, state), rs in sorted(groups.items()):
        n, ps = len(rs), [pred(r) for r in rs]
        mean = tuple(round(sum(p[i] for p in ps) / n, 4) for i in range(3))
        seen = tuple(round(sum(r.outcome == i for r in rs) / n, 4) for i in range(3))
        matches = len({r.match_id for r in rs})
        out.append(Bucket(state, phase, n, matches, mean, seen))  # type: ignore[arg-type]
    return out


def v2_predictor(mu: float, p: PoissonParams) -> Predictor:
    return lambda r: outcome_probs(r.t / 60, *r.goals, *r.xg, mu, p)


def fit_v2(
    train: Sequence[State],
    mu: float,
    length: float,
    k_grid: Sequence[float],
    rho_grid: Sequence[float],
    goal_cap: int,
) -> PoissonParams:
    """poisson_v2 parameters: the given match length, and the (k, rho) on the grids with the
    lowest mean Brier score on the training states."""
    grid = [PoissonParams(k, length, rho, goal_cap) for k in k_grid for rho in rho_grid]
    return min(grid, key=lambda p: mean_score(train, v2_predictor(mu, p), brier))
