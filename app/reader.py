"""The Reader (P12): a second, independent agent that reads a broadcast sentence back into a claim.

It sees ONLY the sentence and the roster (team codes, names and players) — not the Scout's claim,
the moment, the numbers or the event log — and replies with a core/facts.Reading under a strict
JSON Schema. Code then compares the reading with the Scout's claim (app/meaning.py); a mismatch
fails the verifier's meaning_matches check and sends the Scout its one retry.

Like the Scout it runs at build time only. Every request and reply is cached in
data/reader_cache.json, so a build without --foundry replays it with no network. Azure packages
are imported only by FoundryReader.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.scout_schema import strict_schema
from core.config import ROOT, Agents
from core.events import Match
from core.facts import Reading

Record = dict[str, Any]
CACHE_PATH = ROOT / "data" / "reader_cache.json"

INSTRUCTIONS = """You are Reader, a checking agent in a football match control room. The match is synthetic.
The user message is JSON: one broadcast sentence and the roster (team codes and names, player ids and
names). Write down, as a structured claim, exactly what the sentence states. Read it literally: do not
guess what the writer meant, and do not fix it.
- subject: who the claim is about. type "team" with the team code, "player" with the player id from the
  roster, or "match" with id "match" when it is about the whole match (e.g. "the match's fastest shot").
- metric: the one listed metric the sentence states, or null if none says what the sentence says.
  attack_share_left / _centre / _right: the share of the subject team's OWN attacks that went down that
    lane ("71% of Northbridge attacks went down the right"). A share of the lane's attacks that belong to
    a team ("71% of right-side attacks were Northbridge's") is not this metric.
  pass_accuracy: share of the subject's passes completed.
  turnovers_own_third: times the subject lost the ball in its own defensive third.
  shots, goals, cards, substitutions: counts. xg_sum: expected goals. xt_added: expected threat added.
  top_shot_speed: the speed of the fastest shot (subject match for "the match's fastest", else the
    player or team).
  most_central_player: the player was their team's most central passer (value 1 = the most central).
- window: since_minute (with the minute) for "since the 26th minute"; at_minute for a single minute;
  first_half / second_half; unstated if the sentence gives no time span. minute is null unless the kind
  is since_minute or at_minute.
- value: the claimed value as a number. Shares in percent (71 for 71%; "2 of 3" is 67). xG and xT as
  written. For most_central_player, 1."""

_ROSTER_KEYS = ("name", "players")


def reader_schema() -> dict[str, Any]:
    return strict_schema(Reading.model_json_schema())  # type: ignore[no-any-return]


def roster(match: Match) -> dict[str, Any]:
    """Team codes, names and players: all the Reader knows besides the sentence."""
    names = match.meta.names()
    return {
        code: {"name": t.name, "players": {pid: names[pid] for pid in sorted(t.players)}}
        for code, t in sorted(match.meta.teams.items())
    }


def reader_request(sentence: str, match: Match) -> dict[str, Any]:
    return {"sentence": sentence, "roster": roster(match)}


def parse_reading(text: str) -> Reading:
    """Strict: a number sent as text, an extra key or a missing one is a ValidationError."""
    return Reading.model_validate_json(text, strict=True)


def cache_key(request: dict[str, Any]) -> str:
    blob = {"agent": "Reader", "instructions": INSTRUCTIONS, "schema": reader_schema(), **request}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode("utf-8")).hexdigest()


def load_cache(path: Path = CACHE_PATH) -> dict[str, Record]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.exists() else {}


def save_cache(entries: dict[str, Record], path: Path = CACHE_PATH) -> None:
    doc = {
        "note": "Reader requests and replies from a --foundry run. Synthetic match.",
        "entries": entries,
    }
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class ReadResult:
    text: str
    path: str
    model: str


ReadCaller = Callable[[str], ReadResult]  # user JSON -> reply


@dataclass(frozen=True)
class Outcome:
    """One read: the reading, or why there is none, and the record it came from."""

    reading: Reading | None
    label: str  # "read", "invalid_output", "not_cached", "call_failed", "no_budget"
    rec: Record | None


@dataclass
class Reader:
    """One build's Reader session. `caller` is None when replaying the cache. A live session
    reuses answers in `prior` for identical requests. The budget counts model calls."""

    caller: ReadCaller | None
    cache: dict[str, Record]
    budget: int
    prior: dict[str, Record] | None = None
    calls: int = 0

    def read(self, sentence: str, match: Match) -> Outcome:
        request = reader_request(sentence, match)
        key = cache_key(request)
        source = self.cache if self.caller is None else (self.prior or {})
        rec = source.get(key)
        if rec is None or "error" in rec:
            if self.caller is None:
                return Outcome(None, "not_cached" if rec is None else "call_failed", rec)
            if self.calls >= self.budget:
                return Outcome(None, "no_budget", None)
            rec = self._live(request)
            self.calls += 1
        self.cache[key] = rec
        if "error" in rec:
            return Outcome(None, "call_failed", rec)
        try:
            return Outcome(parse_reading(rec["response_text"]), "read", rec)
        except ValidationError:
            return Outcome(None, "invalid_output", rec)

    def _live(self, request: dict[str, Any]) -> Record:
        assert self.caller is not None
        t0 = time.perf_counter()
        try:
            res = self.caller(json.dumps(request, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 - this sentence goes unread; the build carries on
            print(f"  Reader call failed: {type(e).__name__}: {str(e)[:200]}")
            return {"request": request, "error": type(e).__name__}
        ms = round(1000 * (time.perf_counter() - t0))
        return {
            "request": request,
            "response_text": res.text,
            "path": res.path,
            "model": res.model,
            "ms": ms,
        }


class FoundryReader:
    """Calls the Reader prompt agent; falls back to a plain responses call with the same
    instructions and schema if the agent API is unavailable."""

    def __init__(self, cfg: Agents) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        self.model = os.environ["FOUNDRY_MODEL"]
        project = AIProjectClient(
            endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
            allow_preview=True,
        )
        opts: dict[str, Any] = {"timeout": cfg.timeout_s, "max_retries": 0}
        self.plain = project.get_openai_client(**opts)
        self.agent: Any = None
        try:
            project.agents.create_version(cfg.reader_name, definition=self._definition())
            self.agent = project.get_openai_client(agent_name=cfg.reader_name, **opts)
        except Exception as e:  # noqa: BLE001 - the plain responses path still works
            print(f"  Reader agent unavailable ({type(e).__name__}); using plain responses")

    def _definition(self) -> Any:
        from azure.ai.projects.models import (
            PromptAgentDefinition,
            PromptAgentDefinitionTextOptions,
            TextResponseFormatJsonSchema,
        )

        fmt = TextResponseFormatJsonSchema(name="reading", schema=reader_schema(), strict=True)
        return PromptAgentDefinition(
            model=self.model,
            instructions=INSTRUCTIONS,
            text=PromptAgentDefinitionTextOptions(format=fmt),
        )

    def __call__(self, user: str) -> ReadResult:
        if self.agent is not None:
            try:
                return ReadResult(
                    self.agent.responses.create(input=user).output_text, "foundry_agent", self.model
                )
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status_code", None) is None:
                    raise  # network trouble, not the agent API: fail this call only
                print(f"  Reader agent call failed ({type(e).__name__}); using plain responses")
                self.agent = None
        fmt: Any = {
            "type": "json_schema",
            "name": "reading",
            "schema": reader_schema(),
            "strict": True,
        }
        resp = self.plain.responses.create(
            model=self.model, instructions=INSTRUCTIONS, input=user, text={"format": fmt}
        )
        return ReadResult(resp.output_text, "foundry_responses", self.model)
