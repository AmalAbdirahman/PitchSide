"""Every metric the verifier can recompute, in one registry.

A metric declares its name, kind, the subject types it applies to, its tolerance
(a field of the verifier config) and how to compute it from the events in a window.
The moment rules, the chart series and the verifier all compute through
`REGISTRY`, so a claim is always rechecked the same way it was found.
A metric name with no entry here cannot be recomputed, so a claim about it is
unverifiable and never airs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from core.config import MetricContext, Verifier
from core.events import Event
from core.facts import Subject, Window
from core.network import betweenness, pass_links, rank, weights
from core.shares import LANES, attacks_by_lane
from core.xt import completed_move, gain

Kind = Literal["count", "share", "sum", "max", "rank"]
SubjectType = Literal["team", "player", "match"]
# "exact" means no tolerance; the others name a field of the verifier config.
ToleranceKey = Literal["exact", "tol_share_pp", "tol_xg", "tol_speed", "tol_xt"]
ALL_SUBJECTS: frozenset[SubjectType] = frozenset({"team", "player", "match"})
EPS = 1e-9


@dataclass
class Metric:
    """A recomputed value: `n` events considered, `k` of them counted, `ids` the evidence."""

    value: float
    n: int
    k: int = 0
    ids: list[int] = field(default_factory=list)


class MetricDef(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> Kind: ...

    @property
    def subjects(self) -> frozenset[SubjectType]:
        """Subject types the metric is defined for; any other subject is not computable."""
        ...

    def tolerance(self, v: Verifier) -> float: ...

    def value_matches(self, claimed: float, observed: float, v: Verifier) -> bool: ...

    def compute(
        self, events: list[Event], subject: Subject, window: Window, ctx: MetricContext
    ) -> Metric | None: ...

    @property
    def checks_sample(self) -> bool:
        """True if the value is a share, so it gets a Wilson interval and a hedge."""
        ...


Compute = Callable[[list[Event], Subject, MetricContext], Metric | None]


@dataclass(frozen=True)
class EventMetric:
    """A metric computed from the subject's events inside the window."""

    name: str
    kind: Kind
    tolerance_key: ToleranceKey
    fn: Compute
    subjects: frozenset[SubjectType] = ALL_SUBJECTS

    def tolerance(self, v: Verifier) -> float:
        return 0.0 if self.tolerance_key == "exact" else float(getattr(v, self.tolerance_key))

    def value_matches(self, claimed: float, observed: float, v: Verifier) -> bool:
        return abs(observed - claimed) <= self.tolerance(v) + EPS

    @property
    def checks_sample(self) -> bool:
        return self.kind == "share"

    def compute(
        self, events: list[Event], subject: Subject, window: Window, ctx: MetricContext
    ) -> Metric | None:
        if subject.type not in self.subjects:
            return None
        return self.fn(
            of_subject(in_window(events, window.start_t, window.end_t), subject), subject, ctx
        )


@dataclass(frozen=True)
class CentralityRank:
    """most_central_player: the player's rank by betweenness in their team's pass network over
    the window (core/network.py); 1 means the unique most central player. n is the team's passes
    with a receiver, k and ids those the player made or received. Ranks must match exactly."""

    name: str = "most_central_player"
    kind: Kind = "rank"
    subjects: frozenset[SubjectType] = frozenset({"player"})

    def tolerance(self, v: Verifier) -> float:
        return 0.0

    def value_matches(self, claimed: float, observed: float, v: Verifier) -> bool:
        return abs(observed - claimed) <= EPS

    @property
    def checks_sample(self) -> bool:
        return False

    def compute(
        self, events: list[Event], subject: Subject, window: Window, ctx: MetricContext
    ) -> Metric | None:
        if subject.type != "player":
            return None
        evs = in_window(events, window.start_t, window.end_t)
        team = next((e.team for e in evs if e.player_id == subject.id), None)
        links = [] if team is None else pass_links(evs, team)
        scores = betweenness(weights(links))
        if subject.id not in scores:
            return None
        mine = sorted(x.event_id for x in links if subject.id in (x.passer, x.receiver))
        return Metric(float(rank(scores, subject.id)), len(links), len(mine), mine)


@dataclass(frozen=True)
class FieldTilt:
    """field_tilt: the team's share (%) of both teams' passes that start in the final third they
    attack, over the window. Coordinates are per team, attacking left to right, so one threshold
    (pitch.final_third_x) serves both. Needs the opponent's passes, so it is not an EventMetric.
    n is both teams' final-third passes, k and ids the subject team's."""

    name: str = "field_tilt"
    kind: Kind = "share"
    subjects: frozenset[SubjectType] = frozenset({"team"})

    def tolerance(self, v: Verifier) -> float:
        return v.tol_share_pp

    def value_matches(self, claimed: float, observed: float, v: Verifier) -> bool:
        return abs(observed - claimed) <= v.tol_share_pp + EPS

    @property
    def checks_sample(self) -> bool:
        return True

    def compute(
        self, events: list[Event], subject: Subject, window: Window, ctx: MetricContext
    ) -> Metric | None:
        if subject.type != "team":
            return None
        line = ctx.pitch.final_third_x
        passes = [
            e
            for e in in_window(events, window.start_t, window.end_t)
            if e.type == "pass" and e.x >= line
        ]
        return _share([e.id for e in passes if e.team == subject.id], len(passes))


# ---- event selection -----------------------------------------------------------------


def in_window(events: list[Event], start: float, end: float) -> list[Event]:
    return [e for e in events if start <= e.t <= end]


def of_subject(events: list[Event], subject: Subject) -> list[Event]:
    if subject.type == "team":
        return [e for e in events if e.team == subject.id]
    if subject.type == "player":
        return [e for e in events if e.player_id == subject.id]
    return events


def _count(events: list[Event]) -> Metric:
    return Metric(len(events), len(events), len(events), [e.id for e in events])


def _share(hits: list[int], total: int) -> Metric:
    value = 100 * len(hits) / total if total else 0.0
    return Metric(value, total, len(hits), hits)


def _shots(events: list[Event]) -> list[Event]:
    return [e for e in events if e.type == "shot"]


def _xg(shot: Event) -> float:
    if shot.xg is None:
        raise ValueError(f"shot {shot.id} has no xg")
    return shot.xg


def _speed(shot: Event) -> float:
    if shot.shot_speed is None:
        raise ValueError(f"shot {shot.id} has no shot_speed")
    return shot.shot_speed


# ---- compute functions ---------------------------------------------------------------


def _count_of(event_type: str) -> Compute:
    def fn(evs: list[Event], _subject: Subject, _ctx: MetricContext) -> Metric:
        return _count([e for e in evs if e.type == event_type])

    return fn


def _xg_sum(evs: list[Event], _subject: Subject, _ctx: MetricContext) -> Metric:
    shots = _shots(evs)
    return Metric(sum(_xg(e) for e in shots), len(shots), len(shots), [e.id for e in shots])


def _npxg_sum(evs: list[Event], _subject: Subject, _ctx: MetricContext) -> Metric:
    # Non-penalty xG: every shot but penalty kicks. The simulator awards no penalties, so on a
    # synthetic match this equals xg_sum; StatsBomb marks penalties (core/statsbomb.py).
    shots = [e for e in _shots(evs) if not e.penalty]
    return Metric(sum(_xg(e) for e in shots), len(shots), len(shots), [e.id for e in shots])


# Shot outcomes as the sources label them (sim/generate.py, core/statsbomb.SHOT_OUTCOME). A goal is
# a shot on target.
SHOT_OUTCOMES: dict[str, frozenset[str]] = {
    "shots_on_target": frozenset({"on_target", "goal"}),
    "shots_off_target": frozenset({"off_target"}),
    "shots_blocked": frozenset({"blocked"}),
}


def _shot_outcome(metric: str) -> Compute:
    """The subject's shots with this outcome: value and k count them, n is all their shots."""
    wanted = SHOT_OUTCOMES[metric]

    def fn(evs: list[Event], _subject: Subject, _ctx: MetricContext) -> Metric:
        shots = _shots(evs)
        hits = [e.id for e in shots if e.outcome in wanted]
        return Metric(len(hits), len(shots), len(hits), hits)

    return fn


def _top_shot_speed(evs: list[Event], _subject: Subject, _ctx: MetricContext) -> Metric | None:
    # Only shots with a measured speed count; with none (open data has no speeds) there is no metric.
    timed = [e for e in _shots(evs) if e.shot_speed is not None]
    if not timed:
        return None
    best = max(timed, key=_speed)
    return Metric(_speed(best), len(timed), 1, [best.id])


def _xt_added(evs: list[Event], _subject: Subject, ctx: MetricContext) -> Metric:
    # Every completed pass and carry, backwards ones included, so the sum is net threat added.
    moves = [e for e in evs if completed_move(e)]
    value = sum(gain(e, ctx.xt, ctx.pitch) for e in moves)
    return Metric(value, len(moves), len(moves), [e.id for e in moves])


def _turnovers_own_third(evs: list[Event], _subject: Subject, ctx: MetricContext) -> Metric:
    own = ctx.pitch.own_third_x
    return _count([e for e in evs if e.type == "possession_change" and e.x < own])


def _pass_accuracy(evs: list[Event], _subject: Subject, _ctx: MetricContext) -> Metric:
    passes = [e for e in evs if e.type == "pass"]
    return _share([e.id for e in passes if e.outcome == "success"], len(passes))


def _attack_share(lane: str) -> Compute:
    def fn(evs: list[Event], subject: Subject, ctx: MetricContext) -> Metric:
        groups = attacks_by_lane(evs, subject.id, ctx.pitch)
        return _share(groups[lane], sum(len(v) for v in groups.values()))

    return fn


_DEFS: list[EventMetric] = [
    EventMetric("goals", "count", "exact", _count_of("goal")),
    EventMetric("shots", "count", "exact", _count_of("shot")),
    EventMetric("substitutions", "count", "exact", _count_of("substitution")),
    EventMetric("cards", "count", "exact", _count_of("card")),
    EventMetric("turnovers_own_third", "count", "exact", _turnovers_own_third),
    *(EventMetric(name, "count", "exact", _shot_outcome(name)) for name in SHOT_OUTCOMES),
    EventMetric("xg_sum", "sum", "tol_xg", _xg_sum),
    EventMetric("npxg_sum", "sum", "tol_xg", _npxg_sum),
    EventMetric("top_shot_speed", "max", "tol_speed", _top_shot_speed),
    EventMetric("xt_added", "sum", "tol_xt", _xt_added, frozenset({"team", "player"})),
    EventMetric("pass_accuracy", "share", "tol_share_pp", _pass_accuracy),
    *(
        EventMetric(
            f"attack_share_{lane}",
            "share",
            "tol_share_pp",
            _attack_share(lane),
            frozenset({"team"}),
        )
        for lane in LANES
    ),
]
_ALL: list[MetricDef] = [*_DEFS, CentralityRank(), FieldTilt()]
REGISTRY: dict[str, MetricDef] = {d.name: d for d in _ALL}


def compute_metric(
    metric: str, subject: Subject, window: Window, events: list[Event], ctx: MetricContext
) -> Metric | None:
    """Recompute a metric from the events in a window. None if not computable."""
    d = REGISTRY.get(metric)
    return None if d is None else d.compute(events, subject, window, ctx)


def require_metric(
    metric: str, subject: Subject, window: Window, events: list[Event], ctx: MetricContext
) -> Metric:
    """compute_metric for callers that only ask for metrics that are always computable."""
    m = compute_metric(metric, subject, window, events, ctx)
    if m is None:
        raise ValueError(f"{metric} is not computable for {subject} in {window}")
    return m
