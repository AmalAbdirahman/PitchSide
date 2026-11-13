"""Fit the xG model on StatsBomb Open Data shots and validate it on held-out matches.

Run: python scripts/fetch_statsbomb.py --xg   (once; downloads into data/external/, git-ignored)
     python scripts/fit_xg.py                 (writes data/xg_fit.json; prints the config block)

Method (docs/methods/xg.md): whole matches are split into train and test. On the train matches,
non-penalty shots fit a logistic regression on core/xg.py's FEATURES; penalties get their own
conversion rate. On the test matches the model is scored against StatsBomb's own xG and a
constant (the train goal rate per shot) with log loss, Brier score and a reliability table.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT, Settings, load  # noqa: E402
from core.statsbomb import RAW_DIR, Scale, ShotRow, read_shots  # noqa: E402
from core.xg import FEATURES, design_row  # noqa: E402
from core.xg_fit import (  # noqa: E402
    brier,
    fit_logistic,
    log_loss,
    reliability,
    sigmoid,
    split_by_match,
)

OUT = ROOT / "data" / "xg_fit.json"


def load_shots(s: Settings) -> list[ShotRow]:
    scale, rows = Scale.of(s.statsbomb, s.pitch), []
    for comp, season in s.xg_fit.seasons:
        path = RAW_DIR / "matches" / str(comp) / f"{season}.json"
        for m in json.loads(path.read_text(encoding="utf-8")):
            rows += read_shots(RAW_DIR, m["match_id"], scale)
    return rows


def commit() -> str:
    """Short hash of HEAD, marked dirty if the work tree has changes; 'unknown' without git."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        )
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    except FileNotFoundError:  # git not installed: record that rather than fail the fit
        return "unknown"
    return head.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def predict(row: ShotRow, coef: list[float], penalty: float, s: Settings) -> float:
    if row.features.penalty:
        return penalty
    return sigmoid(
        sum(
            b * v
            for b, v in zip(coef, design_row(row.features, s.pitch, s.xg.goal_width), strict=True)
        )
    )


def scores(p: list[float], y: list[int], bins: int) -> dict[str, Any]:
    return {
        "log_loss": round(log_loss(p, y), 4),
        "brier": round(brier(p, y), 4),
        "reliability": [
            {"n": b.n, "mean_pred": round(b.mean_pred, 4), "observed": round(b.observed, 4)}
            for b in reliability(p, y, bins)
        ],
    }


def fit(s: Settings) -> dict[str, Any]:
    f = s.xg_fit
    shots = load_shots(s)
    train_ids, test_ids = split_by_match([r.match_id for r in shots], f.test_fraction, f.split_seed)
    train = [r for r in shots if r.match_id in train_ids]
    test = [r for r in shots if r.match_id in test_ids]
    open_play = [r for r in train if not r.features.penalty]
    model = fit_logistic(
        [design_row(r.features, s.pitch, s.xg.goal_width) for r in open_play],
        [int(r.goal) for r in open_play],
        f.max_iter,
        f.ridge,
    )
    pens = [r for r in train if r.features.penalty]
    penalty = sum(r.goal for r in pens) / len(pens)
    base = sum(r.goal for r in train) / len(train)
    y = [int(r.goal) for r in test]
    ours = [predict(r, model.coef, penalty, s) for r in test]
    statsbomb = [r.statsbomb_xg if r.statsbomb_xg is not None else base for r in test]
    return {
        "data": "StatsBomb Open Data",
        "seasons": [list(x) for x in f.seasons],
        "run_date": datetime.date.today().isoformat(),
        "commit": commit(),
        "matches": {"train": len(train_ids), "test": len(test_ids)},
        "shots": {"train": len(train), "train_open_play": len(open_play), "test": len(test)},
        "goals": {"train": sum(r.goal for r in train), "test": sum(y)},
        "iterations": model.iterations,
        "coefficients": {
            n: {"value": round(c, 4), "se": round(e, 4)}
            for n, c, e in zip(FEATURES, model.coef, model.se, strict=True)
        },
        "penalty": {"rate": round(penalty, 4), "n": len(pens)},
        "test_missing_statsbomb_xg": sum(r.statsbomb_xg is None for r in test),
        "test": {
            "ours": scores(ours, y, f.reliability_bins),
            "statsbomb": scores(statsbomb, y, f.reliability_bins),
            "constant": scores([base] * len(y), y, f.reliability_bins) | {"rate": round(base, 4)},
        },
    }


def main() -> None:
    argparse.ArgumentParser(description="Fit and validate the xG model.").parse_args()
    result = fit(load())
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "test"}, indent=1))
    for name, sc in result["test"].items():
        print(f"{name:10s} log loss {sc['log_loss']:.4f}  Brier {sc['brier']:.4f}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
