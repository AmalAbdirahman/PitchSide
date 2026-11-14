from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

from core.config import ROOT, Settings, load
from core.events import Match
from core.facts import Fact
from core.sources import League, SyntheticSource

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


def load_league() -> League:
    """The synthetic league's priors, as the build loads them."""
    return SyntheticSource().load()[1]


LEAGUE = load_league()


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


@pytest.fixture(scope="session")
def replayed() -> tuple[Any, Any, Any]:
    """P14: the whole synthetic match through the live worker on the in-memory ports (no Docker).
    Returns (bus, state, audit)."""
    import asyncio

    from live.ports import InMemoryAudit, InMemoryBus, InMemoryState
    from live.replayer import replay
    from live.worker import Worker, scout_for

    s = load()
    bus, state, audit = InMemoryBus(), InMemoryState(), InMemoryAudit()
    match, league = SyntheticSource().load()

    async def run() -> None:
        worker = Worker(bus, state, audit, s, scout_for(s))
        await asyncio.gather(replay(bus, match, league, 0), worker.run(once=True))

    asyncio.run(run())
    return bus, state, audit
