"""P11 eval: the Scout in handed mode against explore mode, on the synthetic replay.

Run: python scripts/eval_scout.py             (replays data/agent_cache.json; no network)
     python scripts/eval_scout.py --foundry   (live Scout on Microsoft Foundry for both modes; needs .env;
                                               rewrites data/agent_cache.json)

For each mode, every moment the rules detect gets one Scout conversation (plus one retry for a
rejected claim or an invalid reply), with the memory of earlier facts. Rates are per moment:
- first_pass: the Scout's first claim passed the verifier (verified or corrected);
- after_retry: it passed on the retry;
- rejected: the verifier still rejected it after the retry;
- rules: no usable Scout answer (call failure, invalid twice, budget), so the rule-based draft stands;
- duplicate: a fact that passed repeats an earlier one that passed (same sentence, or same metric and
  subject over an overlapping window; app/scout_memory.duplicates).
The planted-error eval (scripts/eval.py) is separate and unchanged.
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents_foundry import (  # noqa: E402
    CACHE_PATH,
    FoundryCaller,
    Scout,
    load_cache,
    run_scout,
    save_cache,
    scout_metrics,
)
from app.drafter import draft_facts  # noqa: E402
from app.scout_memory import ROUTABLE, duplicates  # noqa: E402
from app.scout_requests import Mode  # noqa: E402
from core.config import ROOT, Settings, load  # noqa: E402
from core.facts import Fact  # noqa: E402
from core.moments import detect_moments  # noqa: E402
from core.sources import SyntheticSource  # noqa: E402
from scripts.fit_xg import commit  # noqa: E402

OUT = ROOT / "data" / "scout_eval.json"
MODES: tuple[Mode, ...] = ("handed", "explore")


def run_mode(mode: Mode, scout: Scout, s: Settings) -> list[Fact]:
    match, league = SyntheticSource().load()
    teams = (match.meta.home, match.meta.away)
    moments = detect_moments(match.events, teams, league, s, match.meta.clock)
    rules = draft_facts(moments, match, league, s)[: len(moments)]
    return run_scout(scout, moments, rules, match, league, s, mode)


def summary(facts: list[Fact], scout: Scout) -> dict[str, Any]:
    n = len(facts)
    counts = scout_metrics(facts)
    steps = [st for f in facts for st in (f.audit.steps if f.audit else []) if st.agent == "Scout"]
    ms = [st.ms for st in steps if st.ms is not None]
    aired = [f for f in facts if f.verification.status in ROUTABLE]
    dup = duplicates(aired)
    return {
        "moments": n,
        "counts": counts,
        "rates": {k: round(v / n, 3) for k, v in counts.items()},
        "invalid_outputs": sum(st.outcome.startswith("invalid_output") for st in steps),
        "aired": len(aired),
        "duplicates": dup,
        "duplicate_rate": round(dup / len(aired), 3) if aired else 0.0,
        "tool_calls": sum(st.tool_calls or 0 for st in steps),
        "model_responses": scout.calls,
        "latency_ms": {"median": statistics.median(ms), "max": max(ms)} if ms else None,
        "models": sorted({st.model for st in steps if st.model}),
        "paths": sorted({st.path for st in steps if st.path}),
    }


def main(live: bool) -> None:
    s, cached = load(), load_cache()
    caller = FoundryCaller(s.agents) if live else None
    record: dict[str, Any] = {"run_date": datetime.date.today().isoformat(), "commit": commit()}
    merged: dict[str, Any] = {}
    for mode in MODES:
        budget = s.agents.budget(mode)
        scout = Scout(caller, {} if live else cached, budget, prior=cached if live else {})
        facts = run_mode(mode, scout, s)
        record[mode] = summary(facts, scout)
        merged |= scout.cache
        r = record[mode]
        print(
            f"{mode}: {r['counts']}  duplicates {r['duplicates']}/{r['aired']}  "
            f"tool calls {r['tool_calls']}  responses {r['model_responses']}"
        )
    if live:
        save_cache(merged)
        record["live"] = True
        print(f"wrote {CACHE_PATH.relative_to(ROOT)}")
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scout eval: handed against explore mode.")
    ap.add_argument("--foundry", action="store_true", help="call the Scout on Foundry (needs .env)")
    main(ap.parse_args().foundry)
