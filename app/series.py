"""Per-minute series for the replay's charts (win probability, team stats, attack lanes).

Every value comes from core/: compute_metric, win_probability, wilson and judge_share.
The front end only draws these numbers; it computes none of its own.
"""

from __future__ import annotations

import math
from typing import Any

from core.config import MetricContext, Settings
from core.events import Clock, Event
from core.facts import Subject, Window
from core.hedge import ShareJudgement, judge_share
from core.intervals import Interval, intervals
from core.metrics import compute_metric, require_metric
from core.moments import score_at
from core.network import betweenness, communities, degree, pass_links, weights
from core.shares import LANES
from core.sources import League
from core.wilson import wilson
from core.winprob import win_probability
from core.xt import completed_move, gain

TEAM_METRICS = (
    "shots",
    "xg_sum",
    "npxg_sum",
    "shots_on_target",
    "shots_off_target",
    "shots_blocked",
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


def tilt_at(
    events: list[Event], team: str, t: float, window_s: int, ctx: MetricContext
) -> float | None:
    """Field tilt (%) over the trailing window ending at t; None with no final-third passes."""
    window = Window(start_t=max(0.0, t - window_s), end_t=t)
    m = require_metric("field_tilt", Subject(type="team", id=team), window, events, ctx)
    return None if m.n == 0 else _r(m.value)


def _win_prob(
    events: list[Event], t: float, teams: tuple[str, str], mu: float, s: Settings
) -> tuple[float, float, float]:
    goals = score_at(events, t, teams)
    xg = [team_stat(events, tm, "xg_sum", t, MetricContext.of(s)) or 0.0 for tm in teams]
    return win_probability(t / 60, goals[teams[0]], goals[teams[1]], xg[0], xg[1], mu, s.winprob)


def win_prob_at(
    events: list[Event], t: float, teams: tuple[str, str], mu: float, s: Settings
) -> list[float]:
    """[home, draw, away] win probability (model estimate) at time t."""
    return [_r(p) for p in _win_prob(events, t, teams, mu, s)]


def _upto(b: Interval, t: float) -> Window:
    return Window(start_t=b.start_t, end_t=min(t, b.end_t))


def whole_percent(probs: tuple[float, ...]) -> list[int]:
    """Probabilities as whole percentages that add up to 100 (largest remainder; a tie goes to
    the earlier outcome)."""
    raw = [100 * p / sum(probs) for p in probs]
    out = [math.floor(x) for x in raw]
    by_rest = sorted(range(len(raw)), key=lambda i: (-(raw[i] - out[i]), i))
    for i in by_rest[: 100 - sum(out)]:
        out[i] += 1
    return out


def result_pct_at(
    events: list[Event], t: float, teams: tuple[str, str], mu: float, s: Settings
) -> list[int]:
    """[home win, draw, away win] in whole per cent (model estimate) at time t."""
    return whole_percent(_win_prob(events, t, teams, mu, s))


def npxg_by_interval(
    events: list[Event], team: str, t: float, blocks: list[Interval], ctx: MetricContext
) -> list[float]:
    """The team's npxG in each interval that has started by t, counted up to t."""
    subject = Subject(type="team", id=team)
    return [
        _r(require_metric("npxg_sum", subject, _upto(b, t), events, ctx).value)
        for b in blocks
        if b.start_t <= t
    ]


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


NETWORK_STEP_S = 300  # pass networks are sampled every 5 minutes; they change slowly


def network_at(events: list[Event], team: str, start: float, t: float) -> dict[str, Any]:
    """The team's pass network from `start` (kick-off or the start of the second half) to t.
    Nodes: [player, mean x, mean y, degree, betweenness, community]; position is the mean of
    where the player passed from and received, in the team's attacking frame."""
    evs = [e for e in events if start <= e.t <= t]
    links = pass_links(evs, team)
    w = weights(links)
    deg, btw = degree(w), betweenness(w)
    group = {p: i for i, g in enumerate(communities(w)) for p in g}
    spots: dict[str, list[tuple[float, float]]] = {p: [] for p in deg}
    by_id = {e.id: e for e in evs}
    for x in links:
        e = by_id[x.event_id]
        spots[x.passer].append((e.x, e.y))
        if e.end_x is not None and e.end_y is not None:
            spots[x.receiver].append((e.end_x, e.end_y))
    nodes = [
        [
            p,
            _r(_mean(s[0] for s in spots[p])),
            _r(_mean(s[1] for s in spots[p])),
            deg[p],
            _r(btw[p]),
            group[p],
        ]
        for p in sorted(deg)
    ]
    return {
        "n": len(links),
        "nodes": nodes,
        "edges": [[a, b, k] for (a, b), k in sorted(w.items())],
    }


def _mean(xs: Any) -> float:
    vals = list(xs)
    return sum(vals) / len(vals) if vals else 0.0


def network_panel(events: list[Event], teams: tuple[str, str], half_t: float) -> dict[str, Any]:
    """Pass networks for the current half, every NETWORK_STEP_S seconds."""
    ts = grid(events, NETWORK_STEP_S)
    return {
        "step_s": NETWORK_STEP_S,
        "t": ts,
        "half_t": half_t,
        "teams": {
            tm: [network_at(events, tm, 0.0 if t < half_t else half_t, t) for t in ts]
            for tm in teams
        },
    }


def build_series(
    events: list[Event], teams: tuple[str, str], league: League, s: Settings, clock: Clock
) -> dict[str, Any]:
    """Column-oriented series sampled every minute."""
    step, ctx = 60, MetricContext.of(s)
    ts = grid(events, step)
    blocks = intervals(clock, s.moments.interval_minutes)
    return {
        "step_s": step,
        "lane_window_s": s.moments.overload_window_s,
        "lane_prior": {k: round(v, 4) for k, v in league.lane_shares.items()},
        "lane_deff": round(league.attack_deff, 3),
        "hedge_clear_prob": s.hedge.clear_prob,
        "tilt_window_s": s.series.tilt_window_s,
        "t": ts,
        "win_prob": [win_prob_at(events, t, teams, league.mu, s) for t in ts],
        "result_pct": [result_pct_at(events, t, teams, league.mu, s) for t in ts],
        "intervals": [{"start_t": b.start_t, "end_t": b.end_t, "label": b.label} for b in blocks],
        "teams": {
            tm: {m: [team_stat(events, tm, m, t, ctx) for t in ts] for m in TEAM_METRICS}
            | {"field_tilt": [tilt_at(events, tm, t, s.series.tilt_window_s, ctx) for t in ts]}
            | {"npxg_by_interval": [npxg_by_interval(events, tm, t, blocks, ctx) for t in ts]}
            for tm in teams
        },
        "lanes": {tm: [lanes_at(events, tm, t, league, s) for t in ts] for tm in teams},
        "xt": xt_panel(events, teams, ctx),
        "network": network_panel(events, teams, float(clock.half_t)),
    }
