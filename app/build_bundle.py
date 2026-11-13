"""Build the static replay bundle: docs/data/hero.json and docs/data/cues.json.

Run: python -m app.build_bundle             (offline: replays data/agent_cache.json if present, else rules)
     python -m app.build_bundle --foundry   (calls the Scout agent on Microsoft Foundry, rewrites the cache)
     python -m app.build_bundle --source statsbomb [--match 8658] [--out DIR]
                                            (real open data, rule drafter; writes to data/external/bundles/)

Pipeline: events -> moments -> drafts (Scout or rules) -> verifier -> router -> renditions.
Rejected and unverifiable drafts go to the log only; they never reach the router.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.agents_foundry import (
    CACHE_PATH,
    FoundryCaller,
    Scout,
    load_cache,
    run_scout,
    save_cache,
    scout_metrics,
    verify_rules,
)
from app.drafter import Moment, draft_facts
from app.renditions import all_preserved, with_renditions
from app.router import route, routing_rules
from app.series import build_series
from core.config import ROOT, Settings, load
from core.events import Match
from core.facts import Fact, validate_fact
from core.moments import detect_moments
from core.sources import EventSource, League, SyntheticSource
from core.statsbomb import StatsBombOpenSource

ROUTABLE = ("verified", "corrected")
DOCS_DATA = ROOT / "docs" / "data"
REAL_BUNDLES = ROOT / "data" / "external" / "bundles"  # git-ignored, like the raw data
# Optional event fields kept for the pitch, shot map and evidence drawer.
EVENT_EXTRAS = (
    "period",
    "x",
    "y",
    "end_x",
    "end_y",
    "outcome",
    "xg",
    "shot_speed",
    "ball_speed",
    "player_speed",
    "pass_difficulty",
    "pass_distance",
    "card",
)
CUE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "cue_id",
            "fact_id",
            "start_t",
            "duration_s",
            "layout",
            "lang",
            "text",
            "evidence_ids",
            "checked",
            "tier",
        ],
        "properties": {
            "cue_id": {"type": "string", "pattern": "^c_[0-9]{6}$"},
            "fact_id": {"type": "string", "pattern": "^f_[0-9]{6}$"},
            "start_t": {"type": "number", "minimum": 0},
            "duration_s": {"type": "number", "exclusiveMinimum": 0},
            "layout": {"enum": ["lower_third", "score_bug", "ticker"]},
            "lang": {"enum": ["en", "es"]},
            "text": {"type": "string", "minLength": 1},
            "evidence_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
            "checked": {"const": True},
            "tier": {"enum": ["T0", "T1"]},
        },
    },
}


def load_match() -> tuple[Match, League]:
    """The synthetic match and its league priors (the default source)."""
    return SyntheticSource().load()


def slim_events(match: Match) -> list[dict[str, Any]]:
    """Events for the replay and the evidence drawer."""
    names = match.meta.names()
    out = []
    for e in match.events:
        row: dict[str, Any] = {
            "id": e.id,
            "t": e.t,
            "type": e.type,
            "team": e.team,
            "player_id": e.player_id,
            "player": names[e.player_id],
        }
        row.update({k: v for k in EVENT_EXTRAS if (v := getattr(e, k)) is not None})
        if e.player_off is not None:
            row["player_off"] = names[e.player_off]
        out.append(row)
    return out


def score_timeline(match: Match) -> list[dict[str, Any]]:
    home, away = match.meta.home, match.meta.away
    score, out = {home: 0, away: 0}, [{"t": 0.0, home: 0, away: 0}]
    for e in match.events:
        if e.type == "goal":
            score[e.team] += 1
            out.append({"t": e.t, **score})
    return out


def make_cues(facts: list[Fact]) -> list[dict[str, Any]]:
    """Cues that air with nobody at the controls (T0 now, T1 after the veto window)."""
    cues = []
    for f in facts:
        if not all_preserved(f) or f.risk is None:
            continue
        for r in f.renditions or []:
            if r.kind == "cue" and r.start_t is not None:
                cues.append(
                    {
                        "fact_id": f.fact_id,
                        "start_t": r.start_t,
                        "duration_s": r.duration_s,
                        "layout": r.layout,
                        "lang": r.lang,
                        "text": r.text,
                        "evidence_ids": list(f.evidence.event_ids),
                        "checked": True,
                        "tier": f.risk.tier,
                    }
                )
    cues.sort(key=lambda c: (c["start_t"], c["fact_id"], c["lang"]))
    return [{"cue_id": f"c_{i:06d}", **c} for i, c in enumerate(cues, 1)]


def make_scout(foundry: bool, s: Settings) -> Scout | None:
    """Live Scout with --foundry; otherwise replay the cache if there is one; otherwise no Scout."""
    budget = s.agents.max_calls
    if foundry:
        return Scout(FoundryCaller(s.agents), {}, budget, prior=load_cache())
    return Scout(None, load_cache(), budget) if CACHE_PATH.exists() else None


def checked_drafts(
    moments: list[Moment], match: Match, league: League, scout: Scout | None, s: Settings
) -> list[Fact]:
    """Verified drafts: one per moment (Scout, else rules), then the rule-based injected drafts."""
    drafts = draft_facts(moments, match, league, s)
    good, injected = drafts[: len(moments)], drafts[len(moments) :]
    if scout:
        first = run_scout(scout, moments, good, match, league, s)
    else:
        first = [verify_rules(d, match, league, s) for d in good]
    return first + [verify_rules(d, match, league, s) for d in injected]


def metrics(checked: list[Fact], facts: list[Fact]) -> dict[str, Any]:
    injected = [c for c in checked if c.injected]
    return {
        "drafts": len(checked),
        "injected_drafts": len(injected),
        "injected_not_verified": sum(c.verification.status != "verified" for c in injected),
        "by_status": dict(Counter(c.verification.status for c in checked)),
        "by_tier": dict(Counter(f.risk.tier for f in facts if f.risk)),
        "renditions_failed_number_check": sum(not all_preserved(f) for f in facts),
        "scout": scout_metrics([c for c in checked if not c.injected]),
    }


def build(
    s: Settings,
    scout: Scout | None = None,
    use_cache: bool = True,
    source: EventSource | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = source or SyntheticSource()
    match, league = source.load()
    teams = (match.meta.home, match.meta.away)
    if scout is None and use_cache:
        scout = make_scout(False, s)
    moments = detect_moments(match.events, teams, league, s, match.meta.clock)
    checked = checked_drafts(moments, match, league, scout, s)
    routable = [c for c in checked if c.verification.status in ROUTABLE]
    facts = sorted(
        (with_renditions(route(c, s.router), match, s) for c in routable),
        key=lambda f: (f.created_t, f.fact_id),
    )
    log = [c for c in checked if c.verification.status not in ROUTABLE]
    for f in facts + log:
        validate_fact(f)
    meta = {
        **match.meta.model_dump(),
        "win_prob_label": "model estimate",
        "win_prob_method": "poisson_v1",
        "mu": league.mu,
        "pitch": s.pitch.model_dump(),
        "routing": routing_rules(s.router).model_dump(mode="json"),
    }
    if not match.meta.synthetic:
        meta["data_source"] = source.label
    hero = {
        "meta": meta,
        "events": slim_events(match),
        "score_timeline": score_timeline(match),
        "facts": [f.to_dict() for f in facts],
        "log": [f.to_dict() for f in log],
        "metrics": metrics(checked, facts),
        "series": build_series(match.events, teams, league, s, match.meta.clock.half_t),
    }
    cues = make_cues(facts)
    Draft202012Validator(CUE_SCHEMA).validate(cues)
    return hero, cues


def write(
    s: Settings,
    out_dir: Path = DOCS_DATA,
    foundry: bool = False,
    source: EventSource | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # The Scout's prompt and cache are for the synthetic match; real matches use the rule drafter.
    scout = make_scout(foundry, s) if source is None else None
    hero, cues = build(s, scout, use_cache=False, source=source)
    if foundry and scout is not None:
        save_cache(scout.cache)
        print(f"Scout calls: {scout.calls}  cache entries written: {len(scout.cache)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hero.json").write_text(
        json.dumps(hero, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "cues.json").write_text(
        json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return hero, cues


def real_source(s: Settings, match_id: int | None, out: Path | None) -> tuple[EventSource, Path]:
    """The StatsBomb source and where its bundle goes: never docs/ (see data_sources.md)."""
    mid = match_id or s.statsbomb.match_id
    out_dir = (out or REAL_BUNDLES / str(mid)).resolve()
    if out_dir.is_relative_to(ROOT / "docs"):
        raise SystemExit("real-data bundles are not written to docs/; see data_sources.md")
    return StatsBombOpenSource(
        s.statsbomb, s.pitch, s.xg, mid, s.moments.overload_window_s
    ), out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the replay bundle.")
    ap.add_argument(
        "--foundry",
        action="store_true",
        help="call the Scout agent on Microsoft Foundry (needs .env)",
    )
    ap.add_argument("--source", choices=("synthetic", "statsbomb"), default="synthetic")
    ap.add_argument("--match", type=int, help="StatsBomb match id (default: config.yaml)")
    ap.add_argument("--out", type=Path, help="output folder for a StatsBomb bundle")
    args = ap.parse_args()
    s, foundry = load(), args.foundry
    if args.source == "statsbomb":
        if foundry:
            raise SystemExit("--foundry is for the synthetic match only")
        source, out_dir = real_source(s, args.match, args.out)
        print(f"source: {source.label}; drafts from: rule-based drafter")
        hero, cues = write(s, out_dir, source=source)
        print(f"wrote {out_dir} (git-ignored; not published)")
    else:
        print(
            "drafts from:",
            "Scout on Foundry (live)"
            if foundry
            else "Scout replayed from data/agent_cache.json"
            if CACHE_PATH.exists()
            else "rule-based drafter",
        )
        hero, cues = write(s, foundry=foundry)
    m = hero["metrics"]
    print(
        f"facts routed: {len(hero['facts'])}  log (rejected/unverifiable): {len(hero['log'])}  cues: {len(cues)}"
    )
    print(f"by status: {m['by_status']}  by tier: {m['by_tier']}")
    print(f"renditions failing the number check: {m['renditions_failed_number_check']}")
    print(f"scout: {m['scout']}")


if __name__ == "__main__":
    main()
