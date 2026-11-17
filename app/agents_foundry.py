"""Scout: a drafting agent on Microsoft Foundry, run at build time only (SPEC section 19).

The Scout drafts one claim and an English sentence per moment, in one of two modes (P11):
- handed: it is given the moment's numbers and evidence, computed by code, and words them;
- explore: it is given the match state and the moment as a lead, and finds numbers and evidence
  itself with read-only tools backed by the metric registry (app/scout_tools.py).
Its reply must follow a JSON Schema generated from Pydantic models (app/scout_schema.py); anything
else is a typed error. The deterministic verifier is the gate: a rejected claim or an invalid reply
goes back to the Scout once with the problem, and a second failure stays rejected (or, for an
invalid reply, falls back to the rule-based drafter). Each request carries a compact memory of what
has already aired (app/scout_memory.py), recorded in the audit trail. With a Reader (P12,
app/reader.py), each sentence the Scout writes is read back by a second agent that sees only the
sentence and the roster; the verifier's meaning_matches check compares that reading with the claim.

Every request, response and tool call is cached in data/agent_cache.json, so a build without
--foundry replays the same agent output with no network; replay re-runs each recorded tool call and
refuses a record whose tool outputs no longer match. Azure packages are imported only with --foundry.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.drafter import Moment, render, scout_can_draft
from app.reader import Outcome, Reader
from app.scout_memory import ROUTABLE, memory_for, memory_ids
from app.scout_prompts import EXPLORE, HANDED
from app.scout_requests import Mode, explore_request, fact_from_output, handed_request
from app.scout_schema import ScoutOutputError, as_dict, output_schema, parse_scout
from app.scout_tools import TOOL_SPECS, ToolBox, ToolCall
from app.verifier import verify
from core.config import ROOT, Agents, MetricContext, Settings, Verifier
from core.events import Match
from core.facts import Audit, AuditStep, Fact, Reading, validate_fact
from core.sources import League

Record = dict[str, Any]  # one cache entry: the request, the raw response and the tool calls
Step = AuditStep
CACHE_PATH = ROOT / "data" / "agent_cache.json"
INSTRUCTIONS: dict[str, str] = {"handed": HANDED, "explore": EXPLORE}


# ---- feedback and audit -------------------------------------------------------------


def feedback(checked: Fact) -> str:
    failed = [f"{c.name}: {c.detail}" for c in checked.verification.checks if not c.passed]
    return "Your claim failed the verifier. " + "; ".join(failed) + ". Revise using only the input."


def step(
    agent: str,
    attempt: int,
    outcome: str,
    rec: Record | None = None,
    path: str | None = None,
    mode: Mode | None = None,
) -> Step:
    s: dict[str, Any] = {"agent": agent, "attempt": attempt, "outcome": outcome}
    if rec and "path" in rec:
        s.update(path=rec["path"], model=rec["model"], ms=rec["ms"])
        s["tool_calls"] = len(rec.get("tool_calls", [])) if mode == "explore" else None
    elif path:
        s["path"] = path
    if agent == "Scout":
        s["mode"] = mode
    return AuditStep.model_validate({k: v for k, v in s.items() if v is not None})


def read_step(attempt_no: int, o: Outcome) -> Step:
    s: dict[str, Any] = {"agent": "Reader", "attempt": attempt_no, "outcome": o.label}
    if o.rec and "path" in o.rec:
        s.update(path=o.rec["path"], model=o.rec["model"], ms=o.rec["ms"])
    if o.reading is not None:
        s["reading"] = o.reading
    return AuditStep.model_validate(s)


def with_audit(fact: Fact, steps: list[Step], memory: list[str] | None = None) -> Fact:
    fact.audit = Audit(steps=steps, memory=memory)
    validate_fact(fact)
    return fact


def template_wording(draft: Fact, m: Moment, match: Match, v: Verifier) -> Fact:
    """Goals, substitutions and cards (T0) air in fixed template wording. The Scout's claim and
    evidence stand, and its own wording stays in draft_en for the log."""
    draft.text.canonical_en, draft.text.numbers = render(m, match, v)
    validate_fact(draft)
    return draft


def verify_rules(
    draft: Fact,
    match: Match,
    league: League,
    s: Settings,
    steps: list[Step] | None = None,
    memory: list[str] | None = None,
) -> Fact:
    """Verify a rule-based draft and record that the rules wrote it."""
    checked = verify(draft, match, league, s)
    trail = (steps or []) + [
        step("Drafter", 1, "drafted", path="rules"),
        step("Verifier", 1, checked.verification.status, path="rules"),
    ]
    return with_audit(checked, trail, memory)


# ---- cache and session --------------------------------------------------------------


def cache_key(request: dict[str, Any], mode: Mode) -> str:
    blob = json.dumps(
        {
            "agent": "Scout",
            "mode": mode,
            "instructions": INSTRUCTIONS[mode],
            "schema": output_schema(),
            "tools": TOOL_SPECS if mode == "explore" else [],
            "input": request,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_cache(path: Path = CACHE_PATH) -> dict[str, Record]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.exists() else {}


def save_cache(entries: dict[str, Record], path: Path = CACHE_PATH) -> None:
    doc = {
        "note": "Scout requests, responses and tool calls from a --foundry build. Synthetic match data only.",
        "entries": entries,
    }
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


@dataclass(frozen=True)
class CallResult:
    text: str
    path: str
    model: str
    rounds: int  # model responses in the conversation (1 + rounds of tool calls)
    tool_calls: list[ToolCall]


Caller = Callable[[str, Mode, ToolBox | None], CallResult]  # user JSON, mode, tools -> result


def replay_ok(rec: Record, tools: ToolBox | None) -> bool:
    """Re-run every recorded tool call; the record stands only if each output is unchanged."""
    recorded = rec.get("tool_calls", [])
    if tools is None:
        return not recorded
    return all(tools.call(c["name"], c["arguments"]) == c["output"] for c in recorded)


@dataclass
class Scout:
    """One build's Scout session. `caller` is None when replaying from the cache.

    A live session reuses successful answers in `prior` for identical requests, so a re-run after
    a network failure only repeats the failed calls. The budget counts model responses, checked
    before each conversation.
    """

    caller: Caller | None
    cache: dict[str, Record]
    budget: int
    prior: dict[str, Record] = field(default_factory=dict)
    calls: int = 0
    reader: Reader | None = None  # P12: reads each Scout sentence back; None skips the check

    def ask(
        self, request: dict[str, Any], mode: Mode, tools: ToolBox | None
    ) -> tuple[str, Record | None]:
        """Outcome label and the cached or live record for one request."""
        if self.calls >= self.budget:
            return "no_budget", None
        key = cache_key(request, mode)
        stored = self.cache.get(key) if self.caller is None else self.prior.get(key)
        if stored is not None and "error" not in stored:
            self.calls += int(stored.get("rounds", 1))
            if not replay_ok(stored, tools):
                return "replay_mismatch", stored
            self.cache[key] = stored
            return "ok", stored
        if self.caller is None:
            return ("not_cached", None) if stored is None else ("call_failed", stored)
        return self._live(key, request, mode, tools)

    def _live(
        self, key: str, request: dict[str, Any], mode: Mode, tools: ToolBox | None
    ) -> tuple[str, Record | None]:
        assert self.caller is not None
        t0 = time.perf_counter()
        try:
            res = self.caller(json.dumps(request, ensure_ascii=False), mode, tools)
        except Exception as e:  # noqa: BLE001 - any failure falls back to the rules for this moment
            print(f"  Scout call failed: {type(e).__name__}: {str(e)[:200]}")
            self.calls += 1
            self.cache[key] = {"request": request, "mode": mode, "error": type(e).__name__}
            return "call_failed", self.cache[key]
        self.calls += res.rounds
        self.cache[key] = {
            "request": request,
            "mode": mode,
            "response_text": res.text,
            "path": res.path,
            "model": res.model,
            "ms": round(1000 * (time.perf_counter() - t0)),
            "rounds": res.rounds,
            "tool_calls": [asdict(c) for c in res.tool_calls],
        }
        return "ok", self.cache[key]


# ---- one attempt -----------------------------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    fact: Fact | None  # verified draft, or None
    steps: list[Step]
    output: Any = None  # what to show the Scout as previous_output on a retry
    feedback: str | None = None  # set when a retry may help


def toolbox(m: Moment, match: Match, mode: Mode, s: Settings) -> ToolBox | None:
    if mode == "handed":
        return None
    return ToolBox(match, MetricContext.of(s), float(m["created_t"]), s.agents.max_events)


def attempt(
    scout: Scout,
    request: dict[str, Any],
    mode: Mode,
    m: Moment,
    match: Match,
    league: League,
    fact_id: str,
    n: int,
    s: Settings,
) -> Attempt:
    """One Scout conversation, then the verifier."""
    outcome, rec = scout.ask(request, mode, toolbox(m, match, mode, s))
    if outcome != "ok" or rec is None:
        return Attempt(None, [step("Scout", n, outcome, rec, mode=mode)])
    required = [k["key"] for k in request["numbers"]] if mode == "handed" else None
    try:
        out = parse_scout(rec["response_text"])
        draft = fact_from_output(
            out, m, match, league, fact_id, mode, required, s.router.t0_metrics
        )
    except (ScoutOutputError, ValidationError) as e:
        problem = e.problems if isinstance(e, ScoutOutputError) else str(e)[:600]
        bad = step("Scout", n, f"invalid_output: {problem[:120]}", rec, mode=mode)
        fb = f"Your reply was invalid: {problem}. Reply again following the schema and the rules."
        return Attempt(None, [bad], rec["response_text"], fb)
    steps = [step("Scout", n, "drafted", rec, mode=mode)]
    reading: Reading | None = None
    if draft.claim.metric in s.router.t0_metrics:
        draft = template_wording(draft, m, match, s.verifier)
        steps.append(step("Drafter", n, "template wording (T0)", path="rules"))
    elif scout.reader is not None:  # the template wording of T0 facts is code's, not the Scout's
        read = scout.reader.read(draft.text.canonical_en, match)
        reading = read.reading
        steps.append(read_step(n, read))
    checked = verify(draft, match, league, s, reading)
    steps.append(step("Verifier", n, checked.verification.status, path="rules"))
    retry = None if checked.verification.status in ROUTABLE else feedback(checked)
    return Attempt(checked, steps, as_dict(out), retry)


# ---- the Scout loop ----------------------------------------------------------------------


@dataclass(frozen=True)
class Pending:
    """A first attempt that may be retried once."""

    request: dict[str, Any]
    first: Attempt
    rules_draft: Fact
    memory: list[str]


def request_for(
    m: Moment, match: Match, mode: Mode, memory: dict[str, Any], s: Settings
) -> dict[str, Any]:
    if mode == "handed":
        return handed_request(m, match, s.verifier, memory)
    return explore_request(m, match, memory)


def first_pass(
    scout: Scout,
    m: Moment,
    rules_draft: Fact,
    match: Match,
    league: League,
    s: Settings,
    mode: Mode,
    memory: dict[str, Any],
) -> tuple[Fact, Pending]:
    """The fact as it stands after one attempt (rules if the Scout gave nothing usable)."""
    request = request_for(m, match, mode, memory, s)
    a = attempt(scout, request, mode, m, match, league, rules_draft.fact_id, 1, s)
    pending = Pending(request, a, rules_draft, memory_ids(memory))
    if a.fact is None:
        return verify_rules(rules_draft, match, league, s, a.steps, pending.memory), pending
    return with_audit(a.fact, a.steps, pending.memory), pending


def second_pass(
    scout: Scout,
    m: Moment,
    current: Fact,
    p: Pending,
    match: Match,
    league: League,
    s: Settings,
    mode: Mode,
) -> Fact:
    """One retry for a rejected claim or an invalid reply. Otherwise the fact is unchanged."""
    if p.first.feedback is None:
        return current
    retry_req = {**p.request, "previous_output": p.first.output, "feedback": p.first.feedback}
    a = attempt(scout, retry_req, mode, m, match, league, current.fact_id, 2, s)
    trail = p.first.steps + a.steps
    if a.fact is not None:
        return with_audit(a.fact, trail, p.memory)
    if p.first.fact is None:  # invalid twice (or the retry failed): the rules draft stands
        return verify_rules(p.rules_draft, match, league, s, trail, p.memory)
    return with_audit(p.first.fact, trail, p.memory)


def run_scout(
    scout: Scout,
    moments: list[Moment],
    rules_drafts: list[Fact],
    match: Match,
    league: League,
    s: Settings,
    mode: Mode = "handed",
) -> list[Fact]:
    """Pass 1: a Scout call per moment, in time order, each with the memory of earlier facts.
    Pass 2: one retry each for rejected claims and invalid replies, while the budget lasts.
    A moment the Scout cannot draft (drafter.scout_can_draft) keeps its rules draft and stays out
    of the Scout's memory."""
    done: list[Fact] = []
    pending: list[Pending] = []
    asked = [(m, d) for m, d in zip(moments, rules_drafts, strict=True) if scout_can_draft(m)]
    for m, d in asked:
        memory = memory_for(done, float(m["created_t"]), match, s.agents.memory_size)
        fact, p = first_pass(scout, m, d, match, league, s, mode, memory)
        done.append(fact)
        pending.append(p)
    scouted = iter(
        second_pass(scout, m, f, p, match, league, s, mode)
        for (m, _), f, p in zip(asked, done, pending, strict=True)
    )
    return [
        next(scouted) if scout_can_draft(m) else verify_rules(d, match, league, s)
        for m, d in zip(moments, rules_drafts, strict=True)
    ]


def scout_metrics(facts: list[Fact]) -> dict[str, int]:
    """How each moment's fact came about, read from the audit trails."""
    out = {"first_pass": 0, "after_retry": 0, "rejected": 0, "rules": 0}
    for f in facts:
        steps = f.audit.steps if f.audit else []
        drafted = [s for s in steps if s.agent == "Scout" and s.outcome == "drafted"]
        if not drafted:
            out["rules"] += 1
        elif f.verification.status not in ROUTABLE:
            out["rejected"] += 1
        else:
            out["after_retry" if drafted[-1].attempt > 1 else "first_pass"] += 1
    return out


# ---- the live Foundry client (only with --foundry) ---------------------------------------


def _text_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "scout_output",
        "schema": output_schema(),
        "strict": True,
    }


def _function_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "strict": True, **spec} for spec in TOOL_SPECS]


class FoundryCaller:
    """Calls the Scout prompt agent for each mode; falls back to plain responses calls with the
    same instructions, schema and tools if the agent API is unavailable."""

    def __init__(self, cfg: Agents) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        self.model, self.max_rounds = os.environ["FOUNDRY_MODEL"], cfg.max_tool_rounds
        project = AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
            allow_preview=True,
        )
        self.opts = {"timeout": cfg.timeout_s, "max_retries": 0}
        self.plain = project.get_openai_client(**self.opts)
        self.agents: dict[str, Any] = {}
        for mode in ("handed", "explore"):
            name = cfg.scout_name if mode == "handed" else f"{cfg.scout_name}-explore"
            try:
                project.agents.create_version(name, definition=self._definition(mode))
                self.agents[mode] = project.get_openai_client(agent_name=name, **self.opts)
            except Exception as e:  # noqa: BLE001 - the plain responses path still works
                print(
                    f"  Scout agent {mode} unavailable ({type(e).__name__}: {str(e)[:200]}); using plain responses"
                )

    def _definition(self, mode: str) -> Any:
        from azure.ai.projects.models import (
            FunctionTool,
            PromptAgentDefinition,
            PromptAgentDefinitionTextOptions,
            TextResponseFormatJsonSchema,
        )

        tools = (
            [FunctionTool(strict=True, **spec) for spec in TOOL_SPECS]
            if mode == "explore"
            else None
        )
        fmt = TextResponseFormatJsonSchema(name="scout_output", schema=output_schema(), strict=True)
        return PromptAgentDefinition(
            model=self.model,
            instructions=INSTRUCTIONS[mode],
            tools=tools,
            text=PromptAgentDefinitionTextOptions(format=fmt),
        )

    def __call__(self, user: str, mode: Mode, tools: ToolBox | None) -> CallResult:
        agent = self.agents.get(mode)
        if agent is not None:
            try:
                return self._converse(agent, {}, "foundry_agent", user, tools)
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) is None:
                    raise  # network trouble, not the agent API: fail this call only
                print(
                    f"  Scout agent call failed ({type(e).__name__}: {str(e)[:200]}); using plain responses for {mode}"
                )
                self.agents.pop(mode)
                if tools is not None:
                    tools.calls.clear()
        kw: dict[str, Any] = {
            "model": self.model,
            "instructions": INSTRUCTIONS[mode],
            "text": {"format": _text_format()},
        }
        if mode == "explore":
            kw["tools"] = _function_tools()
        return self._converse(self.plain, kw, "foundry_responses", user, tools)

    def _converse(
        self, client: Any, kw: dict[str, Any], path: str, user: str, tools: ToolBox | None
    ) -> CallResult:
        """Run the conversation: answer each round of function calls until a final reply."""
        resp, rounds = client.responses.create(input=user, **kw), 1
        while True:
            calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
            if not calls:
                return CallResult(
                    resp.output_text, path, self.model, rounds, list(tools.calls) if tools else []
                )
            if tools is None or rounds >= self.max_rounds:
                raise RuntimeError(f"Scout asked for tools after {rounds} responses")
            outputs = [
                {
                    "type": "function_call_output",
                    "call_id": c.call_id,
                    "output": tools.call(c.name, c.arguments),
                }
                for c in calls
            ]
            resp = client.responses.create(input=outputs, previous_response_id=resp.id, **kw)
            rounds += 1
