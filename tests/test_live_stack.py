"""P14: the same comparison as tests/test_live.py, against the docker compose stack.

Needs the stack running (`docker compose up -d --build --wait`) and a fresh state
(`docker compose down -v` first if the match was replayed before). The test publishes the match to
Redpanda at PITCHSIDE_STACK_SPEED x (default 20), waits for the worker (in its container) to write
the final whistle to Postgres, then compares the audit log with the offline bundle and checks the
API. Marked `stack`; skipped unless PITCHSIDE_STACK=1. Run: PITCHSIDE_STACK=1 pytest -m stack
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request

import pytest

from core.sources import SyntheticSource
from tests.test_live import audit_facts, offline_facts

pytestmark = pytest.mark.stack
if os.environ.get("PITCHSIDE_STACK") != "1":
    pytest.skip("needs the docker compose stack and PITCHSIDE_STACK=1", allow_module_level=True)

SPEED = float(os.environ.get("PITCHSIDE_STACK_SPEED", "20"))


async def _replay_and_wait() -> list:  # type: ignore[type-arg]
    from live.adapters import kafka_bus, postgres_audit
    from live.replayer import replay

    match, league = SyntheticSource().load()
    bus = await kafka_bus()
    try:
        await replay(bus, match, league, SPEED)
    finally:
        await bus.close()
    audit = await postgres_audit()
    try:
        deadline = time.time() + 300
        while time.time() < deadline:
            rows = await audit.entries(match.meta.match_id)
            if any(r.kind == "match_end" for r in rows):
                return rows
            await asyncio.sleep(1)
        raise TimeoutError("the worker did not record the final whistle")
    finally:
        await audit.close()


def test_stack_replay_audit_log_equals_the_offline_bundle() -> None:
    rows = asyncio.run(_replay_and_wait())
    live, offline = audit_facts(rows), offline_facts()
    assert sorted(live) == sorted(offline)
    for fid, f in offline.items():
        assert live[fid] == f, fid
    health: dict[str, object] = {}
    for _ in range(30):  # the API reads the facts topic just after the worker writes to Postgres
        with urllib.request.urlopen("http://localhost:8001/health", timeout=5) as r:
            health = json.load(r)
        if health["ended"]:
            break
        time.sleep(1)
    assert health["ended"] and health["facts"] == len(offline)
