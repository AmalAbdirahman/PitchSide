"""Ask PitchSide (R5): suggested questions answered at build time by the Analyst, checked by code.

For each question in data/ask_questions.json the Analyst, an agent on Microsoft Foundry, answers
with the Scout's read-only tools (app/scout_tools.py), capped at the question's replay time. Code
then checks every number, name, window and evidence id against the tool calls (app/ask_checks.py).
A failed answer goes back once with the reasons; a second failure, or a question the tools cannot
answer, is stored as a refusal plus the closest verified fact, if one exists. Nothing is shown that
the checks did not pass.

Every request, response and tool call is cached in data/ask_cache.json. Without --foundry the build
replays the cache, re-running each recorded tool call; a question with no cached answer is stored as
"not_built". The site only reads docs/data/ask.json; it never calls a model.
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

from app.agents_foundry import CallResult, replay_ok
from app.ask_checks import check_answer, parse_calls
from app.ask_prompts import ANALYST
from app.ask_schema import (
    REFUSAL,
    AnalystOutput,
    AskAnswer,
    AskAttempt,
    AskCheck,
    AskNumber,
    ClosestFact,
    Question,
    ScoutOutputError,
    analyst_schema,
    parse_analyst,
)
from app.foundry_loop import converse
from app.scout_tools import TOOL_SPECS, ToolBox, ToolCall
from core.config import ROOT, MetricContext, Settings
from core.events import Match
from core.facts import Fact

NOT_BUILT = ("not_cached", "no_budget", "replay_mismatch", "call_failed")
Record = dict[str, Any]  # one cache entry: the request, the raw response and the tool calls
CACHE_PATH = ROOT / "data" / "ask_cache.json"
QUESTIONS_PATH = ROOT / "data" / "ask_questions.json"
AnalystCaller = Callable[[str, ToolBox], CallResult]  # user JSON, tools -> result
NOTE = (
    "Suggested answers were generated at build time by the Analyst agent and checked by the "
    "verifier. Synthetic match data only."
)


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    return [Question.model_validate(q) for q in json.loads(path.read_text("utf-8"))["questions"]]


def ask_request(q: Question, match: Match, retry: tuple[str, str] | None = None) -> Record:
    """What the Analyst sees: the question, its time, the clock, teams and roster."""
    clock = match.meta.clock
    request: Record = {
        "question": q.text,
        "now_t": q.at_t,
        "now_minute": clock.minute(q.at_t),
        "match_clock": {"half_starts": list(clock.period_starts), "end_t": clock.end_t},
        "teams": {k: t.name for k, t in match.meta.teams.items()},
        "roster": match.meta.names(),
    }
    if retry is not None:
        request["previous_output"], request["feedback"] = retry
    return request


def cache_key(request: Record) -> str:
    blob = json.dumps(
        {
            "agent": "Analyst",
            "instructions": ANALYST,
            "schema": analyst_schema(),
            "tools": TOOL_SPECS,
            "input": request,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_cache(path: Path = CACHE_PATH) -> dict[str, Record]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.exists() else {}


def save_cache(entries: dict[str, Record], path: Path = CACHE_PATH) -> None:
    doc = {
        "note": "Analyst requests, responses and tool calls from a --foundry build. Synthetic match data only.",
        "entries": entries,
    }
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


@dataclass
class Analyst:
    """One build's Analyst session. `caller` is None when replaying the cache. A live session
    reuses successful answers in `prior` for identical requests. The budget counts responses."""

    caller: AnalystCaller | None
    cache: dict[str, Record]
    budget: int
    prior: dict[str, Record] = field(default_factory=dict)
    calls: int = 0

    def ask(self, request: Record, tools: ToolBox) -> tuple[str, Record | None]:
        if self.calls >= self.budget:
            return "no_budget", None
        key = cache_key(request)
        stored = self.cache.get(key) if self.caller is None else self.prior.get(key)
        if stored is not None and "error" not in stored:
            self.calls += int(stored.get("rounds", 1))
            if not replay_ok(stored, tools):
                return "replay_mismatch", stored
            self.cache[key] = stored
            return "ok", stored
        if self.caller is None:
            return "not_cached", None
        return self._live(key, request, tools)

    def _live(self, key: str, request: Record, tools: ToolBox) -> tuple[str, Record | None]:
        assert self.caller is not None
        t0 = time.perf_counter()
        try:
            res = self.caller(json.dumps(request, ensure_ascii=False), tools)
        except Exception as e:  # noqa: BLE001 - this question is stored as not built; the build goes on
            print(f"  Analyst call failed: {type(e).__name__}: {str(e)[:200]}")
            self.calls += 1
            return "call_failed", None
        self.calls += res.rounds
        self.cache[key] = {
            "request": request,
            "response_text": res.text,
            "path": res.path,
            "model": res.model,
            "ms": round(1000 * (time.perf_counter() - t0)),
            "rounds": res.rounds,
            "tool_calls": [asdict(c) for c in res.tool_calls],
        }
        return "ok", self.cache[key]


# ---- one question ----------------------------------------------------------------------


@dataclass(frozen=True)
class Tried:
    """One attempt: what to store, and what the next attempt needs."""

    attempt: AskAttempt
    out: AnalystOutput | None = None
    numbers: list[AskNumber] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    retry: tuple[str, str] | None = None  # previous output and feedback
    model: str | None = None


def attempt(
    n: int, q: Question, match: Match, analyst: Analyst, s: Settings, retry: tuple[str, str] | None
) -> Tried:
    tools = ToolBox(match, MetricContext.of(s), q.at_t, s.agents.max_events)
    label, rec = analyst.ask(ask_request(q, match, retry), tools)
    if label != "ok" or rec is None:
        outcome = label if label in NOT_BUILT else "not_cached"
        return Tried(AskAttempt(attempt=n, outcome=outcome, checks=[]))  # type: ignore[arg-type]
    calls, text, model = (
        [ToolCall(**c) for c in rec.get("tool_calls", [])],
        rec["response_text"],
        rec.get("model"),
    )
    try:
        out = parse_analyst(text)
    except ScoutOutputError as e:
        bad = AskCheck(name="output_valid", passed=False, detail=e.problems)
        return Tried(
            AskAttempt(attempt=n, outcome="invalid", checks=[bad]),
            calls=calls,
            retry=(text, e.problems),
            model=model,
        )
    if not out.answerable:
        return Tried(
            AskAttempt(attempt=n, outcome="unanswerable", checks=[]), out, calls=calls, model=model
        )
    checks, numbers = check_answer(out, calls, match, q.at_t, s.verifier)
    if all(c.passed for c in checks):
        return Tried(
            AskAttempt(attempt=n, outcome="verified", checks=checks),
            out,
            numbers,
            calls,
            model=model,
        )
    feedback = "; ".join(f"{c.name}: {c.detail}" for c in checks if not c.passed)
    return Tried(
        AskAttempt(attempt=n, outcome="failed", checks=checks),
        out,
        numbers,
        calls,
        (text, feedback),
        model,
    )


def closest_fact(facts: list[Fact], at_t: float, calls: list[ToolCall]) -> ClosestFact | None:
    """The latest verified fact by the question's time about a subject or metric the Analyst
    computed; None if there is none."""
    computed = [c for c in parse_calls(calls) if c.name == "compute_metric"]
    subjects = {str(c.args.get("subject_id")) for c in computed}
    metrics = {str(c.args.get("metric")) for c in computed}
    near = [
        f
        for f in facts
        if f.verification.status in ("verified", "corrected")
        and f.created_t <= at_t
        and (f.claim.subject.id in subjects or f.claim.metric in metrics)
    ]
    if not near:
        return None
    best = max(near, key=lambda f: (f.created_t, f.fact_id))
    return ClosestFact(fact_id=best.fact_id, text=best.text.canonical_en)


def answer_question(
    q: Question, match: Match, facts: list[Fact], analyst: Analyst, s: Settings
) -> AskAnswer:
    tries: list[Tried] = []
    retry = None
    for n in range(1, s.ask.retries + 2):
        t = attempt(n, q, match, analyst, s, retry)
        tries.append(t)
        if t.attempt.outcome not in ("failed", "invalid"):
            break
        retry = t.retry
    last = tries[-1]
    blank = AskAnswer(
        question_id=q.id,
        question=q.text,
        at_t=q.at_t,
        at_minute=match.meta.clock.minute(q.at_t),
        status="not_built",
        text="",
        numbers=[],
        evidence_ids=[],
        closest_fact=None,
        attempts=[t.attempt for t in tries],
        model=next((t.model for t in reversed(tries) if t.model), None),
    )
    if last.attempt.outcome == "verified" and last.out is not None:
        verified = {"text": last.out.text, "evidence_ids": last.out.evidence_ids}
        return blank.model_copy(update={"status": "verified", "numbers": last.numbers, **verified})
    if len(tries) == 1 and last.attempt.outcome in NOT_BUILT:
        return blank
    near = closest_fact(facts, q.at_t, [c for t in tries for c in t.calls])
    return blank.model_copy(update={"status": "refused", "text": REFUSAL, "closest_fact": near})


def build_ask(
    match: Match, facts: list[Fact], s: Settings, analyst: Analyst, questions: list[Question]
) -> dict[str, Any]:
    """The content of docs/data/ask.json."""
    answers = [answer_question(q, match, facts, analyst, s) for q in questions]
    return {"note": NOTE, "answers": [a.model_dump(mode="json") for a in answers]}


def make_analyst(foundry: bool, s: Settings) -> Analyst:
    if foundry:
        return Analyst(FoundryAnalyst(s), {}, s.ask.max_calls, prior=load_cache())
    return Analyst(None, load_cache(), s.ask.max_calls)


# ---- the live Foundry client (only with --foundry) ---------------------------------------


class FoundryAnalyst:
    """Calls the Analyst prompt agent; falls back to plain responses calls with the same
    instructions, schema and tools if the agent API is unavailable."""

    def __init__(self, s: Settings) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        self.model, self.max_rounds = os.environ["FOUNDRY_MODEL"], s.agents.max_tool_rounds
        project = AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
            allow_preview=True,
        )
        opts: dict[str, Any] = {"timeout": s.agents.timeout_s, "max_retries": 0}
        self.plain = project.get_openai_client(**opts)
        self.agent: Any = None
        try:
            project.agents.create_version(s.ask.analyst_name, definition=self._definition())
            self.agent = project.get_openai_client(agent_name=s.ask.analyst_name, **opts)
        except Exception as e:  # noqa: BLE001 - the plain responses path still works
            print(f"  Analyst agent unavailable ({type(e).__name__}); using plain responses")

    def _definition(self) -> Any:
        from azure.ai.projects.models import (
            FunctionTool,
            PromptAgentDefinition,
            PromptAgentDefinitionTextOptions,
            TextResponseFormatJsonSchema,
        )

        tools: list[Any] = [FunctionTool(strict=True, **spec) for spec in TOOL_SPECS]
        fmt = TextResponseFormatJsonSchema(
            name="analyst_output", schema=analyst_schema(), strict=True
        )
        return PromptAgentDefinition(
            model=self.model,
            instructions=ANALYST,
            tools=tools,
            text=PromptAgentDefinitionTextOptions(format=fmt),
        )

    def __call__(self, user: str, tools: ToolBox) -> CallResult:
        if self.agent is not None:
            try:
                c = converse(self.agent, {}, user, tools, self.max_rounds, "Analyst")
                return CallResult(c.text, "foundry_agent", self.model, c.rounds, c.tool_calls)
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) is None:
                    raise  # network trouble, not the agent API: fail this call only
                print(f"  Analyst agent call failed ({type(e).__name__}); using plain responses")
                self.agent = None
                tools.calls.clear()
        kw: dict[str, Any] = {
            "model": self.model,
            "instructions": ANALYST,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "analyst_output",
                    "schema": analyst_schema(),
                    "strict": True,
                }
            },
            "tools": [{"type": "function", "strict": True, **spec} for spec in TOOL_SPECS],
        }
        c = converse(self.plain, kw, user, tools, self.max_rounds, "Analyst")
        return CallResult(c.text, "foundry_responses", self.model, c.rounds, c.tool_calls)
