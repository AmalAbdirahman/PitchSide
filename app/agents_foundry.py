"""Scout: a drafting agent on Microsoft Foundry, run at build time only (SPEC section 19).

The Scout turns one moment summary computed by core/ into a claim and English
wording. The deterministic verifier is still the gate: a rejected claim goes
back to the Scout once with the failed checks, and if it fails again it stays
rejected and is logged. Any call failure falls back to the rule-based drafter
for that moment, so the build always completes.

Every request and response is cached in data/agent_cache.json, so a build
without --foundry replays the same agent output with no network. Azure
packages are imported only when --foundry runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.drafter import player_name, render, share_hedge, team_name
from app.verifier import validate_fact, verify
from core.config import ROOT, load
from core.moments import minute

Fact = dict[str, Any]
Match = dict[str, Any]
Record = dict[str, Any]
Step = dict[str, Any]
CACHE_PATH = ROOT / "data" / "agent_cache.json"
ROUTABLE = ("verified", "corrected")
MOMENT_FIELDS = ("rule", "kind", "metric", "subject", "window", "team", "lane", "half", "card", "n", "k")

SCOUT_INSTRUCTIONS = """You are Scout, the drafting agent in a football match control room. The match is synthetic.
The user message is JSON describing one moment computed from the event log. Reply with JSON only, no prose:
{"claim": {"subject": {"type": ..., "id": ...}, "metric": ..., "window": {"start_t": ..., "end_t": ...},
 "comparator": "eq", "value": <number>},
 "text": {"canonical_en": "<one broadcast sentence, at most 200 characters>",
          "numbers": [{"key": ..., "value": ..., "unit": ...}]},
 "evidence_ids": [<integers>]}
Rules:
- Claim only what is in the input. Copy subject, metric and window from the input moment.
- claim.value is the input claim_value.
- text.numbers must list exactly the input numbers, same keys. Every number written in the sentence must be one of them,
  written the same way (minutes as ordinals like 63rd, percentages like 71%, xG with two decimals). Write no other digits.
- Minutes are match minutes. since_minute is when the window starts; do not mention when it ends.
- For attack_share_* metrics the value is the share of the team's OWN attacks that went down that lane:
  write "71% of Northbridge attacks have gone down the right", never "71% of right-side attacks".
- For top_shot_speed say it was the fastest shot of that half. Write xG with exactly two decimals (1.00, 0.30).
- Use only the team and player names given in the input. Name no one else.
- Cite only event ids from the input evidence_ids.
- No intent words: never say deliberately, tried to, wanted to, decided to. Describe what happened, not why.
- Respect the hedge: "clear" or "none" = plain statement; "suggests" = start with "The numbers suggest";
  "early" = start with "Early sign:".
- If the input has "feedback", your previous claim failed the verifier. Fix exactly what the feedback says."""


# ---- requests and facts -----------------------------------------------------

def scout_request(m: dict[str, Any], match: Match) -> dict[str, Any]:
    """The JSON the Scout sees for one moment: facts computed by code, nothing else."""
    _, nums = render(m, match)
    teams = {tid: team_name(match, tid) for tid in match["meta"]["teams"]}
    pids = set(m.get("players", [])) | ({m["subject"]["id"]} if m["subject"]["type"] == "player" else set())
    hedge = "none"
    if m["rule"] == "overload":
        hedge = share_hedge(m["k"], m["n"], 100 * load()["verifier"]["lane_baseline"])
    return {"moment": {k: m[k] for k in MOMENT_FIELDS if k in m},
            "minutes": {"window_start": minute(m["window"]["start_t"]), "window_end": minute(m["window"]["end_t"])},
            "teams": teams, "players": {p: player_name(match, p) for p in sorted(pids)},
            "numbers": nums, "claim_value": next((n["value"] for n in nums if n["key"] == "value"), m["value"]),
            "evidence_ids": list(m["evidence_ids"]), "hedge": hedge}


def parse_output(text: str) -> dict[str, Any]:
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    out = json.loads(body)
    if not isinstance(out, dict):
        raise ValueError("Scout output is not a JSON object")
    return out


def scout_fact(out: dict[str, Any], m: dict[str, Any], match: Match, fact_id: str, required: list[str]) -> Fact:
    """Build a draft fact from Scout output. Raises ValueError/KeyError/TypeError on malformed output."""
    claim, text = dict(out["claim"]), out["text"]
    keys = sorted(n["key"] for n in text["numbers"])
    if keys != sorted(required):
        raise ValueError(f"numbers keys {keys} differ from the input keys {sorted(required)}")
    if str(claim.get("metric", "")).startswith("attack_share_"):
        claim["baseline"] = round(100 * load()["verifier"]["lane_baseline"], 2)
    fact: Fact = {
        "fact_id": fact_id, "match_id": match["meta"]["match_id"], "kind": m["kind"], "created_t": m["created_t"],
        "injected": False, "claim": claim,
        "text": {"draft_en": text["canonical_en"], "canonical_en": text["canonical_en"], "numbers": text["numbers"]},
        "evidence": {"event_ids": [int(i) for i in out["evidence_ids"]], "players": list(m.get("players", [])),
                     "window": dict(claim["window"])},
        "verification": {"status": "pending", "checks": []}, "approval": {"state": "not_routed"},
    }
    if "impact" in m:
        fact["impact"] = dict(m["impact"])
    validate_fact(fact)
    return fact


def template_wording(draft: Fact, m: dict[str, Any], match: Match) -> Fact:
    """Goals, substitutions and cards (T0) air in fixed template wording. The Scout's claim and
    evidence stand, and its own wording stays in draft_en for the log."""
    text, nums = render(m, match)
    draft["text"].update(canonical_en=text, numbers=nums)
    validate_fact(draft)
    return draft


def feedback(checked: Fact) -> str:
    failed = [f"{c['name']}: {c['detail']}" for c in checked["verification"]["checks"] if not c["passed"]]
    return "Your claim failed the verifier. " + "; ".join(failed) + ". Revise using only the input."


# ---- audit ------------------------------------------------------------------------

def step(agent: str, attempt: int, outcome: str, rec: Record | None = None, path: str | None = None) -> Step:
    s: Step = {"agent": agent, "attempt": attempt, "outcome": outcome}
    if rec and "path" in rec:
        s.update(path=rec["path"], model=rec["model"], ms=rec["ms"])
    elif path:
        s["path"] = path
    return s


def with_audit(fact: Fact, steps: list[Step]) -> Fact:
    fact["audit"] = {"steps": steps}
    validate_fact(fact)
    return fact


def verify_rules(draft: Fact, match: Match, steps: list[Step] | None = None) -> Fact:
    """Verify a rule-based draft and record that the rules wrote it."""
    checked = verify(draft, match)
    trail = (steps or []) + [step("Drafter", 1, "drafted", path="rules"),
                             step("Verifier", 1, checked["verification"]["status"], path="rules")]
    return with_audit(checked, trail)


# ---- cache and session --------------------------------------------------------------

def cache_key(request: dict[str, Any]) -> str:
    blob = json.dumps({"agent": "Scout", "instructions": SCOUT_INSTRUCTIONS, "input": request}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_cache(path: Path = CACHE_PATH) -> dict[str, Record]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.exists() else {}


def save_cache(entries: dict[str, Record], path: Path = CACHE_PATH) -> None:
    doc = {"note": "Scout requests and responses from a --foundry build. Synthetic match data only.",
           "entries": entries}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


Caller = Callable[[str], tuple[str, str, str]]  # user JSON -> (response text, path, model)


@dataclass
class Scout:
    """One build's Scout session. `caller` is None when replaying from the cache.

    A live session reuses successful answers in `prior` for identical requests, so a re-run
    after a network failure only repeats the failed calls.
    """
    caller: Caller | None
    cache: dict[str, Record]
    budget: int
    prior: dict[str, Record] = field(default_factory=dict)
    calls: int = 0

    def ask(self, request: dict[str, Any]) -> tuple[str, Record | None]:
        """Outcome label and the cached or live record for one request."""
        if self.calls >= self.budget:
            return "no_budget", None
        self.calls += 1
        key = cache_key(request)
        if self.caller is None:
            rec = self.cache.get(key)
            return ("not_cached", None) if rec is None else (("call_failed" if "error" in rec else "ok"), rec)
        if key in self.prior and "error" not in self.prior[key]:
            self.cache[key] = self.prior[key]
            return "ok", self.cache[key]
        t0 = time.perf_counter()
        try:
            text, path, model = self.caller(json.dumps(request, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 - any failure falls back to the rules
            print(f"  Scout call failed: {type(e).__name__}: {str(e)[:200]}")
            self.cache[key] = {"request": request, "error": type(e).__name__}
            return "call_failed", self.cache[key]
        rec = {"request": request, "response_text": text, "path": path, "model": model,
               "ms": round(1000 * (time.perf_counter() - t0))}
        self.cache[key] = rec
        return "ok", rec


# ---- the Scout loop ------------------------------------------------------------------------

def attempt(scout: Scout, request: dict[str, Any], m: dict[str, Any], match: Match, fact_id: str,
            n: int) -> tuple[Fact | None, dict[str, Any] | None, Step]:
    """One Scout call. Returns (checked fact or None, parsed output or None, the steps taken)."""
    outcome, rec = scout.ask(request)
    if outcome != "ok" or rec is None:
        return None, None, [step("Scout", n, outcome, rec)]
    try:
        out = parse_output(rec["response_text"])
        draft = scout_fact(out, m, match, fact_id, [k["key"] for k in request["numbers"]])
    except (ValueError, KeyError, TypeError) as e:
        return None, None, [step("Scout", n, f"invalid_output: {str(e)[:120]}", rec)]
    steps = [step("Scout", n, "drafted", rec)]
    if m["metric"] in load()["router"]["t0_metrics"]:
        draft = template_wording(draft, m, match)
        steps.append(step("Drafter", n, "template wording (T0)", path="rules"))
    return verify(draft, match), out, steps


def scout_moment_first(scout: Scout, m: dict[str, Any], rules_draft: Fact, match: Match) -> tuple[Fact, Any]:
    request = scout_request(m, match)
    checked, out, steps = attempt(scout, request, m, match, rules_draft["fact_id"], 1)
    if checked is None:
        return verify_rules(rules_draft, match, steps), None
    trail = steps + [step("Verifier", 1, checked["verification"]["status"], path="rules")]
    return with_audit(checked, trail), (request, out)


def scout_moment_retry(scout: Scout, m: dict[str, Any], first: Fact, ctx: Any, match: Match) -> Fact:
    """One retry for a Scout claim the verifier did not pass. Otherwise the fact is unchanged."""
    if ctx is None or first["verification"]["status"] in ROUTABLE:
        return first
    request, out = ctx
    retry_req = {**request, "previous_output": out, "feedback": feedback(first)}
    checked, _, steps = attempt(scout, retry_req, m, match, first["fact_id"], 2)
    trail = first["audit"]["steps"] + steps
    if checked is None:
        return with_audit(first, trail)
    return with_audit(checked, trail + [step("Verifier", 2, checked["verification"]["status"], path="rules")])


def run_scout(scout: Scout, moments: list[dict[str, Any]], rules_drafts: list[Fact], match: Match) -> list[Fact]:
    """Pass 1: a Scout call per moment. Pass 2: one retry each for rejected claims, while budget lasts."""
    first = [scout_moment_first(scout, m, d, match) for m, d in zip(moments, rules_drafts)]
    return [scout_moment_retry(scout, m, f, ctx, match) for m, (f, ctx) in zip(moments, first)]


def scout_metrics(facts: list[Fact]) -> dict[str, int]:
    """How each moment's fact came about, read from the audit trails."""
    out = {"first_pass": 0, "after_retry": 0, "rejected": 0, "rules": 0}
    for f in facts:
        steps = f.get("audit", {}).get("steps", [])
        drafted = [s for s in steps if s["agent"] == "Scout" and s["outcome"] == "drafted"]
        if not drafted:
            out["rules"] += 1
        elif f["verification"]["status"] not in ROUTABLE:
            out["rejected"] += 1
        else:
            out["after_retry" if drafted[-1]["attempt"] > 1 else "first_pass"] += 1
    return out


# ---- the live Foundry client (only with --foundry) ---------------------------------------

class FoundryCaller:
    """Calls the Scout prompt agent; falls back to a plain responses call with the same instructions."""

    def __init__(self) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import PromptAgentDefinition
        from azure.identity import DefaultAzureCredential
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        cfg = load()["agents"]
        self.model = os.environ["FOUNDRY_MODEL"]
        project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                                  credential=DefaultAzureCredential(), allow_preview=True)
        opts = {"timeout": cfg["timeout_s"], "max_retries": 0}
        self.plain = project.get_openai_client(**opts)
        self.agent: Any = None
        try:
            project.agents.create_version(cfg["scout_name"], definition=PromptAgentDefinition(
                model=self.model, instructions=SCOUT_INSTRUCTIONS))
            self.agent = project.get_openai_client(agent_name=cfg["scout_name"], **opts)
        except Exception as e:  # noqa: BLE001
            print(f"  Scout agent unavailable ({type(e).__name__}: {str(e)[:200]}); using plain responses")

    def __call__(self, user: str) -> tuple[str, str, str]:
        if self.agent is not None:
            try:
                return self.agent.responses.create(input=user).output_text, "foundry_agent", self.model
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) is None:
                    raise  # network trouble, not the agent API: fail this call only
                print(f"  Scout agent call failed ({type(e).__name__}: {str(e)[:200]}); using plain responses from now on")
                self.agent = None
        r = self.plain.responses.create(model=self.model, instructions=SCOUT_INSTRUCTIONS, input=user)
        return r.output_text, "foundry_responses", self.model
