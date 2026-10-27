"""Metric computation over an event window, and rule-based moment detection.

`compute_metric` is the single definition of every metric. The moment rules
and the verifier both use it, so a claim is always rechecked the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.config import load
from core.shares import attacks_by_lane
from core.winprob import win_probability

Event = dict[str, Any]


@dataclass
class Metric:
    value: float
    n: int
    k: int = 0
    ids: list[int] = field(default_factory=list)


def minute(t: float) -> int:
    """Football minute for a clock time in seconds (0-59 s is the 1st minute)."""
    return int(t // 60) + 1


def ordinal(n: int) -> str:
    """1 -> 1st, 2 -> 2nd, 63 -> 63rd, 11 -> 11th."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def in_window(events: list[Event], start: float, end: float) -> list[Event]:
    return [e for e in events if start <= e["t"] <= end]


def _of(events: list[Event], subject: dict[str, str]) -> list[Event]:
    if subject["type"] == "team":
        return [e for e in events if e["team"] == subject["id"]]
    if subject["type"] == "player":
        return [e for e in events if e["player_id"] == subject["id"]]
    return events


def _count(events: list[Event]) -> Metric:
    return Metric(len(events), len(events), len(events), [e["id"] for e in events])


def _share(hits: list[int], total: int) -> Metric:
    value = 100 * len(hits) / total if total else 0.0
    return Metric(value, total, len(hits), hits)


def compute_metric(metric: str, subject: dict[str, str], window: dict[str, float],
                   events: list[Event]) -> Metric | None:
    """Recompute a metric from the events in a window. None if not computable."""
    evs = _of(in_window(events, window["start_t"], window["end_t"]), subject)
    shots = [e for e in evs if e["type"] == "shot"]
    if metric == "goals":
        return _count([e for e in evs if e["type"] == "goal"])
    if metric == "shots":
        return _count(shots)
    if metric == "substitutions":
        return _count([e for e in evs if e["type"] == "substitution"])
    if metric == "cards":
        return _count([e for e in evs if e["type"] == "card"])
    if metric == "xg_sum":
        return Metric(sum(e["xg"] for e in shots), len(shots), len(shots), [e["id"] for e in shots])
    if metric == "top_shot_speed":
        if not shots:
            return None
        best = max(shots, key=lambda e: e["shot_speed"])
        return Metric(best["shot_speed"], len(shots), 1, [best["id"]])
    if metric == "turnovers_own_third":
        own = load()["pitch"]["own_third_x"]
        return _count([e for e in evs if e["type"] == "possession_change" and e["x"] < own])
    if metric == "pass_accuracy":
        passes = [e for e in evs if e["type"] == "pass"]
        return _share([e["id"] for e in passes if e["outcome"] == "success"], len(passes))
    if metric.startswith("attack_share_") and subject["type"] == "team":
        groups = attacks_by_lane(evs, subject["id"])
        return _share(groups[metric.removeprefix("attack_share_")], sum(len(v) for v in groups.values()))
    return None


# ---- moment rules ------------------------------------------------------

def _team(team: str) -> dict[str, str]:
    return {"type": "team", "id": team}


def _moment(kind: str, rule: str, metric: str, subject: dict[str, str], start: float, end: float,
            m: Metric, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "rule": rule, "metric": metric, "subject": subject,
            "window": {"start_t": start, "end_t": end}, "created_t": end,
            "value": m.value, "n": m.n, "k": m.k, "evidence_ids": m.ids, **extra}


def score_at(events: list[Event], t: float, teams: tuple[str, str]) -> dict[str, int]:
    return {tm: sum(e["type"] == "goal" and e["team"] == tm and e["t"] <= t for e in events) for tm in teams}


def _wp(events: list[Event], t: float, team: str, other: str, mu: float, inclusive: bool) -> float:
    past = [e for e in events if (e["t"] <= t if inclusive else e["t"] < t)]
    g = {tm: sum(e["type"] == "goal" and e["team"] == tm for e in past) for tm in (team, other)}
    x = {tm: sum(e.get("xg", 0.0) for e in past if e["type"] == "shot" and e["team"] == tm) for tm in (team, other)}
    return win_probability(t / 60, g[team], g[other], x[team], x[other], mu)[0]


def goal_moments(events: list[Event], teams: tuple[str, str], mu: float) -> list[dict[str, Any]]:
    out = []
    for g in (e for e in events if e["type"] == "goal"):
        shot = next(e for e in reversed(events) if e["type"] == "shot" and e["t"] <= g["t"] and e["team"] == g["team"])
        other = teams[1] if g["team"] == teams[0] else teams[0]
        before = _wp(events, shot["t"], g["team"], other, mu, inclusive=False)
        after = _wp(events, g["t"], g["team"], other, mu, inclusive=True)
        m = Metric(1, 1, 1, [shot["id"], g["id"]])
        out.append(_moment("score_event", "goal", "goals", {"type": "player", "id": g["player_id"]},
                           shot["t"], g["t"], m, team=g["team"], players=[g["player_id"]],
                           score=score_at(events, g["t"], teams),
                           impact={"subject_team": g["team"], "win_prob_before": round(before, 3),
                                   "win_prob_after": round(after, 3), "delta": round(after - before, 3),
                                   "method": "poisson_v1"}))
    return out


def event_moments(events: list[Event]) -> list[dict[str, Any]]:
    """Plain event-log moments: substitutions and cards."""
    out = []
    for e in events:
        m = Metric(1, 1, 1, [e["id"]])
        if e["type"] == "substitution":
            out.append(_moment("moment", "substitution", "substitutions", _team(e["team"]), e["t"], e["t"], m,
                               players=[e["player_id"], e["player_off"]]))
        elif e["type"] == "card":
            out.append(_moment("moment", "card", "cards", {"type": "player", "id": e["player_id"]}, e["t"], e["t"], m,
                               team=e["team"], players=[e["player_id"]], card=e.get("card", "yellow")))
    return out


def _checkpoints() -> list[float]:
    c = load()["moments"]
    end = 2 * load()["sim"]["half_s"]
    return [float(t) for t in range(c["first_check_s"], end + 1, c["check_every_s"])]


def _overload_start(events: list[Event], team: str, cp: float) -> float:
    """Window start: the team's latest substitution if recent, else a rolling window."""
    c = load()["moments"]
    subs = [e["t"] for e in events if e["type"] == "substitution" and e["team"] == team and e["t"] < cp]
    if subs and cp - subs[-1] <= 2 * c["overload_window_s"]:
        return subs[-1]
    return max(0.0, cp - c["overload_window_s"])


def overload_moments(events: list[Event], teams: tuple[str, str]) -> list[dict[str, Any]]:
    c, n_min = load()["moments"], load()["verifier"]["n_min"]
    out, last = [], {}
    for cp in _checkpoints():
        for team in teams:
            start = _overload_start(events, team, cp)
            shares = {ln: compute_metric(f"attack_share_{ln}", _team(team), {"start_t": start, "end_t": cp}, events)
                      for ln in ("left", "centre", "right")}
            lane, m = max(shares.items(), key=lambda kv: kv[1].value)
            if m.n < n_min or m.value < 100 * c["overload_min_share"]:
                continue
            if team in last and last[team][1] == lane and cp - last[team][0] < c["overload_window_s"]:
                continue
            last[team] = (cp, lane)
            out.append(_moment("tactical", "overload", f"attack_share_{lane}", _team(team), start, cp, m, lane=lane))
    return out


def turnover_moments(events: list[Event], teams: tuple[str, str]) -> list[dict[str, Any]]:
    c = load()["moments"]
    out, next_ok = [], {tm: 0.0 for tm in teams}
    for cp in _checkpoints():
        for team in teams:
            start = cp - c["turnover_window_s"]
            m = compute_metric("turnovers_own_third", _team(team), {"start_t": start, "end_t": cp}, events)
            if m and m.value >= c["turnover_min_count"] and cp >= next_ok[team]:
                next_ok[team] = cp + c["turnover_window_s"]
                out.append(_moment("stat", "turnovers", "turnovers_own_third", _team(team), start, cp, m))
    return out


def half_moments(events: list[Event], teams: tuple[str, str]) -> list[dict[str, Any]]:
    half = float(load()["sim"]["half_s"])
    out = []
    # The simulator keeps first-half events strictly below half_s.
    for idx, (start, end) in enumerate(((0.0, half - 0.1), (half, 2 * half))):
        name = ("first", "second")[idx]
        w = {"start_t": start, "end_t": end}
        for team in teams:
            for metric in ("xg_sum", "pass_accuracy"):
                m = compute_metric(metric, _team(team), w, events)
                out.append(_moment("stat", metric, metric, _team(team), start, end, m, half=name))
        top = compute_metric("top_shot_speed", {"type": "match", "id": "match"}, w, events)
        if top:
            shooter = next(e for e in events if e["id"] == top.ids[0])["player_id"]
            out.append(_moment("milestone", "top_speed", "top_shot_speed", {"type": "match", "id": "match"},
                               start, end, top, half=name, players=[shooter]))
        out += _top_shooter(events, w, name)
    return out


def _top_shooter(events: list[Event], w: dict[str, float], name: str) -> list[dict[str, Any]]:
    shots = [e for e in in_window(events, w["start_t"], w["end_t"]) if e["type"] == "shot"]
    if not shots:
        return []
    counts: dict[str, int] = {}
    for e in shots:
        counts[e["player_id"]] = counts.get(e["player_id"], 0) + 1
    pid = max(sorted(counts), key=lambda p: counts[p])
    if counts[pid] < 2:
        return []
    subj = {"type": "player", "id": pid}
    m = compute_metric("shots", subj, w, events)
    return [_moment("stat", "player_shots", "shots", subj, w["start_t"], w["end_t"], m, half=name, players=[pid])]


def detect_moments(events: list[Event], teams: tuple[str, str], mu: float) -> list[dict[str, Any]]:
    """All moments in the match, sorted by creation time."""
    moments = (goal_moments(events, teams, mu) + event_moments(events) + overload_moments(events, teams)
               + turnover_moments(events, teams) + half_moments(events, teams))
    return sorted(moments, key=lambda m: (m["created_t"], m["rule"]))
