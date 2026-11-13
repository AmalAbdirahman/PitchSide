"""Expected goals: a logistic model fitted on StatsBomb Open Data shots (P7).

- Features (FEATURES): distance to the goal centre (m), the angle the goal mouth subtends (rad),
  header, other body part (not foot or head), direct free kick, under pressure.
- Penalties are not in the logistic part: they get the penalty conversion rate of the training set.
- Coefficients live in config.yaml with their provenance; scripts/fit_xg.py fits them and writes
  data/xg_fit.json. Method, validation and limits: docs/methods/xg.md.
- No keeper or defender positions, no assist type, no shot height: StatsBomb's own xG knows more.
- Coordinates: metres on a 105 x 68 pitch, the shooting team attacks left to right, so the goal is
  at x = 105, y = 34. The simulator's shots are open-play shots with the foot, not under pressure.
- Real StatsBomb shots use StatsBomb's xG unless config says statsbomb.shot_xg: ours.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.config import Pitch, Xg

# Order of the design row and of the fitted coefficients (names match the Xg config fields).
FEATURES = ("intercept", "dist", "angle", "header", "other_body", "free_kick", "under_pressure")


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


def xg_of(s: ShotFeatures, pitch: Pitch, coef: Xg) -> float:
    """Probability that the shot is a goal."""
    if s.penalty:
        return coef.penalty
    row = design_row(s, pitch, coef.goal_width)
    logit = sum(getattr(coef, name) * v for name, v in zip(FEATURES, row, strict=True))
    return 1 / (1 + math.exp(-logit))


def xg(x: float, y: float, pitch: Pitch, coef: Xg) -> float:
    """xG of an open-play shot with the foot from (x, y), not under pressure."""
    return xg_of(ShotFeatures(x, y), pitch, coef)
