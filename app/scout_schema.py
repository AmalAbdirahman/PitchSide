"""The Scout's output contract (P11): Pydantic models, the JSON Schema the model must follow, and
a parser that turns anything else into a typed error.

The same models give the schema sent to Foundry (strict structured output) and validate what comes
back, so the contract cannot drift. Validation is strict: "71" is not 71.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from core.facts import MetricName

MAX_SENTENCE = 200


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class OutSubject(_Out):
    type: Literal["team", "player", "match"]
    id: str


class OutWindow(_Out):
    start_t: float
    end_t: float


class OutClaim(_Out):
    subject: OutSubject
    metric: MetricName
    window: OutWindow
    comparator: Literal["eq", "gt", "lt"]
    value: float


class OutNumber(_Out):
    key: str
    value: float
    unit: str | None


class OutText(_Out):
    canonical_en: str
    numbers: list[OutNumber]


class ScoutOutput(_Out):
    claim: OutClaim
    text: OutText
    evidence_ids: list[int]


class ScoutOutputError(ValueError):
    """The Scout's reply is not a valid ScoutOutput. `problems` is fed back on the retry."""

    def __init__(self, problems: str) -> None:
        super().__init__(problems)
        self.problems = problems


def strict_schema(node: Any) -> Any:
    """Make a Pydantic schema fit OpenAI's strict mode: drop title and default, and list every
    property as required (a field with a None default must still be sent, as null)."""
    if isinstance(node, dict):
        out = {k: strict_schema(v) for k, v in node.items() if k not in ("title", "default")}
        if "properties" in out:
            out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [strict_schema(v) for v in node]
    return node


def output_schema() -> dict[str, Any]:
    """JSON Schema for structured output, generated from ScoutOutput."""
    return strict_schema(ScoutOutput.model_json_schema())  # type: ignore[no-any-return]


def parse_scout(text: str) -> ScoutOutput:
    """Validate the reply. No fence stripping: structured output returns bare JSON or it is wrong."""
    try:
        out = ScoutOutput.model_validate_json(text)
    except ValidationError as e:
        errs = "; ".join(
            f"{'.'.join(map(str, x['loc'])) or 'output'}: {x['msg']}" for x in e.errors()
        )
        raise ScoutOutputError(errs[:600]) from e
    if len(out.text.canonical_en) > MAX_SENTENCE:
        raise ScoutOutputError(f"text.canonical_en is longer than {MAX_SENTENCE} characters")
    return out


def as_dict(out: ScoutOutput) -> dict[str, Any]:
    return json.loads(out.model_dump_json())  # type: ignore[no-any-return]
