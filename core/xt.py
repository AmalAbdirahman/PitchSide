"""Expected threat (xT): a Markov model of moving the ball up the pitch (P8).

The pitch is cut into a grid (16 columns along its length, 12 rows across). For each cell, from
the actions of the training matches:
- shoot probability s = shots / (shots + moves), move probability m = moves / (shots + moves),
- score probability g = goals / shots,
- transition T[i][j] = completed moves from i that end in j / all moves from i (a failed move ends
  the chain, so a row of T sums to the completion rate, not to 1).
The threat of a cell solves xT = s * g + m * (T @ xT), found by value iteration from xT = 0.

A move is a pass (completed or not) or a carry; a shot is any shot but a penalty. Coordinates are
metres on our pitch, the acting team attacking towards x = length. xT ignores defenders and
everything off the ball, and its values depend on the training data. Method and validation:
docs/methods/xt.md. Fitted by scripts/fit_xt.py; the surface lives in config.yaml.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from core.config import Pitch, Xt
from core.events import Event


@dataclass(frozen=True)
class Grid:
    cols: int  # along the pitch length (x)
    rows: int  # across the pitch width (y)
    length: float
    width: float

    @classmethod
    def of(cls, xt: Xt, pitch: Pitch) -> Grid:
        return cls(xt.cols, xt.rows, pitch.length, pitch.width)

    @property
    def n(self) -> int:
        return self.cols * self.rows

    def cell(self, x: float, y: float) -> int:
        """Cell index (row-major, row 0 at y = 0); points on or past an edge clamp inside."""
        c = min(max(int(x / self.length * self.cols), 0), self.cols - 1)
        r = min(max(int(y / self.width * self.rows), 0), self.rows - 1)
        return r * self.cols + c


@dataclass(frozen=True)
class Action:
    """One training action. For a move `success` means completed; for a shot, a goal."""

    kind: Literal["move", "shot"]
    x: float
    y: float
    success: bool
    end_x: float | None = None
    end_y: float | None = None


@dataclass
class Counts:
    shots: list[int]
    goals: list[int]
    moves: list[int]
    completed: list[dict[int, int]] = field(default_factory=list)  # from cell -> {to cell: n}


def count(actions: Iterable[Action], grid: Grid) -> Counts:
    c = Counts([0] * grid.n, [0] * grid.n, [0] * grid.n, [{} for _ in range(grid.n)])
    for a in actions:
        i = grid.cell(a.x, a.y)
        if a.kind == "shot":
            c.shots[i] += 1
            c.goals[i] += a.success
            continue
        c.moves[i] += 1
        if a.success and a.end_x is not None and a.end_y is not None:
            j = grid.cell(a.end_x, a.end_y)
            c.completed[i][j] = c.completed[i].get(j, 0) + 1
    return c


@dataclass(frozen=True)
class Probabilities:
    shoot: list[float]
    move: list[float]
    score: list[float]
    trans: list[dict[int, float]]


def probabilities(c: Counts) -> Probabilities:
    """Per-cell probabilities; a cell with no actions (or no shots) gets zero."""
    shoot, move, score, trans = [], [], [], []
    for i in range(len(c.shots)):
        total = c.shots[i] + c.moves[i]
        shoot.append(c.shots[i] / total if total else 0.0)
        move.append(c.moves[i] / total if total else 0.0)
        score.append(c.goals[i] / c.shots[i] if c.shots[i] else 0.0)
        trans.append({j: k / c.moves[i] for j, k in c.completed[i].items()} if c.moves[i] else {})
    return Probabilities(shoot, move, score, trans)


@dataclass(frozen=True)
class Solution:
    values: list[float]
    deltas: list[float]  # largest change in any cell, per iteration


def solve(p: Probabilities, max_iter: int, tol: float) -> Solution:
    """Value iteration from zero until the largest change is below tol."""
    values, deltas = [0.0] * len(p.shoot), []
    for _ in range(max_iter):
        new = [
            p.shoot[i] * p.score[i] + p.move[i] * sum(t * values[j] for j, t in p.trans[i].items())
            for i in range(len(values))
        ]
        deltas.append(max(abs(a - b) for a, b in zip(new, values, strict=True)))
        values = new
        if deltas[-1] < tol:
            return Solution(values, deltas)
    raise ValueError(f"xT did not converge in {max_iter} iterations (last change {deltas[-1]})")


def value_at(x: float, y: float, xt: Xt, pitch: Pitch) -> float:
    """Threat of the cell holding (x, y), from the fitted surface in config."""
    cell = Grid.of(xt, pitch).cell(x, y)
    return xt.surface[cell // xt.cols][cell % xt.cols]


def completed_move(e: Event) -> bool:
    """A pass or carry that reached its end point: the actions xT added counts."""
    return (
        e.type in ("pass", "carry")
        and e.outcome == "success"
        and e.end_x is not None
        and e.end_y is not None
    )


def gain(e: Event, xt: Xt, pitch: Pitch) -> float:
    """xT at the end of a completed move minus xT at its start (negative for a move backwards)."""
    if e.end_x is None or e.end_y is None:
        raise ValueError(f"event {e.id} has no end point")
    return value_at(e.end_x, e.end_y, xt, pitch) - value_at(e.x, e.y, xt, pitch)
