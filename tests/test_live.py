"""P14: the live worker on the in-memory bus, state store and audit log. No Docker, no network.

The main test replays the whole synthetic match at full speed through the worker and compares the
audit log with the offline bundle (app/build_bundle.build). tests/test_live_stack.py runs the same
comparison against the docker compose stack.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.build_bundle import build
from core.config import load
from core.sources import SyntheticSource
from live.messages import parse
from live.ports import DecisionTaken, InMemoryAudit, InMemoryBus, InMemoryState
from live.worker import Worker, scout_for

S = load()


def offline_facts() -> dict[str, dict[str, Any]]:
    """The offline bundle's facts and rejected drafts, without the planted test errors."""
    hero, _ = build(S)
    return {f["fact_id"]: f for f in hero["facts"] + hero["log"] if not f.get("injected")}


def audit_facts(rows: list[Any]) -> dict[str, dict[str, Any]]:
    return {r.fact_id: r.payload["fact"] for r in rows if r.kind in ("fact_ready", "fact_rejected")}


def test_full_replay_audit_log_equals_the_offline_bundle(
    replayed: tuple[InMemoryBus, InMemoryState, InMemoryAudit],
) -> None:
    _, _, audit = replayed
    rows = audit.rows
    live = audit_facts(rows)
    offline = offline_facts()
    assert sorted(live) == sorted(offline)
    for fid, f in offline.items():
        assert live[fid] == f, fid
    assert [r.kind for r in rows][0] == "match_start" and rows[-1].kind == "match_end"
    # Facts are recorded in time order: each when its moment became due.
    times = [r.payload["fact"]["created_t"] for r in rows if r.fact_id]
    assert times == sorted(times)


def test_facts_topic_carries_ticks_facts_and_latency(
    replayed: tuple[InMemoryBus, InMemoryState, InMemoryAudit],
) -> None:
    bus, _, audit = replayed
    msgs = [parse(m) for m in bus.topics["facts"]]
    kinds = [m.kind for m in msgs]
    assert kinds.count("tick") == len(SyntheticSource().load()[0].events)
    assert kinds.count("fact") == len(audit_facts(audit.rows)) and kinds[-1] == "end"
    ticks = [m for m in msgs if m.kind == "tick"]
    assert ticks[-1].score == {"NOR": 1, "HAR": 0}
    assert all(m.latency_ms >= 0 for m in msgs if m.kind == "fact")


def test_a_restarted_worker_resumes_from_state_without_duplicates(
    replayed: tuple[InMemoryBus, InMemoryState, InMemoryAudit],
) -> None:
    """Replay the same topic into a fresh worker that shares the old state and audit log."""
    bus, state, audit = replayed
    before = list(audit.rows)

    async def again() -> None:
        await Worker(bus, state, audit, S, scout_for(S)).run(once=True)

    asyncio.run(again())
    facts = [r for r in audit.rows if r.fact_id]
    assert len(facts) == len([r for r in before if r.fact_id])
    assert audit_facts(audit.rows) == audit_facts(before)


def test_audit_keeps_the_first_decision_only() -> None:
    async def go() -> None:
        audit = InMemoryAudit()
        await audit.append("m", "decision", "f_000001", {"decision": "veto"})
        with pytest.raises(DecisionTaken):
            await audit.append("m", "decision", "f_000001", {"decision": "approve"})
        assert await audit.append("m", "fact_ready", "f_000002", {}) == 2
        assert await audit.append("m", "fact_ready", "f_000002", {"again": 1}) == 2

    asyncio.run(go())
