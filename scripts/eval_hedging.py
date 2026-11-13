"""P9 study: how often does each hedge rule call a lane overload "clear" when there is none?

Run: python scripts/eval_hedging.py      (writes data/hedging_eval.json; no network, about a minute)

The moment detector (core/moments.overload_moments) looks again every check_every_s seconds, so a
rule that is right 95% of the time on one look can still announce a false overload in many matches.
This script simulates matches and runs the detector exactly as the build does; every overload it
raises is then judged by each rule:
- wilson: the product before P9, a 95% Wilson interval for the top lane against a 1/3 baseline;
- each Beta-Binomial candidate in config hedge_eval.candidates, against the league lane share.
Null matches have no planted overload, so every "clear" call is a false alarm. Planted matches run
the hero script, where the home side overloads the left lane after its substitution; there we
measure whether and how soon each rule calls that overload clear.
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT, Hedge, Settings, load  # noqa: E402
from core.events import Clock, Event  # noqa: E402
from core.hedge import beta_hedge  # noqa: E402
from core.moments import overload_moments  # noqa: E402
from core.sources import League, SyntheticSource  # noqa: E402
from core.wilson import hedge_for, wilson  # noqa: E402
from scripts.fit_xg import commit  # noqa: E402
from sim.generate import Match as SimMatch  # noqa: E402

OUT = ROOT / "data" / "hedging_eval.json"
TEAMS = ("NOR", "HAR")
Rule = Callable[[int, int, str], bool]  # (k, n, lane) -> called "clear"


def rules(s: Settings, league: League) -> dict[str, Rule]:
    v = s.verifier

    def wilson_rule(k: int, n: int, _lane: str) -> bool:
        lo, hi = wilson(k, n, v.z)
        return hedge_for(n, lo, hi, v.lane_baseline, v.n_min) == "clear"

    def beta_rule(h: Hedge) -> Rule:
        return lambda k, n, lane: (
            beta_hedge(k, n, league.lane_shares[lane], h, v.n_min, league.attack_deff) == "clear"
        )

    out: dict[str, Rule] = {"wilson": wilson_rule}
    for c in s.hedge_eval.candidates:
        out[c.name] = beta_rule(c)
    return out


def overloads(seed: int, hero: bool, s: Settings) -> list[dict[str, Any]]:
    events = [Event.model_validate(e) for e in SimMatch(seed, hero, s).run()]
    clock = Clock(period_starts=[0, s.sim.half_s], end_t=2 * s.sim.half_s)
    return overload_moments(events, TEAMS, s, clock)


def null_study(s: Settings, judge: dict[str, Rule]) -> dict[str, Any]:
    per_match: dict[str, list[int]] = {name: [] for name in judge}
    raised = 0
    for i in range(s.hedge_eval.null_matches):
        found = overloads(s.hedge_eval.null_seed + i, False, s)
        raised += len(found)
        for name, rule in judge.items():
            per_match[name].append(sum(rule(m["k"], m["n"], m["lane"]) for m in found))
    return {
        "matches": s.hedge_eval.null_matches,
        "overloads_raised_per_match": round(raised / s.hedge_eval.null_matches, 3),
        "rules": {
            name: {
                "false_clear_per_match": round(statistics.mean(c), 3),
                "matches_with_a_false_clear": round(sum(x > 0 for x in c) / len(c), 3),
            }
            for name, c in per_match.items()
        },
    }


def strong(s: Settings) -> Settings:
    """The same settings with the stronger, longer planted overload."""
    e = s.hedge_eval
    sim = s.sim.model_copy(
        update={
            "overload_lane_weights": e.strong_lane_weights,
            "hero_overload_until_t": e.strong_until_t,
        }
    )
    return s.model_copy(update={"sim": sim})


def planted_study(s: Settings, judge: dict[str, Rule]) -> dict[str, Any]:
    start, until = s.sim.hero_sub_t, s.sim.hero_overload_until_t
    last = until + s.moments.check_every_s  # the first look after it ends still sees it
    delays: dict[str, list[float]] = {name: [] for name in judge}
    for i in range(s.hedge_eval.planted_matches):
        found = [
            m
            for m in overloads(s.hedge_eval.planted_seed + i, True, s)
            if m["subject"]["id"] == "NOR"
            and m["lane"] == "left"
            and start <= m["created_t"] <= last
        ]
        for name, rule in judge.items():
            hit = next((m["created_t"] for m in found if rule(m["k"], m["n"], m["lane"])), None)
            if hit is not None:
                delays[name].append((hit - start) / 60)
    n = s.hedge_eval.planted_matches
    return {
        "matches": n,
        "overload": {
            "team": "NOR",
            "lane": "left",
            "from_t": start,
            "until_t": until,
            "lane_weights": list(s.sim.overload_lane_weights),
        },
        "rules": {
            name: {
                "detected": round(len(d) / n, 3),
                "median_delay_min": round(statistics.median(d), 1) if d else None,
                "mean_delay_min": round(statistics.mean(d), 1) if d else None,
            }
            for name, d in delays.items()
        },
    }


def main() -> None:
    argparse.ArgumentParser(description="False-alarm study for the overload hedge.").parse_args()
    s = load()
    _, league = SyntheticSource().load()
    judge = rules(s, league)
    result = {
        "run_date": datetime.date.today().isoformat(),
        "commit": commit(),
        "lane_shares": league.lane_shares,
        "attack_deff": league.attack_deff,
        "candidates": [c.model_dump() for c in s.hedge_eval.candidates],
        "null": null_study(s, judge),
        "planted": planted_study(s, judge),
        "planted_strong": planted_study(strong(s), judge),
    }
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(
        "rule                               false/match  >=1   hero: det delay  strong: det delay"
    )
    for name in judge:
        f = result["null"]["rules"][name]
        p, q = result["planted"]["rules"][name], result["planted_strong"]["rules"][name]
        print(
            f"{name:34s} {f['false_clear_per_match']:11.3f} {f['matches_with_a_false_clear']:5.3f}"
            f" {p['detected']:10.3f} {p['median_delay_min']!s:>5s} {q['detected']:12.3f}"
            f" {q['median_delay_min']!s:>5s}"
        )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
