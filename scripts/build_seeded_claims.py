"""Build tests/seeded_claims.json from the committed hero match.

Run: python scripts/build_seeded_claims.py

Good claims are every non-injected draft. Bad claims apply the drafter's
injection functions to chosen good drafts with varied parameters. All of them
are test cases for the verifier, not model outputs.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drafter as d  # noqa: E402
from core.config import ROOT, load  # noqa: E402
from core.moments import detect_moments  # noqa: E402

Fact = dict[str, Any]


def _ids() -> Iterator[str]:
    for i in range(900001, 999999):
        yield f"f_{i:06d}"


def build() -> list[dict[str, Any]]:
    match = json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))
    mu = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))["mu_xg_per_team_minute"]
    good = [f for f in d.draft_facts(detect_moments(match["events"], ("NOR", "HAR"), mu), match) if not f["injected"]]

    def find(metric: str) -> list[Fact]:
        return [f for f in good if f["claim"]["metric"].startswith(metric)]

    goal, over, turn = find("goals")[0], find("attack_share_"), find("turnovers_own_third")
    xg, acc, top, shots = find("xg_sum"), find("pass_accuracy"), find("top_shot_speed"), find("shots")
    ids, out = _ids(), []

    def add(cat: str, expect: str, fact: Fact) -> None:
        out.append({"id": f"sc_{len(out) + 1:03d}", "category": cat, "expect": expect, "fact": fact})

    for f in good:
        add("good", "verified", f)
    for base, bump in [(over[0], 9), (over[3], -6), (turn[0], 1), (turn[1], -2), (xg[1], 0.15), (acc[0], 5),
                       (top[0], 7), (shots[0], 1), (goal, -2)]:
        add("wrong_number", "rejected", d.inject_wrong_number(base, next(ids), bump))
    mates = [p for p in match["meta"]["teams"]["NOR"]["players"] if p not in ("NOR_01", "NOR_09")]
    for base, wrong in [(goal, mates[0]), (goal, mates[5]), (top[0], "HAR_09"), (top[1], "NOR_11"),
                        (shots[0], "HAR_08"), (shots[1], "NOR_10")]:
        add("wrong_player", "rejected", d.inject_wrong_player(base, next(ids), wrong, match))
    for base, shift in [(turn[0], 900), (over[2], 600), (xg[2], 2700), (acc[2], 2700), (goal, 120), (shots[1], 2700)]:
        add("wrong_window", "rejected", d.inject_wrong_window(base, next(ids), shift))
    for base, mode in [(over[0], "cite_later"), (turn[1], "cite_later"), (goal, "cite_later"),
                       (goal, "early_created"), (over[4], "early_created"), (xg[0], "early_created")]:
        add("future_event", "rejected", d.inject_future_event(base, next(ids), match, mode))
    for base, style in [(over[2], "deliberately"), (turn[2], "deliberately"), (xg[3], "deliberately"),
                        (turn[0], "clause"), (shots[0], "clause"), (acc[1], "clause")]:
        add("intent_word", "corrected", d.inject_intent(base, next(ids), style))
    s = load()["sim"]
    for team, lane, start in [("NOR", "left", s["hero_sub_t"]), ("HAR", "left", 0.0), ("NOR", "right", 0.0),
                              ("HAR", "centre", 2700.0), ("NOR", "centre", 1800.0), ("HAR", "right", 4200.0),
                              ("NOR", "left", 3000.0)]:
        f = d.first_small_sample(team, lane, start, match, next(ids))
        if f:
            add("small_sample", "hedge early or unverifiable", f)
    return out


def main() -> None:
    out = build()
    (ROOT / "tests" / "seeded_claims.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(dict(Counter(c["category"] for c in out)))


if __name__ == "__main__":
    main()
