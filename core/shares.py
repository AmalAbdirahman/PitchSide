"""Attack share by lane.

Definitions (documented assumptions):
- An "attack" is a pass or carry that ends in the final third
  (end_x > final_third_x), or a shot. Attempts count whether or not they succeed.
- Lane comes from the end point (shot: its location). The pitch width is split
  into equal thirds. Coordinates are normalised so every team attacks left to
  right with y = 0 on its left touchline, so y < width / 3 is the left lane.
"""
from __future__ import annotations

from typing import Any, Iterable

from core.config import load

LANES = ("left", "centre", "right")
Event = dict[str, Any]


def lane_of(y: float) -> str:
    """Lane name for a y coordinate."""
    third = load()["pitch"]["width"] / 3
    if y < third:
        return "left"
    return "centre" if y < 2 * third else "right"


def is_attack(e: Event) -> bool:
    """True if the event counts as an attack."""
    if e["type"] == "shot":
        return True
    if e["type"] in ("pass", "carry"):
        return e.get("end_x", 0) > load()["pitch"]["final_third_x"]
    return False


def attack_lane(e: Event) -> str:
    """Lane of an attack event."""
    y = e["y"] if e["type"] == "shot" else e["end_y"]
    return lane_of(y)


def attacks_by_lane(events: Iterable[Event], team: str) -> dict[str, list[int]]:
    """Event ids of a team's attacks grouped by lane."""
    out: dict[str, list[int]] = {lane: [] for lane in LANES}
    for e in events:
        if e["team"] == team and is_attack(e):
            out[attack_lane(e)].append(e["id"])
    return out


def lane_shares(events: Iterable[Event], team: str) -> tuple[dict[str, float], int]:
    """Share of attacks per lane (0..1) and the total number of attacks."""
    groups = attacks_by_lane(events, team)
    n = sum(len(ids) for ids in groups.values())
    if n == 0:
        return {lane: 0.0 for lane in LANES}, 0
    return {lane: len(ids) / n for lane, ids in groups.items()}, n
