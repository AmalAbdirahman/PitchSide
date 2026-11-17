"""A fact and its parts as Pydantic models. `fact.schema.json` is generated from these.

Field order is the order the bundle serialises, so changing it changes hero.json.
Models fed from Scout output (Subject, Window, Observed, Claim, TextNumber) allow
extra keys, as the hand-written schema did; everything code builds forbids them.
Run `python scripts/write_schema.py` after changing a model.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, WithJsonSchema, model_validator
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from core.events import Number

# Bounded numbers: Field enforces the bound, WithJsonSchema writes it into the schema
# (pydantic would otherwise emit a non-standard "ge"/"le" for an int | float union).
Probability = Annotated[
    int | float,
    Field(ge=0, le=1),
    WithJsonSchema({"type": "number", "minimum": 0, "maximum": 1}),
]
Delta = Annotated[
    int | float,
    Field(ge=-1, le=1),
    WithJsonSchema({"type": "number", "minimum": -1, "maximum": 1}),
]
NonNegative = Annotated[int | float, Field(ge=0), WithJsonSchema({"type": "number", "minimum": 0})]

# Metrics the Scout and the Reader may name. Their output schemas use this list, and both agent
# caches key on those schemas, so growing it changes every cached request: extend it only together
# with a --foundry run. The verifier's list, MetricName, may hold more.
AgentMetricName = Literal[
    "goals",
    "shots",
    "xg_sum",
    "attack_share_left",
    "attack_share_centre",
    "attack_share_right",
    "pass_accuracy",
    "turnovers_own_third",
    "duels_lost",
    "win_prob",
    "top_shot_speed",
    "xt_added",
    "most_central_player",
    "substitutions",
    "cards",
]
# Every metric a fact may claim; each has a REGISTRY entry (tests/test_metrics.py).
MetricName = Literal[
    AgentMetricName,
    Literal["npxg_sum", "field_tilt", "shots_on_target", "shots_off_target", "shots_blocked"],
]
AGENT_METRICS: frozenset[str] = frozenset(get_args(AgentMetricName))
Hedge = Literal["none", "early", "suggests", "clear"]
Status = Literal["pending", "verified", "corrected", "rejected", "unverifiable"]
Tier = Literal["T0", "T1", "T2"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, serialize_by_alias=True)


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)


# ---- the claim ------------------------------------------------------------------


class Subject(_Open):
    type: Literal["team", "player", "match"]
    id: str


class Window(_Open):
    start_t: Number
    end_t: Number


class Observed(_Open):
    value: Number | None = None
    n: Annotated[int, Field(ge=0)] | None = None


class Claim(_Open):
    subject: Subject
    metric: MetricName
    window: Window
    comparator: Literal["eq", "gt", "lt", "share_gt"]
    value: Number
    baseline: Number | None = None
    observed: Observed | None = None


class TextNumber(_Open):
    key: str
    value: Number
    unit: str | None = None


class FactText(_Strict):
    draft_en: str | None = Field(None, description="Original drafter wording, kept for the log.")
    canonical_en: str = Field(max_length=280)
    numbers: list[TextNumber]


class Evidence(_Strict):
    event_ids: list[int] = Field(min_length=1)
    players: list[str] | None = None
    window: Window | None = None


class Impact(_Strict):
    subject_team: str | None = None
    win_prob_before: Probability | None = None
    win_prob_after: Probability | None = None
    delta: Delta
    method: Literal["poisson_v1"]


class Uncertainty(_Strict):
    """For a share: the Beta posterior's 95% interval and P(share > league share), which set the
    hedge, and the Wilson interval on the raw counts for comparison (docs/methods/hedging.md)."""

    n: Annotated[int, Field(ge=0)]
    ci_method: Literal["beta", "wilson", "none"]
    ci_low: Number | None = None
    ci_high: Number | None = None
    hedge: Hedge
    baseline: Number | None = None  # league share of the lane, 0..1
    p_above: Number | None = None  # posterior P(share > baseline + min_effect)
    wilson_low: Number | None = None
    wilson_high: Number | None = None


# ---- verification, routing, output -------------------------------------------------


class Check(_Strict):
    name: Literal[
        "recompute",
        "evidence_exists",
        "entities",
        "numbers_match",
        "intent_stripped",
        "sample_size",
        "meaning_matches",
    ]
    passed: bool
    detail: str | None = None


class Correction(_Strict):
    field: str
    # "from" in JSON; `from_` in Python, where `from` is a keyword.
    from_: str = Field(validation_alias=AliasChoices("from", "from_"), serialization_alias="from")
    to: str
    reason: str


class Verification(_Strict):
    status: Status
    checks: list[Check]
    corrections: list[Correction] | None = None
    reject_reason: str | None = None


class Risk(_Strict):
    tier: Tier
    reasons: list[str] | None = None


class Approval(_Strict):
    state: Literal[
        "not_routed",
        "queued",
        "veto_window",
        "auto_approved",
        "approved",
        "rejected",
        "vetoed",
        "expired",
    ]
    valid_from_t: Number | None = None
    decided_by: Literal["auto", "producer", "autopilot"] | None = None
    ttl_s: NonNegative | None = None


# ---- broadcast graphics (P13): one typed card per cue, drawn by docs/overlay.html ----------

Layout = Literal["lower_third", "ticker", "stat_comparison", "player_card"]


class LowerThirdCard(_Strict):
    layout: Literal["lower_third"]
    label: str  # who it is about, or "Goal"
    text: str


class TickerCard(_Strict):
    layout: Literal["ticker"]
    text: str


class Side(_Strict):
    """One team in a stat comparison; `shown` is the value as written on screen."""

    team: str
    name: str
    shown: str


class StatComparisonCard(_Strict):
    """The subject team's claimed value beside the other team's, recomputed from the event log
    over the same window by the metric registry."""

    layout: Literal["stat_comparison"]
    label: str
    period: str  # "First half", "Second half" or "" (no digits, so no unchecked numbers)
    subject: str  # team code of the claim's subject
    home: Side
    away: Side
    text: str


class PlayerCard(_Strict):
    layout: Literal["player_card"]
    player_id: str
    name: str
    team: str
    team_name: str
    label: str
    figure: str  # the headline number(s) as written, from the fact's text.numbers
    period: str
    text: str


Card = Annotated[
    LowerThirdCard | TickerCard | StatComparisonCard | PlayerCard, Field(discriminator="layout")
]


class Rendition(_Strict):
    kind: Literal["cue", "caption", "audio_script"]
    lang: Literal["en", "es"]
    text: str
    layout: Layout | None = None
    duration_s: Number | None = None
    start_t: Number | None = None
    numbers_preserved: bool
    card: Card | None = None  # cues only (P13)

    @model_validator(mode="after")
    def _card_matches_layout(self) -> Rendition:
        if self.card is not None and self.card.layout != self.layout:
            raise ValueError(f"card layout {self.card.layout} differs from layout {self.layout}")
        return self


class ReadSubject(_Strict):
    type: Literal["team", "player", "match"]
    id: str


class ReadWindow(_Strict):
    """The window as the sentence states it, in official minutes (code maps it to seconds)."""

    kind: Literal["since_minute", "at_minute", "first_half", "second_half", "unstated"]
    minute: int | None = None


class Reading(_Strict):
    """What the Reader (P12) understood a sentence to claim, from the sentence and roster only.
    `metric` is None when no listed metric says what the sentence says."""

    subject: ReadSubject
    metric: AgentMetricName | None = None
    window: ReadWindow
    value: float


class AuditStep(_Strict):
    agent: Literal["Scout", "Drafter", "Verifier", "Reader"]
    attempt: Annotated[int, Field(ge=1)]
    outcome: str
    path: Literal["foundry_agent", "foundry_responses", "rules"] | None = None
    model: str | None = None
    ms: Annotated[int, Field(ge=0)] | None = None
    mode: Literal["handed", "explore"] | None = None  # Scout steps only (P11)
    tool_calls: Annotated[int, Field(ge=0)] | None = None  # read-only tool calls in this attempt
    reading: Reading | None = None  # Reader steps only (P12)


class Audit(_Strict):
    """Who drafted and checked this fact, in order (SPEC section 19), and the facts the Scout was
    shown as memory (P11)."""

    steps: list[AuditStep]
    memory: list[str] | None = None


class Fact(_Strict):
    """One claim about the match, with its evidence, checks, routing and renditions."""

    fact_id: str = Field(pattern=r"^f_[0-9]{6}$")
    match_id: str
    kind: Literal["score_event", "stat", "moment", "milestone", "tactical", "recap"]
    created_t: Number = Field(description="Match clock seconds when proposed.")
    injected: bool = Field(
        False, description="True if the drafter deliberately injected an error, for eval only."
    )
    claim: Claim
    text: FactText
    evidence: Evidence
    verification: Verification
    approval: Approval
    impact: Impact | None = None
    uncertainty: Uncertainty | None = None
    audit: Audit | None = None
    risk: Risk | None = None
    renditions: list[Rendition] | None = None

    def to_dict(self) -> dict[str, Any]:
        """The JSON form written to the bundle: absent fields are left out, not null."""
        return self.model_dump(by_alias=True, exclude_none=True)


def validate_fact(fact: Fact) -> None:
    """Re-check a fact after in-place edits. Raises pydantic.ValidationError (a ValueError)."""
    Fact.model_validate(fact.to_dict(), strict=True)


# ---- schema generation --------------------------------------------------------------


class _SchemaGen(GenerateJsonSchema):
    """Optional fields are omitted when empty (never null), so the schema has no nulls."""

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False

    def nullable_schema(self, schema: core_schema.NullableSchema) -> JsonSchemaValue:
        return self.generate_inner(schema["schema"])

    def default_schema(self, schema: core_schema.WithDefaultSchema) -> JsonSchemaValue:
        out = super().default_schema(schema)
        if "default" in out and out["default"] is None:
            del out["default"]
        return out


def fact_schema() -> dict[str, Any]:
    """JSON Schema for a serialised fact, generated from the models."""
    body = Fact.model_json_schema(by_alias=True, schema_generator=_SchemaGen, mode="serialization")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "fact.schema.json",
    } | body
