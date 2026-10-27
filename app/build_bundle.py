"""Build the static replay bundle: docs/data/hero.json and docs/data/cues.json.

Run: python -m app.build_bundle

Pipeline: events -> moments -> drafts -> verifier -> router -> renditions.
Rejected and unverifiable drafts go to the log only; they never reach the router.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.drafter import draft_facts
from app.renditions import all_preserved, with_renditions
from app.router import route
from app.series import build_series
from app.verifier import validate_fact, verify
from core.config import ROOT, load
from core.moments import detect_moments

Fact = dict[str, Any]
# Optional event fields kept for the pitch, shot map and evidence drawer.
EVENT_EXTRAS = ("period", "x", "y", "end_x", "end_y", "outcome", "xg", "shot_speed", "ball_speed",
                "player_speed", "pass_difficulty", "pass_distance", "card")
CUE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["cue_id", "fact_id", "start_t", "duration_s", "layout", "lang", "text", "evidence_ids",
                     "checked", "tier"],
        "properties": {
            "cue_id": {"type": "string", "pattern": "^c_[0-9]{6}$"},
            "fact_id": {"type": "string", "pattern": "^f_[0-9]{6}$"},
            "start_t": {"type": "number", "minimum": 0}, "duration_s": {"type": "number", "exclusiveMinimum": 0},
            "layout": {"enum": ["lower_third", "score_bug", "ticker"]}, "lang": {"enum": ["en", "es"]},
            "text": {"type": "string", "minLength": 1},
            "evidence_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
            "checked": {"const": True}, "tier": {"enum": ["T0", "T1"]},
        },
    },
}


def load_match() -> tuple[dict[str, Any], float]:
    match = json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))
    mu = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))["mu_xg_per_team_minute"]
    return match, mu


def slim_events(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Events for the replay and the evidence drawer."""
    names = {pid: n for t in match["meta"]["teams"].values() for pid, n in t["players"].items()}
    out = []
    for e in match["events"]:
        row = {"id": e["id"], "t": e["t"], "type": e["type"], "team": e["team"], "player_id": e["player_id"],
               "player": names[e["player_id"]]}
        row.update({k: e[k] for k in EVENT_EXTRAS if k in e})
        if "player_off" in e:
            row["player_off"] = names[e["player_off"]]
        out.append(row)
    return out


def score_timeline(match: dict[str, Any]) -> list[dict[str, Any]]:
    home, away = match["meta"]["home"], match["meta"]["away"]
    score, out = {home: 0, away: 0}, [{"t": 0.0, home: 0, away: 0}]
    for e in match["events"]:
        if e["type"] == "goal":
            score[e["team"]] += 1
            out.append({"t": e["t"], **score})
    return out


def make_cues(facts: list[Fact]) -> list[dict[str, Any]]:
    """Cues that air with nobody at the controls (T0 now, T1 after the veto window)."""
    cues = []
    for f in facts:
        if not all_preserved(f):
            continue
        for r in f["renditions"]:
            if r["kind"] == "cue" and "start_t" in r:
                cues.append({"fact_id": f["fact_id"], "start_t": r["start_t"], "duration_s": r["duration_s"],
                             "layout": r["layout"], "lang": r["lang"], "text": r["text"],
                             "evidence_ids": f["evidence"]["event_ids"], "checked": True, "tier": f["risk"]["tier"]})
    cues.sort(key=lambda c: (c["start_t"], c["fact_id"], c["lang"]))
    return [{"cue_id": f"c_{i:06d}", **c} for i, c in enumerate(cues, 1)]


def metrics(drafts: list[Fact], checked: list[Fact], facts: list[Fact]) -> dict[str, Any]:
    injected = [c for c in checked if c["injected"]]
    return {
        "drafts": len(drafts), "injected_drafts": len(injected),
        "injected_not_verified": sum(c["verification"]["status"] != "verified" for c in injected),
        "by_status": dict(Counter(c["verification"]["status"] for c in checked)),
        "by_tier": dict(Counter(f["risk"]["tier"] for f in facts)),
        "renditions_failed_number_check": sum(not all_preserved(f) for f in facts),
    }


def build() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    match, mu = load_match()
    teams = (match["meta"]["home"], match["meta"]["away"])
    drafts = draft_facts(detect_moments(match["events"], teams, mu), match)
    checked = [verify(d, match) for d in drafts]
    routable = [c for c in checked if c["verification"]["status"] in ("verified", "corrected")]
    facts = sorted((with_renditions(route(c), match) for c in routable), key=lambda f: (f["created_t"], f["fact_id"]))
    log = [c for c in checked if c not in routable]
    for f in facts + log:
        validate_fact(f)
    meta = {**match["meta"], "win_prob_label": "model estimate", "win_prob_method": "poisson_v1", "mu": mu,
            "pitch": load()["pitch"]}
    hero = {"meta": meta, "events": slim_events(match), "score_timeline": score_timeline(match),
            "facts": facts, "log": log, "metrics": metrics(drafts, checked, facts),
            "series": build_series(match["events"], teams, mu)}
    cues = make_cues(facts)
    Draft202012Validator(CUE_SCHEMA).validate(cues)
    return hero, cues


def write(out_dir: Path = ROOT / "docs" / "data") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    hero, cues = build()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hero.json").write_text(json.dumps(hero, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    return hero, cues


def main() -> None:
    hero, cues = write()
    m = hero["metrics"]
    print(f"facts routed: {len(hero['facts'])}  log (rejected/unverifiable): {len(hero['log'])}  cues: {len(cues)}")
    print(f"by status: {m['by_status']}  by tier: {m['by_tier']}")
    print(f"renditions failing the number check: {m['renditions_failed_number_check']}")


if __name__ == "__main__":
    main()
