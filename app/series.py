"""Per-minute series for the replay's charts (win probability, team stats, attack lanes).

Every value comes from core/: compute_metric, win_probability, wilson and hedge_for.
The front end only draws these numbers; it computes none of its own.
"""
from __future__ import annotations

import math
from typing import Any

from core.config import load
from core.moments import compute_metric, score_at
from core.shares import LANES
from core.wilson import hedge_for, wilson
from core.winprob import win_probability

Event = dict[str, Any]
TEAM_METRICS = ("shots", "xg_sum", "pass_accuracy", "turnovers_own_third", "top_shot_speed")


def _r(x: float) -> float:
    return round(x, 3)


def grid(events: list[Event], step_s: int) -> list[float]:
    """Sample times from kick-off to the last event, inclusive, every step_s seconds."""
    last = max(e["t"] for e in events)
    return [float(i * step_s) for i in range(math.ceil(last / step_s) + 1)]


def team_stat(events: list[Event], team: str, metric: str, t: float) -> float | None:
    """A metric for a team from kick-off to t (None if not computable yet)."""
    m = compute_metric(metric, {"type": "team", "id": team}, {"start_t": 0.0, "end_t": t}, events)
    return None if m is None or (metric == "pass_accuracy" and m.n == 0) else _r(m.value)


def win_prob_at(events: list[Event], t: float, teams: tuple[str, str], mu: float) -> list[float]:
    """[home, draw, away] win probability (model estimate) at time t."""
    goals = score_at(events, t, teams)
    xg = [team_stat(events, tm, "xg_sum", t) or 0.0 for tm in teams]
    home, draw, away = win_probability(t / 60, goals[teams[0]], goals[teams[1]], xg[0], xg[1], mu)
    return [_r(home), _r(draw), _r(away)]


def lanes_at(events: list[Event], team: str, t: float) -> dict[str, Any]:
    """Attack share by lane over the trailing window, with Wilson intervals and the hedge."""
    c, v = load()["moments"], load()["verifier"]
    window = {"start_t": max(0.0, t - c["overload_window_s"]), "end_t": t}
    out: dict[str, Any] = {}
    ci: dict[str, tuple[float, float]] = {}
    for lane in LANES:
        m = compute_metric(f"attack_share_{lane}", {"type": "team", "id": team}, window, events)
        ci[lane] = wilson(m.k, m.n, v["z"])
        out[lane] = {"k": m.k, "share": _r(m.value / 100), "lo": _r(ci[lane][0]), "hi": _r(ci[lane][1])}
        out["n"] = m.n
    top = max(LANES, key=lambda ln: out[ln]["k"])
    hedge = hedge_for(out["n"], ci[top][0], ci[top][1], v["lane_baseline"], v["n_min"])
    out["top"], out["hedge"] = top, hedge
    out["overload"] = top if hedge == "clear" and out[top]["share"] >= c["overload_min_share"] else None
    return out


def build_series(events: list[Event], teams: tuple[str, str], mu: float) -> dict[str, Any]:
    """Column-oriented series sampled every minute."""
    step = 60
    ts = grid(events, step)
    return {
        "step_s": step,
        "lane_window_s": load()["moments"]["overload_window_s"],
        "lane_baseline": load()["verifier"]["lane_baseline"],
        "t": ts,
        "win_prob": [win_prob_at(events, t, teams, mu) for t in ts],
        "teams": {tm: {m: [team_stat(events, tm, m, t) for t in ts] for m in TEAM_METRICS} for tm in teams},
        "lanes": {tm: [lanes_at(events, tm, t) for t in ts] for tm in teams},
    }
