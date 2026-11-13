"""Attack share by lane.

Definitions (documented assumptions):
- An "attack" is a pass or carry that ends in the final third
  (end_x > final_third_x), or a shot. Attempts count whether or not they succeed.
- Lane comes from the end point (shot: its location). The pitch width is split
  into equal thirds. Coordinates are normalised so every team attacks left to
  right with y = 0 on its left touchline, so y < width / 3 is the left lane.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.config import Pitch
from core.events import Event

LANES = ("left", "centre", "right")


def lane_of(y: float, pitch: Pitch) -> str:
    """Lane name for a y coordinate."""
    third = pitch.width / 3
    if y < third:
        return "left"
    return "centre" if y < 2 * third else "right"


def is_attack(e: Event, pitch: Pitch) -> bool:
    """True if the event counts as an attack."""
    if e.type == "shot":
        return True
    if e.type in ("pass", "carry"):
        return (e.end_x or 0) > pitch.final_third_x
    return False


def attack_lane(e: Event, pitch: Pitch) -> str:
    """Lane of an attack event."""
    y = e.y if e.type == "shot" else e.end_y
    if y is None:
        raise ValueError(f"attack event {e.id} has no end_y")
    return lane_of(y, pitch)


def attacks_by_lane(events: Iterable[Event], team: str, pitch: Pitch) -> dict[str, list[int]]:
    """Event ids of a team's attacks grouped by lane."""
    out: dict[str, list[int]] = {lane: [] for lane in LANES}
    for e in events:
        if e.team == team and is_attack(e, pitch):
            out[attack_lane(e, pitch)].append(e.id)
    return out


def pooled_lane_shares(matches: Iterable[Iterable[Event]], pitch: Pitch) -> dict[str, float]:
    """Share of all attacks per lane over many matches, both teams pooled (each team's events
    attack left to right, so a lane means the same side for both)."""
    counts = {lane: 0 for lane in LANES}
    for events in matches:
        for e in events:
            if is_attack(e, pitch):
                counts[attack_lane(e, pitch)] += 1
    total = sum(counts.values())
    if total == 0:
        raise ValueError("no attacks to pool")
    return {lane: c / total for lane, c in counts.items()}


def attack_design_effect(
    matches: Iterable[Iterable[Event]], pitch: Pitch, shares: dict[str, float], window_s: float
) -> float:
    """How much more lane counts vary than independent attacks would: over every team's
    consecutive windows of window_s seconds in every match, sum (k - n p)^2 / sum n p (1 - p) for
    each lane (k of the window's n attacks, league share p). 1 means independent attacks; the
    hedge divides counts by it. Attacks in one possession often share a lane, so it is above 1.
    Never below 1: fewer effective attacks than counted is the only correction made."""
    num = den = 0.0
    for events in matches:
        evs = list(events)
        end = max((e.t for e in evs), default=0.0)
        for team in sorted({e.team for e in evs}):
            for start in range(0, int(end) + 1, int(window_s)):
                lanes = [
                    attack_lane(e, pitch)
                    for e in evs
                    if e.team == team and start <= e.t < start + window_s and is_attack(e, pitch)
                ]
                for lane, p in shares.items():
                    num += (lanes.count(lane) - len(lanes) * p) ** 2
                    den += len(lanes) * p * (1 - p)
    if den == 0:
        raise ValueError("no attacks to measure")
    return max(1.0, num / den)


def lane_shares(events: Iterable[Event], team: str, pitch: Pitch) -> tuple[dict[str, float], int]:
    """Share of attacks per lane (0..1) and the total number of attacks."""
    groups = attacks_by_lane(events, team, pitch)
    n = sum(len(ids) for ids in groups.values())
    if n == 0:
        return {lane: 0.0 for lane in LANES}, 0
    return {lane: len(ids) / n for lane, ids in groups.items()}, n
