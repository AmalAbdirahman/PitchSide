"""What the Scout is sent in each mode, and how its validated output becomes a draft fact (P11).

handed: the moment's numbers, claim value and evidence, computed by code; the Scout words them.
explore: the match state and the flagged moment as a lead, with no numbers; the Scout finds them
with the read-only tools (app/scout_tools.py). In both modes the verifier recomputes everything.
"""

from __future__ import annotations

from typing import Any, Literal

from app.drafter import Moment, claim_value, player_name, render, team_name
from app.scout_schema import ScoutOutput, ScoutOutputError, as_dict
from core.config import Verifier
from core.events import Match
from core.facts import Claim, Evidence, Fact, FactText, Impact, TextNumber, validate_fact
from core.moments import score_at
from core.sources import League

Mode = Literal["handed", "explore"]
MOMENT_FIELDS = (
    "rule",
    "kind",
    "metric",
    "subject",
    "window",
    "team",
    "lane",
    "half",
    "card",
    "own_goal",
    "n",
    "k",
)
LEAD_FIELDS = ("rule", "kind", "subject", "window", "team", "lane", "half")


def handed_request(m: Moment, match: Match, v: Verifier, memory: dict[str, Any]) -> dict[str, Any]:
    """The moment's facts, computed by code, nothing else."""
    _, nums = render(m, match, v)
    teams = {tid: team_name(match, tid) for tid in match.meta.teams}
    pids = set(m.get("players", [])) | (
        {m["subject"]["id"]} if m["subject"]["type"] == "player" else set()
    )
    hedge = str(m["hedge"]) if m["rule"] == "overload" else "none"
    return {
        "moment": {k: m[k] for k in MOMENT_FIELDS if k in m},
        "minutes": {
            "window_start": match.meta.clock.minute(m["window"]["start_t"]),
            "window_end": match.meta.clock.minute(m["window"]["end_t"]),
        },
        "teams": teams,
        "players": {p: player_name(match, p) for p in sorted(pids)},
        "numbers": [n.model_dump(exclude_none=True) for n in nums],
        "claim_value": claim_value(m, nums),
        "evidence_ids": list(m["evidence_ids"]),
        "hedge": hedge,
        "memory": memory,
    }


def explore_request(m: Moment, match: Match, memory: dict[str, Any]) -> dict[str, Any]:
    """The match state at the moment and the moment as a lead, with no numbers or evidence."""
    now, meta = float(m["created_t"]), match.meta
    teams = (meta.home, meta.away)
    return {
        "now": {"t": now, "minute": meta.clock.minute(now)},
        "score": score_at(match.events, now, teams),
        "teams": {tid: team_name(match, tid) for tid in teams},
        "players": {
            pid: player_name(match, pid) for tid in teams for pid in sorted(meta.teams[tid].players)
        },
        "lead": {k: m[k] for k in LEAD_FIELDS if k in m},
        "memory": memory,
    }


def _matches_moment(claim: Claim, m: Moment) -> bool:
    return claim.metric == m["metric"] and claim.subject.model_dump() == m["subject"]


def _players(claim: Claim, m: Moment, mode: Mode) -> list[str]:
    if mode == "handed" or _matches_moment(claim, m):
        return list(m.get("players", []))
    return [claim.subject.id] if claim.subject.type == "player" else []


def fact_from_output(
    out: ScoutOutput,
    m: Moment,
    match: Match,
    league: League,
    fact_id: str,
    mode: Mode,
    required: list[str] | None,
    t0_metrics: tuple[str, ...],
) -> Fact:
    """A draft fact from validated Scout output. Raises ScoutOutputError if the output breaks a
    rule the schema cannot express."""
    raw = as_dict(out)
    claim = Claim.model_validate(raw["claim"], strict=True)
    numbers = [TextNumber.model_validate(n, strict=True) for n in raw["text"]["numbers"]]
    keys = sorted(n.key for n in numbers)
    if required is not None and keys != sorted(required):
        raise ScoutOutputError(f"numbers keys {keys} differ from the input keys {sorted(required)}")
    if claim.metric in t0_metrics and not _matches_moment(claim, m):
        raise ScoutOutputError("goals, substitutions and cards may only restate the lead itself")
    if claim.metric.startswith("attack_share_"):  # set by code, never by the model
        claim.baseline = round(
            100 * league.lane_shares[claim.metric.removeprefix("attack_share_")], 2
        )
    same = _matches_moment(claim, m)
    fact = Fact.model_validate(
        {
            "fact_id": fact_id,
            "match_id": match.meta.match_id,
            "kind": m["kind"] if same else "stat",
            "created_t": m["created_t"],
            "injected": False,
            "claim": claim,
            "text": FactText(
                draft_en=out.text.canonical_en, canonical_en=out.text.canonical_en, numbers=numbers
            ),
            "evidence": Evidence(
                event_ids=list(out.evidence_ids),
                players=_players(claim, m, mode),
                window=claim.window.model_copy(),
            ),
            "verification": {"status": "pending", "checks": []},
            "approval": {"state": "not_routed"},
            "impact": Impact.model_validate(m["impact"]) if "impact" in m and same else None,
        }
    )
    validate_fact(fact)
    return fact
