"""Expected goals from shot location.

Assumptions (stated, not fitted to real data):
- Logistic model on two features only: distance to goal centre (metres)
  and the angle the goal mouth subtends at the shot location (radians).
- Coefficients live in config.yaml and were set by hand so a central shot
  from 6 m is about 0.40, from 11 m about 0.20, from 20 m about 0.07.
- No body part, assist type, defender or keeper position. Penalties are
  not modelled separately.
- Coordinates: metres on a 105 x 68 pitch, the shooting team attacks
  left to right, so the goal is at x = 105, y = 34.
"""
from __future__ import annotations

import math

from core.config import load


def goal_angle(x: float, y: float) -> float:
    """Angle in radians subtended by the goal mouth at (x, y)."""
    cfg = load()
    half_w = cfg["xg"]["goal_width"] / 2
    gx, gy = cfg["pitch"]["length"], cfg["pitch"]["width"] / 2
    a1 = math.atan2(gy - half_w - y, gx - x)
    a2 = math.atan2(gy + half_w - y, gx - x)
    return abs(a2 - a1)


def goal_distance(x: float, y: float) -> float:
    """Distance in metres from (x, y) to the centre of the goal."""
    cfg = load()
    return math.hypot(cfg["pitch"]["length"] - x, cfg["pitch"]["width"] / 2 - y)


def xg(x: float, y: float) -> float:
    """Probability that a shot from (x, y) is a goal."""
    c = load()["xg"]
    logit = c["intercept"] + c["dist"] * goal_distance(x, y) + c["angle"] * goal_angle(x, y)
    return 1 / (1 + math.exp(-logit))
