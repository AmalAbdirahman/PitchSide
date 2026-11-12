"""Domain models: the schema is generated from them, and they serialise exactly as the bundle needs."""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.build_bundle import build
from core.config import ROOT, Settings
from core.events import Event, Match
from core.facts import Correction, Fact, TextNumber, fact_schema, validate_fact
from tests.conftest import SeededClaim

COMMITTED = json.loads((ROOT / "fact.schema.json").read_text(encoding="utf-8"))


def test_committed_schema_is_generated_from_the_models() -> None:
    assert COMMITTED == fact_schema(), "run: python scripts/write_schema.py"
    Draft202012Validator.check_schema(COMMITTED)


def test_bundle_facts_validate_against_the_committed_schema(settings: Settings) -> None:
    hero, _ = build(settings)
    validator = Draft202012Validator(COMMITTED)
    for fact in hero["facts"] + hero["log"]:
        assert not list(validator.iter_errors(fact)), fact["fact_id"]


def test_seeded_facts_round_trip_to_identical_json(seeded: list[SeededClaim]) -> None:
    raw = json.loads((ROOT / "tests" / "seeded_claims.json").read_text(encoding="utf-8"))
    for entry, model in zip(raw, seeded, strict=True):
        assert json.dumps(model.fact.to_dict()) == json.dumps(entry["fact"])


def test_ints_stay_ints() -> None:
    assert TextNumber(key="value", value=71).model_dump() == {
        "key": "value",
        "value": 71,
        "unit": None,
    }
    assert isinstance(TextNumber(key="value", value=71).value, int)
    assert isinstance(TextNumber(key="value", value=0.32).value, float)


def test_correction_uses_from_in_json() -> None:
    c = Correction.model_validate({"field": "f", "from": "a", "to": "b", "reason": "r"})
    assert c.from_ == "a"
    assert c.model_dump() == {"field": "f", "from": "a", "to": "b", "reason": "r"}


def _fact(seeded: list[SeededClaim]) -> Fact:
    return seeded[0].fact.model_copy(deep=True)


def test_text_longer_than_280_is_rejected(seeded: list[SeededClaim]) -> None:
    with pytest.raises(ValidationError, match="canonical_en"):
        _fact(seeded).text.canonical_en = "x" * 281


def test_unknown_audit_path_and_bad_fact_id_are_rejected(seeded: list[SeededClaim]) -> None:
    d = _fact(seeded).to_dict()
    d["audit"] = {"steps": [{"agent": "Scout", "attempt": 1, "outcome": "x", "path": "elsewhere"}]}
    with pytest.raises(ValidationError, match="path"):
        Fact.model_validate(d)
    d.pop("audit")
    d["fact_id"] = "f_1"
    with pytest.raises(ValidationError, match="fact_id"):
        Fact.model_validate(d)


def test_strict_validation_rejects_a_number_as_text(seeded: list[SeededClaim]) -> None:
    d = _fact(seeded).to_dict()
    d["claim"]["value"] = "71"
    with pytest.raises(ValidationError):
        Fact.model_validate(d, strict=True)


def test_validate_fact_catches_in_place_list_edits(seeded: list[SeededClaim]) -> None:
    fact = _fact(seeded)
    fact.evidence.event_ids.clear()
    with pytest.raises(ValidationError, match="event_ids"):
        validate_fact(fact)


def test_probability_bounds_are_enforced(seeded: list[SeededClaim]) -> None:
    d = _fact(seeded).to_dict()
    d["impact"] = {"delta": 1.5, "method": "poisson_v1"}
    with pytest.raises(ValidationError, match="delta"):
        Fact.model_validate(d)


def test_match_loads_and_rejects_unknown_event_fields(match: Match) -> None:
    assert match.meta.team_name("NOR") == "Northbridge"
    assert match.meta.team_of(match.events[0].player_id) == match.events[0].team
    row: dict[str, Any] = match.events[0].model_dump(exclude_none=True) | {"weather": "rain"}
    with pytest.raises(ValidationError, match="weather"):
        Event.model_validate(row)
