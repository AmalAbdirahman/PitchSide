"""Fit the expected threat (xT) surface on StatsBomb Open Data and validate it on held-out matches.

Run: python scripts/fetch_statsbomb.py --xg   (once; the same seasons as the xG fit)
     python scripts/fit_xt.py                 (writes data/xt_fit.json)

Method (docs/methods/xt.md): whole matches are split into train and test exactly as for xG. The
train matches' passes, carries and non-penalty shots give per-cell probabilities, and value
iteration solves for the surface. On the test matches, each action's start cell value is scored
against what happened next: did the acting team score later in the same possession?
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT, Settings, load  # noqa: E402
from core.statsbomb import RAW_DIR, Scale, XtRow, read_events, xt_rows  # noqa: E402
from core.xg_fit import split_by_match  # noqa: E402
from core.xt import Grid, count, probabilities, solve  # noqa: E402
from scripts.fit_xg import commit, scores  # noqa: E402

OUT = ROOT / "data" / "xt_fit.json"


def load_rows(s: Settings) -> list[XtRow]:
    scale, rows = Scale.of(s.statsbomb, s.pitch), []
    for comp, season in s.xg_fit.seasons:
        path = RAW_DIR / "matches" / str(comp) / f"{season}.json"
        for m in json.loads(path.read_text(encoding="utf-8")):
            rows += xt_rows(read_events(RAW_DIR, m["match_id"]), m["match_id"], scale)
    return rows


def scored_later(rows: list[XtRow]) -> list[int]:
    """1 if the acting team scores later in the same possession (this action included)."""
    seen: set[tuple[int, int, int]] = set()
    out = []
    for r in reversed(rows):
        key = (r.match_id, r.possession, r.team_id)
        if r.action.kind == "shot" and r.action.success:
            seen.add(key)
        out.append(int(key in seen))
    return out[::-1]


def fit(s: Settings) -> dict[str, Any]:
    f, grid = s.xg_fit, Grid.of(s.xt, s.pitch)
    rows = load_rows(s)
    train_ids, test_ids = split_by_match([r.match_id for r in rows], f.test_fraction, f.split_seed)
    train = [r for r in rows if r.match_id in train_ids]
    test = [r for r in rows if r.match_id in test_ids]
    c = count((r.action for r in train), grid)
    sol = solve(probabilities(c), s.xt_fit.max_iter, s.xt_fit.tol)
    surface = [
        [round(v, 5) for v in sol.values[r * grid.cols : (r + 1) * grid.cols]]
        for r in range(grid.rows)
    ]
    y_train, y = scored_later(train), scored_later(test)
    base = sum(y_train) / len(y_train)
    pred = [
        surface[(i := grid.cell(r.action.x, r.action.y)) // grid.cols][i % grid.cols] for r in test
    ]
    return {
        "data": "StatsBomb Open Data",
        "seasons": [list(x) for x in f.seasons],
        "run_date": datetime.date.today().isoformat(),
        "commit": commit(),
        "matches": {"train": len(train_ids), "test": len(test_ids)},
        "actions": {
            "train_moves": sum(c.moves),
            "train_completed": sum(sum(d.values()) for d in c.completed),
            "train_shots": sum(c.shots),
            "train_goals": sum(c.goals),
            "test": len(test),
            "test_scored_later": sum(y),
        },
        "cols": grid.cols,
        "rows": grid.rows,
        "tol": s.xt_fit.tol,
        "iterations": len(sol.deltas),
        "max_change": [float(f"{d:.3g}") for d in sol.deltas],
        "surface": surface,
        "test": {
            "xt": scores(pred, y, f.reliability_bins),
            # Every constant prediction ties, so reliability bins would only sort by outcome.
            "constant": {
                k: v for k, v in scores([base] * len(y), y, 1).items() if k != "reliability"
            }
            | {"rate": round(base, 4)},
        },
    }


def main() -> None:
    argparse.ArgumentParser(description="Fit and validate the xT surface.").parse_args()
    result = fit(load())
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("test", "surface")}, indent=1))
    for name, sc in result["test"].items():
        print(f"{name:10s} log loss {sc['log_loss']:.4f}  Brier {sc['brier']:.4f}")
    print(f"wrote {OUT}; python scripts/plot_xt.py draws the figures")


if __name__ == "__main__":
    main()
