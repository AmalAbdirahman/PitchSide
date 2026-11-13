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
from dataclasses import dataclass

from core.config import Pitch, Xg


def goal_angle(x: float, y: float, pitch: Pitch, goal_width: float) -> float:
    """Angle in radians subtended by the goal mouth at (x, y)."""
    half_w = goal_width / 2
    gx, gy = pitch.length, pitch.width / 2
    a1 = math.atan2(gy - half_w - y, gx - x)
    a2 = math.atan2(gy + half_w - y, gx - x)
    return abs(a2 - a1)


def goal_distance(x: float, y: float, pitch: Pitch) -> float:
    """Distance in metres from (x, y) to the centre of the goal."""
    return math.hypot(pitch.length - x, pitch.width / 2 - y)


def xg(x: float, y: float, pitch: Pitch, coef: Xg) -> float:
    """Probability that a shot from (x, y) is a goal."""
    logit = (
        coef.intercept
        + coef.dist * goal_distance(x, y, pitch)
        + coef.angle * goal_angle(x, y, pitch, coef.goal_width)
    )
    return 1 / (1 + math.exp(-logit))


# ---- features for the fitted model (P7) ------------------------------------------------

# Order of the design row and of the fitted coefficients.
FEATURES = ("intercept", "dist", "angle", "header", "other_body", "free_kick", "under_pressure")


@dataclass(frozen=True)
class ShotFeatures:
    """What the model knows about a shot. Defaults describe an open-play shot with the foot,
    not under pressure, which is all the simulator can say about its shots."""

    x: float
    y: float
    header: bool = False
    other_body: bool = False  # neither foot nor head
    free_kick: bool = False  # a direct free kick
    under_pressure: bool = False
    penalty: bool = False  # modelled apart from the logistic part


def design_row(s: ShotFeatures, pitch: Pitch, goal_width: float) -> list[float]:
    """The shot as numbers, in FEATURES order."""
    return [
        1.0,
        goal_distance(s.x, s.y, pitch),
        goal_angle(s.x, s.y, pitch, goal_width),
        float(s.header),
        float(s.other_body),
        float(s.free_kick),
        float(s.under_pressure),
    ]
