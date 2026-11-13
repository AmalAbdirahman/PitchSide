"""Per-minute series for the replay's charts (win probability, team stats, attack lanes).

Every value comes from core/: compute_metric, win_probability, wilson and judge_share.
The front end only draws these numbers; it computes none of its own.
"""

from __future__ import annotations

import math
from typing import Any

from core.config import MetricContext, Settings
from core.events import Event
from core.facts import Subject, Window
from core.hedge import ShareJudgement, judge_share
from core.metrics import compute_metric, require_metric
from core.moments import score_at
from core.shares import LANES
from core.sources import League
from core.wilson import wilson
from core.winprob import win_probability
from core.xt import completed_move, gain

TEAM_METRICS = (
    "shots",
    "xg_sum",
    "pass_accuracy",
    "turnovers_own_third",
    "top_shot_speed",
    "xt_added",
)


def _r(x: float) -> float:
    return round(x, 3)


def grid(events: list[Event], step_s: int) -> list[float]:
    """Sample times from kick-off to the last event, inclusive, every step_s seconds."""
    last = max(e.t for e in events)
    return [float(i * step_s) for i in range(math.ceil(last / step_s) + 1)]


def team_stat(
    events: list[Event], team: str, metric: str, t: float, ctx: MetricContext
) -> float | None:
    """A metric for a team from kick-off to t (None if not computable yet)."""
    subject, window = Subject(type="team", id=team), Window(start_t=0.0, end_t=t)
    m = compute_metric(metric, subject, window, events, ctx)
    return None if m is None or (metric == "pass_accuracy" and m.n == 0) else _r(m.value)


def win_prob_at(
    events: list[Event], t: float, teams: tuple[str, str], mu: float, s: Settings
) -> list[float]:
    """[home, draw, away] win probability (model estimate) at time t."""
    goals = score_at(events, t, teams)
    xg = [team_stat(events, tm, "xg_sum", t, MetricContext.of(s)) or 0.0 for tm in teams]
    home, draw, away = win_probability(
        t / 60, goals[teams[0]], goals[teams[1]], xg[0], xg[1], mu, s.winprob
    )
    return [_r(home), _r(draw), _r(away)]


def lanes_at(
    events: list[Event], team: str, t: float, league: League, s: Settings
) -> dict[str, Any]:
    """Attack share by lane over the trailing window: Wilson intervals on the raw counts, and
    P(share > league share) under the Beta-Binomial hedge, which sets the hedge for the top lane."""
    c, v = s.moments, s.verifier
    window = Window(start_t=max(0.0, t - c.overload_window_s), end_t=t)
    out: dict[str, Any] = {}
    judged: dict[str, ShareJudgement] = {}
    ctx = MetricContext.of(s)
    for lane in LANES:
        subject = Subject(type="team", id=team)
        m = require_metric(f"attack_share_{lane}", subject, window, events, ctx)
        lo, hi = wilson(m.k, m.n, v.z)
        base = league.lane_shares[lane]
        judged[lane] = judge_share(m.k, m.n, base, s.hedge, v.n_min, league.attack_deff)
        out[lane] = {
            "k": m.k,
            "share": _r(m.value / 100),
            "lo": _r(lo),
            "hi": _r(hi),
            "p_above": _r(judged[lane].p_above),
        }
        out["n"] = m.n
    top = max(LANES, key=lambda ln: out[ln]["k"])
    hedge = judged[top].hedge
    out["top"], out["hedge"] = top, hedge
    out["overload"] = (
        top if hedge == "clear" and out[top]["share"] >= c.overload_min_share else None
    )
    return out


def xt_panel(events: list[Event], teams: tuple[str, str], ctx: MetricContext) -> dict[str, Any]:
    """The fitted surface and each completed move's xT gain, per team, for the threat map."""
    return {
        "cols": ctx.xt.cols,
        "rows": ctx.xt.rows,
        "surface": [list(r) for r in ctx.xt.surface],
        "moves": {
            tm: [
                [e.id, round(gain(e, ctx.xt, ctx.pitch), 4)]
                for e in events
                if e.team == tm and completed_move(e)
            ]
            for tm in teams
        },
    }


def build_series(
    events: list[Event], teams: tuple[str, str], league: League, s: Settings
) -> dict[str, Any]:
    """Column-oriented series sampled every minute."""
    step, ctx = 60, MetricContext.of(s)
    ts = grid(events, step)
    return {
        "step_s": step,
        "lane_window_s": s.moments.overload_window_s,
        "lane_prior": {k: round(v, 4) for k, v in league.lane_shares.items()},
        "lane_deff": round(league.attack_deff, 3),
        "hedge_clear_prob": s.hedge.clear_prob,
        "t": ts,
        "win_prob": [win_prob_at(events, t, teams, league.mu, s) for t in ts],
        "teams": {
            tm: {m: [team_stat(events, tm, m, t, ctx) for t in ts] for m in TEAM_METRICS}
            for tm in teams
        },
        "lanes": {tm: [lanes_at(events, tm, t, league, s) for t in ts] for tm in teams},
        "xt": xt_panel(events, teams, ctx),
    }
