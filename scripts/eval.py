"""Run the verifier over tests/seeded_claims.json and print catch and false-reject rates.

Run: python scripts/eval.py

The injected claims come from our own rule-based drafter. They are test cases
for the verifier, not a measurement of how any language model behaves.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.verifier import verify  # noqa: E402
from core.config import ROOT  # noqa: E402

REJECT_CATEGORIES = {"wrong_number", "wrong_player", "wrong_window", "future_event"}


def caught(category: str, out: dict[str, Any]) -> bool:
    """Did the verifier handle this injected claim the way SPEC section 7 requires?"""
    v = out["verification"]
    if category in REJECT_CATEGORIES:
        return v["status"] == "rejected"
    if category == "intent_word":
        return v["status"] == "corrected" and any(c["reason"].startswith("intent") for c in v.get("corrections", []))
    if category == "small_sample":
        return v["status"] == "unverifiable" or out.get("uncertainty", {}).get("hedge") == "early"
    raise ValueError(category)


def run() -> dict[str, Any]:
    seeded = json.loads((ROOT / "tests" / "seeded_claims.json").read_text(encoding="utf-8"))
    match = json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))
    per: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    false_rejects: list[str] = []
    for entry in seeded:
        out = verify(entry["fact"], match)
        cat = entry["category"]
        if cat == "good":
            per[cat][1] += 1
            if out["verification"]["status"] != "verified":
                false_rejects.append(entry["id"])
            else:
                per[cat][0] += 1
        else:
            per[cat][1] += 1
            per[cat][0] += caught(cat, out)
    injected = [v for k, v in per.items() if k != "good"]
    return {"per": dict(per), "caught": sum(v[0] for v in injected), "injected": sum(v[1] for v in injected),
            "good": per["good"][1], "false_rejects": false_rejects}


def main() -> None:
    r = run()
    print("Verifier eval over tests/seeded_claims.json")
    print("(injected claims are test cases from our rule-based drafter, not model outputs)\n")
    print(f"{'category':<16}{'handled':>8}{'total':>7}")
    for cat, (ok, total) in sorted(r["per"].items()):
        print(f"{cat:<16}{ok:>8}{total:>7}")
    catch = r["caught"] / r["injected"]
    fr = len(r["false_rejects"]) / r["good"]
    print(f"\nCatch rate:        {r['caught']}/{r['injected']} = {catch:.1%}")
    print(f"False-reject rate: {len(r['false_rejects'])}/{r['good']} = {fr:.1%}")
    if r["false_rejects"]:
        print("False rejects:", ", ".join(r["false_rejects"]))


if __name__ == "__main__":
    main()
