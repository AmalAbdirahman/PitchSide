"""P10 study: is the win-probability model calibrated, and does a fitted variant do better?

Run: python scripts/fetch_statsbomb.py --xg   (once; real matches with lineups)
     python scripts/eval_winprob.py           (writes data/winprob_eval.json; no network, a few minutes)

Game states are taken every winprob_eval.every_min official minutes before 90 (core/winprob_eval)
and scored against the result at the end of regulation. Two datasets, each with train and test
matches: simulated matches (sim/generate.py, seeds from config) and the StatsBomb Open Data
seasons of the xG fit with its match split. Models, all scored on the test matches only:
- constant: the training matches' share of home wins, draws and away wins, at every state;
- poisson_v1: the product model (config winprob);
- mu_only: poisson_v1 that all but ignores in-game xG (k = 100000), the score-and-clock baseline;
- poisson_v2: match length = mean length of regulation in the training matches, and k and rho
  picked from config grids by training Brier score.
"""

from __future__ import annotations

import datetime
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT, Settings, load  # noqa: E402
from core.events import Clock, Event, Match  # noqa: E402
from core.sources import SyntheticSource  # noqa: E402
from core.statsbomb import RAW_DIR, StatsBombOpenSource, xg_rate  # noqa: E402
from core.winprob import PoissonParams  # noqa: E402
from core.winprob_eval import (  # noqa: E402
    Predictor,
    State,
    bootstrap_diff,
    brier,
    calibration,
    fit_v2,
    log_loss,
    match_states,
    mean_score,
    outcome_rates,
    regulation_end,
    states,
    v2_predictor,
)
from core.xg_fit import split_by_match  # noqa: E402
from scripts.fit_xg import commit  # noqa: E402
from sim.generate import Match as SimMatch  # noqa: E402

OUT = ROOT / "data" / "winprob_eval.json"
NO_XG_K = 100000.0


@dataclass(frozen=True)
class Data:
    """Train and test states for one dataset, the league rate mu and the training match length."""

    label: str
    train: list[State]
    test: list[State]
    mu: float
    length: float  # minutes


def minutes(s: Settings) -> list[int]:
    return list(range(0, 90, s.winprob_eval.every_min))


def synthetic(s: Settings) -> Data:
    e = s.winprob_eval
    clock = Clock(period_starts=[0, s.sim.half_s], end_t=2 * s.sim.half_s)

    def run(first: int, n: int) -> list[State]:
        out: list[State] = []
        for seed in range(first, first + n):
            events = [Event.model_validate(x) for x in SimMatch(seed, False, s).run()]
            out += states(events, ("NOR", "HAR"), clock, f"sim_{seed}", minutes(s))
        return out

    train = run(e.sim_seed, e.sim_train_matches)
    test = run(e.sim_seed + e.sim_train_matches, e.sim_test_matches)
    mu = SyntheticSource().load()[1].mu
    label = f"synthetic: {e.sim_train_matches} train, {e.sim_test_matches} test simulated matches"
    return Data(label, train, test, mu, 2 * s.sim.half_s / 60)


def real_matches(s: Settings) -> dict[int, Match]:
    out: dict[int, Match] = {}
    for comp, season in s.xg_fit.seasons:
        sb = s.statsbomb.model_copy(update={"competition_id": comp, "season_id": season})
        src = StatsBombOpenSource(sb, s.pitch, s.xg, sb.match_id, s.moments.overload_window_s)
        rows = json.loads((RAW_DIR / "matches" / str(comp) / f"{season}.json").read_text("utf-8"))
        out |= {r["match_id"]: src.match(r["match_id"]) for r in rows}
    return out


def statsbomb(s: Settings) -> Data:
    """mu and the match length come from the training matches only."""
    matches = real_matches(s)
    f = s.xg_fit
    train_ids, test_ids = split_by_match(list(matches), f.test_fraction, f.split_seed)
    totals = [xg_rate(matches[i]) for i in train_ids]
    mu = sum(x for x, _ in totals) / sum(n for _, n in totals)
    length = statistics.mean(regulation_end(matches[i].meta.clock) / 60 for i in train_ids)
    train = [r for i in sorted(train_ids) for r in match_states(matches[i], minutes(s))]
    test = [r for i in sorted(test_ids) for r in match_states(matches[i], minutes(s))]
    label = f"StatsBomb Open Data: {len(train_ids)} train, {len(test_ids)} test matches"
    return Data(label, train, test, mu, length)


def models(d: Data, s: Settings) -> tuple[dict[str, Predictor], PoissonParams]:
    e, v1 = s.winprob_eval, PoissonParams.v1(s.winprob)
    const = outcome_rates(d.train)
    v2 = fit_v2(d.train, d.mu, d.length, e.k_grid, e.rho_grid, v1.goal_cap)
    no_xg = PoissonParams(NO_XG_K, v1.length, 0.0, v1.goal_cap)
    preds: dict[str, Predictor] = {
        "constant": lambda r: const,
        "poisson_v1": v2_predictor(d.mu, v1),
        "mu_only": v2_predictor(d.mu, no_xg),
        "poisson_v2": v2_predictor(d.mu, v2),
    }
    return preds, v2


def evaluate(d: Data, s: Settings) -> dict[str, Any]:
    e = s.winprob_eval
    preds, v2 = models(d, s)

    def ci(a: str, b: str) -> list[float]:
        lo, hi = bootstrap_diff(d.test, preds[a], preds[b], e.bootstrap, e.bootstrap_seed)
        return [round(lo, 4), round(hi, 4)]

    return {
        "data": d.label,
        "test_states": len(d.test),
        "mu": round(d.mu, 6),
        "constant": [round(x, 4) for x in outcome_rates(d.train)],
        "poisson_v2": {"k": v2.k, "length": round(v2.length, 2), "rho": v2.rho},
        "test_scores": {
            name: {
                "brier": round(mean_score(d.test, p, brier), 4),
                "log_loss": round(mean_score(d.test, p, log_loss), 4),
            }
            for name, p in preds.items()
        },
        "brier_diff_95": {
            "v1_minus_constant": ci("poisson_v1", "constant"),
            "v2_minus_v1": ci("poisson_v2", "poisson_v1"),
            "v2_minus_mu_only": ci("poisson_v2", "mu_only"),
        },
        "calibration": {
            name: [asdict(b) for b in calibration(d.test, preds[name], e.phases)]
            for name in ("poisson_v1", "poisson_v2")
        },
    }


def main() -> None:
    s = load()
    record = {
        "run_date": datetime.date.today().isoformat(),
        "commit": commit(),
        "every_min": s.winprob_eval.every_min,
        "synthetic": evaluate(synthetic(s), s),
        "statsbomb": evaluate(statsbomb(s), s),
    }
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for key in ("synthetic", "statsbomb"):
        r = record[key]
        print(r["data"], r["poisson_v2"])
        for name, sc in r["test_scores"].items():
            print(f"  {name:<11} Brier {sc['brier']:.4f}  log loss {sc['log_loss']:.4f}")
        print("  Brier differences, 95% match bootstrap:", r["brier_diff_95"])
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
