"""Rule-based moment detection over the event log.

Moments are found with the same metric definitions the verifier uses to recheck
claims (`core/metrics.py`), so a claim is always rechecked the way it was found.
"""

from __future__ import annotations

from typing import Any

from core.config import Pitch, Settings, Winprob
from core.events import Clock, Event
from core.facts import Subject, Window
from core.metrics import Metric, compute_metric, in_window, require_metric
from core.winprob import win_probability


def ordinal(n: int) -> str:
    """1 -> 1st, 2 -> 2nd, 63 -> 63rd, 11 -> 11th."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


# ---- moment rules ------------------------------------------------------


def _team(team: str) -> Subject:
    return Subject(type="team", id=team)


def _moment(
    kind: str,
    rule: str,
    metric: str,
    subject: Subject,
    start: float,
    end: float,
    m: Metric,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "rule": rule,
        "metric": metric,
        "subject": subject.model_dump(),
        "window": {"start_t": start, "end_t": end},
        "created_t": end,
        "value": m.value,
        "n": m.n,
        "k": m.k,
        "evidence_ids": m.ids,
        **extra,
    }


def score_at(events: list[Event], t: float, teams: tuple[str, str]) -> dict[str, int]:
    return {tm: sum(e.type == "goal" and e.team == tm and e.t <= t for e in events) for tm in teams}


def _wp(
    events: list[Event],
    t: float,
    team: str,
    other: str,
    mu: float,
    inclusive: bool,
    wp: Winprob,
) -> float:
    past = [e for e in events if (e.t <= t if inclusive else e.t < t)]
    g = {tm: sum(e.type == "goal" and e.team == tm for e in past) for tm in (team, other)}
    x = {
        tm: sum(e.xg or 0.0 for e in past if e.type == "shot" and e.team == tm)
        for tm in (team, other)
    }
    return win_probability(t / 60, g[team], g[other], x[team], x[other], mu, wp)[0]


def _scoring_shot(events: list[Event], g: Event) -> tuple[float, list[int]]:
    """Start time and evidence of a goal: the scoring side's last shot at or before it."""
    shot = next(e for e in reversed(events) if e.type == "shot" and e.t <= g.t and e.team == g.team)
    return shot.t, [shot.id, g.id]


def goal_moments(
    events: list[Event], teams: tuple[str, str], mu: float, wp: Winprob
) -> list[dict[str, Any]]:
    out = []
    for g in (e for e in events if e.type == "goal"):
        own = g.outcome == "own_goal"
        start, ids = (g.t, [g.id]) if own else _scoring_shot(events, g)
        other = teams[1] if g.team == teams[0] else teams[0]
        before = _wp(events, start, g.team, other, mu, False, wp)
        after = _wp(events, g.t, g.team, other, mu, True, wp)
        m = Metric(1, 1, 1, ids)
        out.append(
            _moment(
                "score_event",
                "goal",
                "goals",
                Subject(type="player", id=g.player_id),
                start,
                g.t,
                m,
                team=g.team,
                players=[g.player_id],
                score=score_at(events, g.t, teams),
                **({"own_goal": True} if own else {}),
                impact={
                    "subject_team": g.team,
                    "win_prob_before": round(before, 3),
                    "win_prob_after": round(after, 3),
                    "delta": round(after - before, 3),
                    "method": "poisson_v1",
                },
            )
        )
    return out


def event_moments(events: list[Event]) -> list[dict[str, Any]]:
    """Plain event-log moments: substitutions and cards."""
    out = []
    for e in events:
        m = Metric(1, 1, 1, [e.id])
        if e.type == "substitution":
            out.append(
                _moment(
                    "moment",
                    "substitution",
                    "substitutions",
                    _team(e.team),
                    e.t,
                    e.t,
                    m,
                    players=[e.player_id, e.player_off],
                )
            )
        elif e.type == "card":
            out.append(
                _moment(
                    "moment",
                    "card",
                    "cards",
                    Subject(type="player", id=e.player_id),
                    e.t,
                    e.t,
                    m,
                    team=e.team,
                    players=[e.player_id],
                    card=e.card or "yellow",
                )
            )
    return out


def _checkpoints(s: Settings, clock: Clock) -> list[float]:
    c = s.moments
    return [float(t) for t in range(c.first_check_s, int(clock.end_t) + 1, c.check_every_s)]


def _overload_start(events: list[Event], team: str, cp: float, window_s: int) -> float:
    """Window start: the team's latest substitution if recent, else a rolling window."""
    subs = [e.t for e in events if e.type == "substitution" and e.team == team and e.t < cp]
    if subs and cp - subs[-1] <= 2 * window_s:
        return subs[-1]
    return max(0.0, cp - window_s)


def overload_moments(
    events: list[Event], teams: tuple[str, str], s: Settings, clock: Clock
) -> list[dict[str, Any]]:
    c, n_min = s.moments, s.verifier.n_min
    out: list[dict[str, Any]] = []
    last: dict[str, tuple[float, str]] = {}
    for cp in _checkpoints(s, clock):
        for team in teams:
            start = _overload_start(events, team, cp, c.overload_window_s)
            w = Window(start_t=start, end_t=cp)
            shares = {
                ln: require_metric(f"attack_share_{ln}", _team(team), w, events, s.pitch)
                for ln in ("left", "centre", "right")
            }
            lane, m = max(shares.items(), key=lambda kv: kv[1].value)
            if m.n < n_min or m.value < 100 * c.overload_min_share:
                continue
            if team in last and last[team][1] == lane and cp - last[team][0] < c.overload_window_s:
                continue
            last[team] = (cp, lane)
            out.append(
                _moment(
                    "tactical",
                    "overload",
                    f"attack_share_{lane}",
                    _team(team),
                    start,
                    cp,
                    m,
                    lane=lane,
                )
            )
    return out


def turnover_moments(
    events: list[Event], teams: tuple[str, str], s: Settings, clock: Clock
) -> list[dict[str, Any]]:
    c = s.moments
    out, next_ok = [], {tm: 0.0 for tm in teams}
    for cp in _checkpoints(s, clock):
        for team in teams:
            start = cp - c.turnover_window_s
            w = Window(start_t=start, end_t=cp)
            m = compute_metric("turnovers_own_third", _team(team), w, events, s.pitch)
            if m and m.value >= c.turnover_min_count and cp >= next_ok[team]:
                next_ok[team] = cp + c.turnover_window_s
                out.append(
                    _moment("stat", "turnovers", "turnovers_own_third", _team(team), start, cp, m)
                )
    return out


def half_moments(
    events: list[Event], teams: tuple[str, str], s: Settings, clock: Clock
) -> list[dict[str, Any]]:
    half, end_t = float(clock.half_t), float(clock.end_t)
    out = []
    # Every source keeps first-half events at least 0.1 s before the second half starts.
    for idx, (start, end) in enumerate(((0.0, half - 0.1), (half, end_t))):
        name = ("first", "second")[idx]
        w = Window(start_t=start, end_t=end)
        for team in teams:
            for metric in ("xg_sum", "pass_accuracy"):
                m = require_metric(metric, _team(team), w, events, s.pitch)
                if not m.ids:  # no shots or passes in the half: nothing to cite, so no claim
                    continue
                out.append(_moment("stat", metric, metric, _team(team), start, end, m, half=name))
        match_subject = Subject(type="match", id="match")
        top = compute_metric("top_shot_speed", match_subject, w, events, s.pitch)
        if top:
            shooter = next(e for e in events if e.id == top.ids[0]).player_id
            out.append(
                _moment(
                    "milestone",
                    "top_speed",
                    "top_shot_speed",
                    match_subject,
                    start,
                    end,
                    top,
                    half=name,
                    players=[shooter],
                )
            )
        out += _top_shooter(events, w, name, s.pitch)
    return out


def _top_shooter(events: list[Event], w: Window, name: str, pitch: Pitch) -> list[dict[str, Any]]:
    shots = [e for e in in_window(events, w.start_t, w.end_t) if e.type == "shot"]
    if not shots:
        return []
    counts: dict[str, int] = {}
    for e in shots:
        counts[e.player_id] = counts.get(e.player_id, 0) + 1
    pid = max(sorted(counts), key=lambda p: counts[p])
    if counts[pid] < 2:
        return []
    subj = Subject(type="player", id=pid)
    m = require_metric("shots", subj, w, events, pitch)
    return [
        _moment(
            "stat",
            "player_shots",
            "shots",
            subj,
            w.start_t,
            w.end_t,
            m,
            half=name,
            players=[pid],
        )
    ]


def detect_moments(
    events: list[Event], teams: tuple[str, str], mu: float, s: Settings, clock: Clock
) -> list[dict[str, Any]]:
    """All moments in the match, sorted by creation time."""
    moments = (
        goal_moments(events, teams, mu, s.winprob)
        + event_moments(events)
        + overload_moments(events, teams, s, clock)
        + turnover_moments(events, teams, s, clock)
        + half_moments(events, teams, s, clock)
    )
    return sorted(moments, key=lambda m: (m["created_t"], m["rule"]))
