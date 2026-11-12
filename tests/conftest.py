from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from core.config import ROOT, Settings, load
from core.events import Match
from core.facts import Fact

SEEDED_PATH = ROOT / "tests" / "seeded_claims.json"


class SeededClaim(BaseModel):
    """One entry of tests/seeded_claims.json: a draft fact and what the verifier should do."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    category: str
    expect: str
    fact: Fact


_SEEDED = TypeAdapter(list[SeededClaim])


def load_match() -> Match:
    return Match.model_validate_json((ROOT / "data" / "events.json").read_text(encoding="utf-8"))


def load_seeded() -> list[SeededClaim]:
    return _SEEDED.validate_json(SEEDED_PATH.read_text(encoding="utf-8"), strict=True)


@pytest.fixture(scope="session")
def match() -> Match:
    return load_match()


@pytest.fixture(scope="session")
def seeded() -> list[SeededClaim]:
    return load_seeded()


def seeded_of(category: str) -> list[SeededClaim]:
    """Fresh copies, so a test that edits a fact cannot affect another."""
    return [c for c in load_seeded() if c.category == category]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load()
