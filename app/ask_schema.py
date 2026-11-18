"""Ask PitchSide (R5): the Analyst's output contract and the stored answer models.

The Analyst returns AnalystOutput (strict structured output, generated from these models, as for
the Scout). Code checks it (app/ask_checks.py) and the build stores one AskAnswer per question in
docs/data/ask.json.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.scout_schema import ScoutOutputError, _Out, strict_schema

MAX_ANSWER = 300
REFUSAL = "I can't verify that from the event log."

# Which part of a recorded tool call a number comes from.
NumberField = Literal[
    "value", "n", "k", "total", "start_minute", "end_minute", "event_minute", "event_xg"
]


class OutAskNumber(_Out):
    key: str  # a short label, e.g. "shots"
    value: float  # as written in the text
    call: int  # index of the tool call that produced it, 0 = first call
    field: NumberField
    event_id: int | None  # for event_minute and event_xg
    scale: Literal[1, 100]  # 100 when the text gives a share as a per cent


class AnalystOutput(_Out):
    answerable: bool  # false when the tools cannot answer the question
    text: str
    numbers: list[OutAskNumber]
    evidence_ids: list[int]


def analyst_schema() -> dict[str, Any]:
    return strict_schema(AnalystOutput.model_json_schema())  # type: ignore[no-any-return]


def parse_analyst(text: str) -> AnalystOutput:
    """Validate the reply; anything else is a ScoutOutputError with the problems, for the retry."""
    try:
        out = AnalystOutput.model_validate_json(text)
    except ValidationError as e:
        errs = "; ".join(
            f"{'.'.join(map(str, x['loc'])) or 'output'}: {x['msg']}" for x in e.errors()
        )
        raise ScoutOutputError(errs[:600]) from e
    if len(out.text) > MAX_ANSWER:
        raise ScoutOutputError(f"text is longer than {MAX_ANSWER} characters")
    return out


# ---- what the build stores ------------------------------------------------------------


class _Stored(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AskCheck(_Stored):
    name: Literal[
        "output_valid", "numbers_listed", "numbers_match", "entities", "window", "evidence"
    ]
    passed: bool
    detail: str


class AskNumber(_Stored):
    key: str
    value: float
    verified: bool
    source: str  # the tool call and field it was checked against, in words


class AskAttempt(_Stored):
    attempt: int
    outcome: Literal[
        "verified",
        "failed",
        "unanswerable",
        "invalid",
        "not_cached",
        "no_budget",
        "replay_mismatch",
        "call_failed",
    ]
    checks: list[AskCheck]


class ClosestFact(_Stored):
    fact_id: str
    text: str


class Question(_Stored):
    id: str
    text: str
    at_t: float  # the replay time the question is asked at; tools never look past it


class AskAnswer(_Stored):
    question_id: str
    question: str
    at_t: float
    at_minute: int
    status: Literal["verified", "refused", "not_built"]
    text: str
    numbers: list[AskNumber]
    evidence_ids: list[int]
    closest_fact: ClosestFact | None
    attempts: list[AskAttempt]
    model: str | None


__all__ = [
    "REFUSAL",
    "AnalystOutput",
    "AskAnswer",
    "AskAttempt",
    "AskCheck",
    "AskNumber",
    "ClosestFact",
    "OutAskNumber",
    "Question",
    "ScoutOutputError",
    "analyst_schema",
    "parse_analyst",
]
