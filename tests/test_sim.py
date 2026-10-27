from __future__ import annotations

from typing import Any

from core.config import load
from core.moments import minute
from core.shares import lane_shares
from sim.generate import Match


def test_same_seed_reproduces_committed_events(match: dict[str, Any]) -> None:
    assert Match(match["meta"]["seed"], hero=True).run() == match["events"]


def test_event_count_and_monotonic_ids(match: dict[str, Any]) -> None:
    events = match["events"]
    lo, hi = load()["data_card"]["hero_events"]
    assert lo <= len(events) <= hi
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))
    assert all(a["t"] <= b["t"] for a, b in zip(events, events[1:]))


def test_planted_story(match: dict[str, Any]) -> None:
    s, events = load()["sim"], match["events"]
    subs = [e for e in events if e["type"] == "substitution" and e["team"] == "NOR"]
    assert [minute(e["t"]) for e in subs] == [58]
    assert [(minute(e["t"]), e["team"]) for e in events if e["type"] == "goal"] == [(63, "NOR")]
    after = [e for e in events if s["hero_sub_t"] <= e["t"] < s["hero_overload_until_t"]]
    assert lane_shares(after, "NOR")[0]["left"] > 0.5
    own = load()["pitch"]["own_third_x"]
    losses = [e for e in events if e["type"] == "possession_change" and e["team"] == "HAR" and e["x"] < own]
    early = sum(e["t"] < s["hero_press_from_t"] for e in losses) / s["hero_press_from_t"]
    late = sum(e["t"] >= s["hero_press_from_t"] for e in losses) / (5400 - s["hero_press_from_t"])
    assert late > early


def test_shots_carry_xg_and_speed(match: dict[str, Any]) -> None:
    shots = [e for e in match["events"] if e["type"] == "shot"]
    assert shots and all(0 < e["xg"] < 1 and e["shot_speed"] > 0 for e in shots)
    passes = [e for e in match["events"] if e["type"] == "pass"]
    assert all(0 <= e["pass_difficulty"] <= 1 and "ball_speed" in e for e in passes)
