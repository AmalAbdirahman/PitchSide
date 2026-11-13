"""xT model (core/xt.py): grid, counts, probabilities and value iteration on hand examples."""

from __future__ import annotations

import json

import pytest

from core.config import ROOT, MetricContext, Pitch, Xt, load
from core.events import Event
from core.facts import Subject, Window
from core.metrics import REGISTRY, compute_metric
from core.moments import _xt_leader
from core.xt import Action, Grid, count, gain, probabilities, solve, value_at

S = load()
G2 = Grid(cols=2, rows=1, length=100, width=50)  # cell 0: x < 50, cell 1: x >= 50


def test_cells_are_row_major_and_clamp() -> None:
    g = Grid(cols=16, rows=12, length=105, width=68)
    assert g.n == 192
    assert g.cell(0, 0) == 0
    assert g.cell(104.9, 0) == 15
    assert g.cell(0, 67.9) == 11 * 16
    assert g.cell(105, 68) == 191, "a point on the far edge belongs to the last cell"
    assert g.cell(-3, -1) == 0


def _moves(n: int, done: int, frm: float, to: float) -> list[Action]:
    return [Action("move", frm, 25, i < done, to, 25) for i in range(n)]


def _shots(n: int, goals: int, x: float) -> list[Action]:
    return [Action("shot", x, 25, i < goals) for i in range(n)]


def test_counts() -> None:
    c = count(_moves(4, 3, 10, 70) + _shots(10, 5, 80), G2)
    assert (c.shots, c.goals, c.moves) == ([0, 10], [0, 5], [4, 0])
    assert c.completed == [{1: 3}, {}]


def test_probabilities_include_failed_moves_in_the_denominator() -> None:
    p = probabilities(count(_moves(4, 3, 10, 70) + _shots(10, 5, 80), G2))
    assert (p.shoot, p.move, p.score) == ([0.0, 1.0], [1.0, 0.0], [0.0, 0.5])
    assert p.trans == [{1: 0.75}, {}]


def test_one_way_chain() -> None:
    # Cell 1 only shoots (xT = 0.5); cell 0 only moves to cell 1, 3 of 4 completed: 0.75 * 0.5.
    sol = solve(probabilities(count(_moves(4, 3, 10, 70) + _shots(10, 5, 80), G2)), 100, 1e-12)
    assert sol.values == pytest.approx([0.375, 0.5])


def test_coupled_cells_match_the_closed_form() -> None:
    # Cell 1: 10 shots (5 goals) and 10 completed moves back to cell 0, so
    # x1 = 0.5 * 0.5 + 0.5 * x0; cell 0 as above, x0 = 0.75 * x1. Solved: x1 = 0.4, x0 = 0.3.
    acts = _moves(4, 3, 10, 70) + _shots(10, 5, 80) + _moves(10, 10, 80, 10)
    sol = solve(probabilities(count(acts, G2)), 200, 1e-12)
    assert sol.values == pytest.approx([0.3, 0.4])
    assert sol.deltas[-1] < 1e-12
    assert all(b <= a + 1e-15 for a, b in zip(sol.deltas[1:], sol.deltas[2:], strict=False))


def test_no_convergence_raises() -> None:
    acts = _moves(4, 3, 10, 70) + _shots(10, 5, 80) + _moves(10, 10, 80, 10)
    with pytest.raises(ValueError, match="did not converge"):
        solve(probabilities(count(acts, G2)), 3, 1e-12)


def test_empty_cells_have_no_threat() -> None:
    sol = solve(probabilities(count(_shots(4, 1, 80), G2)), 10, 1e-12)
    assert sol.values == [0.0, 0.25]


def test_value_at_reads_the_surface() -> None:
    pitch = Pitch(length=100, width=50, own_third_x=33, final_third_x=67)
    xt = Xt(cols=2, rows=2, surface=((0.1, 0.2), (0.3, 0.4)))
    assert value_at(10, 10, xt, pitch) == 0.1
    assert value_at(90, 10, xt, pitch) == 0.2
    assert value_at(90, 40, xt, pitch) == 0.4


def test_surface_shape_is_checked() -> None:
    with pytest.raises(ValueError, match="2 rows of 2"):
        Xt(cols=2, rows=2, surface=((0.1, 0.2),))


def test_config_surface_is_the_fitted_one() -> None:
    record = json.loads((ROOT / "data" / "xt_fit.json").read_text(encoding="utf-8"))
    assert [list(r) for r in S.xt.surface] == record["surface"]
    assert (S.xt.cols, S.xt.rows) == (record["cols"], record["rows"])


def test_fitted_surface_rises_towards_goal() -> None:
    mid = S.xt.rows // 2
    row = S.xt.surface[mid]
    assert row[-1] > row[S.xt.cols // 2] > row[0]
    assert max(max(r) for r in S.xt.surface) == max(row[-2:])


# ---- xt_added metric and the xT moment (P8) ------------------------------------------------

PITCH2 = Pitch(length=100, width=50, own_third_x=33, final_third_x=67)
CTX2 = MetricContext(PITCH2, Xt(cols=2, rows=1, surface=((0.1, 0.5),)))
HALF = Window(start_t=0.0, end_t=100.0)


def _ev(i: int, kind: str, pid: str, x: float, ex: float, outcome: str = "success") -> Event:
    return Event(
        id=i, t=float(i), period=1, type=kind, team=pid[:3], player_id=pid,
        x=x, y=25, end_x=ex, end_y=25, outcome=outcome,
    )  # fmt: skip


EVENTS = [
    _ev(1, "pass", "NOR_01", 10, 70),  # +0.4
    _ev(2, "pass", "NOR_01", 10, 70, "fail"),  # not completed: ignored
    _ev(3, "carry", "NOR_02", 70, 10),  # -0.4, a move backwards counts
    _ev(4, "carry", "NOR_02", 10, 90),  # +0.4
    _ev(5, "carry", "NOR_02", 60, 80),  # 0.0 within one cell
    _ev(6, "shot", "NOR_02", 80, 100),  # not a move
    _ev(7, "pass", "HAR_01", 20, 40),  # other team, within one cell
]


def test_xt_added_sums_gains_of_completed_moves() -> None:
    nor = compute_metric("xt_added", Subject(type="team", id="NOR"), HALF, EVENTS, CTX2)
    assert nor is not None
    assert nor.value == pytest.approx(0.4)
    assert (nor.n, nor.ids) == (4, [1, 3, 4, 5])
    p2 = compute_metric("xt_added", Subject(type="player", id="NOR_02"), HALF, EVENTS, CTX2)
    assert p2 is not None and p2.value == pytest.approx(0.0)


def test_xt_added_is_for_teams_and_players_only() -> None:
    match = Subject(type="match", id="match")
    assert compute_metric("xt_added", match, HALF, EVENTS, CTX2) is None
    d = REGISTRY["xt_added"]
    assert (d.kind, d.tolerance(S.verifier), d.checks_sample) == ("sum", S.verifier.tol_xt, False)


def test_gain_needs_an_end_point() -> None:
    e = Event(id=9, t=1.0, period=1, type="pass", team="NOR", player_id="NOR_01", x=1, y=1)
    with pytest.raises(ValueError, match="no end point"):
        gain(e, CTX2.xt, PITCH2)


def test_xt_moment_names_the_player_with_the_most_threat_added() -> None:
    (m,) = _xt_leader(EVENTS, HALF, "first", CTX2, 0.1)
    assert (m["rule"], m["metric"], m["subject"]["id"], m["players"]) == (
        "xt_leader", "xt_added", "NOR_01", ["NOR_01"],
    )  # fmt: skip
    assert m["value"] == pytest.approx(0.4)
    assert m["evidence_ids"] == [1]


def test_no_xt_moment_below_the_threshold() -> None:
    assert _xt_leader(EVENTS, HALF, "first", CTX2, 0.5) == []
    assert _xt_leader([], HALF, "first", CTX2, 0.0) == []
