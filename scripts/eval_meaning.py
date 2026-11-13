"""P12 eval: precision and recall of the meaning_matches check on a hand-labelled sentence set.

Run: python scripts/eval_meaning.py             (replays data/reader_cache.json, no network, and
                                                 checks the result against data/meaning_eval.json)
     python scripts/eval_meaning.py --foundry   (live Reader on Microsoft Foundry; needs .env;
                                                 adds its reads to data/reader_cache.json)

tests/meaning_cases.json holds sentences about claims from the synthetic match: "same" (the sentence
says what the claim says) and "different" (every number is the claim's, the meaning is not). For
each, the Reader reads the sentence with only the roster, and app/meaning.check_meaning compares its
reading with the claim. A flag is a failed check. Positive = "different":
- precision: of the sentences flagged, the share labelled different;
- recall: of the sentences labelled different, the share flagged.
A sentence the Reader could not read (no reply, invalid reply) counts as not flagged.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.meaning import meaning_problems  # noqa: E402
from app.reader import FoundryReader, Outcome, Reader, load_cache, save_cache  # noqa: E402
from core.config import ROOT, Settings, load  # noqa: E402
from core.events import Match  # noqa: E402
from core.facts import Claim  # noqa: E402
from core.sources import SyntheticSource  # noqa: E402
from scripts.fit_xg import commit  # noqa: E402

CASES = ROOT / "tests" / "meaning_cases.json"
OUT = ROOT / "data" / "meaning_eval.json"


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]  # type: ignore[no-any-return]


def judge(case: dict[str, Any], o: Outcome, match: Match, s: Settings) -> dict[str, Any]:
    """One case: the reading, whether the check flags it, and why."""
    claim = Claim.model_validate(case["claim"])
    problems = meaning_problems(o.reading, claim, match.meta.clock, s.verifier) if o.reading else []
    return {
        "id": case["id"],
        "label": case["label"],
        "read": o.label,
        "flagged": bool(problems),
        "problems": problems,
        "reading": o.reading.model_dump() if o.reading else None,
    }


def scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(r["flagged"] and r["label"] == "different" for r in rows)
    fp = sum(r["flagged"] and r["label"] == "same" for r in rows)
    fn = sum(not r["flagged"] and r["label"] == "different" for r in rows)
    tn = len(rows) - tp - fp - fn
    return {
        "cases": len(rows),
        "different": tp + fn,
        "same": fp + tn,
        "unread": sum(r["read"] != "read" for r in rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
    }


def main(live: bool) -> None:
    s, cached = load(), load_cache()
    match, _ = SyntheticSource().load()
    caller = FoundryReader(s.agents) if live else None
    reader = Reader(caller, {} if live else cached, s.agents.reader_max_calls, prior=cached)
    rows = [judge(c, reader.read(c["sentence"], match), match, s) for c in load_cases()]
    record: dict[str, Any] = {
        "run_date": datetime.date.today().isoformat(),
        "commit": commit(),
        "live": live,
        "models": sorted({r["model"] for r in reader.cache.values() if "model" in r}),
        "scores": scores(rows),
        "cases": rows,
    }
    print(json.dumps(record["scores"]))
    if not live and OUT.exists():  # a replay checks the live record rather than replacing it
        same = json.loads(OUT.read_text(encoding="utf-8"))["scores"] == record["scores"]
        print(f"replay {'matches' if same else 'DIFFERS FROM'} {OUT.relative_to(ROOT)}")
        return
    if live:
        save_cache({**cached, **reader.cache})
        print(f"Reader calls: {reader.calls}")
    OUT.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Precision and recall of the meaning check.")
    ap.add_argument(
        "--foundry", action="store_true", help="call the Reader on Foundry (needs .env)"
    )
    main(ap.parse_args().foundry)
