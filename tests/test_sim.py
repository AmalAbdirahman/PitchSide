from __future__ import annotations

from core.config import load
from core.events import Match
from core.moments import minute
from core.shares import lane_shares
from sim.generate import Match as Simulation

S = load()


def test_same_seed_reproduces_committed_events(match: Match) -> None:
    committed = [e.model_dump(exclude_none=True) for e in match.events]
    assert Simulation(match.meta.seed, True, S).run() == committed


def test_event_count_and_monotonic_ids(match: Match) -> None:
    events = match.events
    lo, hi = S.data_card.hero_events
    assert lo <= len(events) <= hi
    assert [e.id for e in events] == list(range(1, len(events) + 1))
    assert all(a.t <= b.t for a, b in zip(events, events[1:], strict=False))


def test_planted_story(match: Match) -> None:
    s, events = S.sim, match.events
    subs = [e for e in events if e.type == "substitution" and e.team == "NOR"]
    assert [minute(e.t) for e in subs] == [58]
    assert [(minute(e.t), e.team) for e in events if e.type == "goal"] == [(63, "NOR")]
    after = [e for e in events if s.hero_sub_t <= e.t < s.hero_overload_until_t]
    assert lane_shares(after, "NOR", S.pitch)[0]["left"] > 0.5
    own = S.pitch.own_third_x
    losses = [e for e in events if e.type == "possession_change" and e.team == "HAR" and e.x < own]
    early = sum(e.t < s.hero_press_from_t for e in losses) / s.hero_press_from_t
    late = sum(e.t >= s.hero_press_from_t for e in losses) / (5400 - s.hero_press_from_t)
    assert late > early


def test_shots_carry_xg_and_speed(match: Match) -> None:
    shots = [e for e in match.events if e.type == "shot"]
    assert shots and all(0 < (e.xg or 0) < 1 and (e.shot_speed or 0) > 0 for e in shots)
    passes = [e for e in match.events if e.type == "pass"]
    assert all(
        e.pass_difficulty is not None and 0 <= e.pass_difficulty <= 1 and e.ball_speed is not None
        for e in passes
    )
