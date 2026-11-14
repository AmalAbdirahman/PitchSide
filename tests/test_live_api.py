"""P14: the live API on the in-memory ports: snapshot over the WebSocket, producer decisions
checked against the routing rules, first decision wins, decisions in the audit log.
Needs the `live` extras (fastapi, httpx); skipped without them."""

from __future__ import annotations

import time
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from core.config import load  # noqa: E402
from live.api import create_app  # noqa: E402
from live.ports import InMemoryAudit, InMemoryBus  # noqa: E402

S = load()


@pytest.fixture()
def client(replayed: tuple[Any, Any, Any]) -> Any:
    """The API over a copy of the replayed facts topic. Its clock stands 0.5 s after each fact
    was ready, so every fact is still in its tier's starting state."""
    bus = InMemoryBus()
    bus.topics["facts"] = list(replayed[0].topics["facts"])
    audit = InMemoryAudit()
    clock = {"now": time.time()}

    async def ports() -> tuple[InMemoryBus, InMemoryAudit]:
        return bus, audit

    app = create_app(ports, S, clock=lambda: clock["now"])
    with TestClient(app) as c:
        c.audit, c.clock = audit, clock
        _wait(lambda: app.state.view.ended)
        yield c


def _wait(cond: Any, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while not cond():
        if time.time() > end:
            raise TimeoutError
        time.sleep(0.02)


def _fact(c: Any, tier: str) -> dict[str, Any]:
    snap = c.get("/state").json()
    entry = next(
        f for f in snap["facts"] if f["status"] == "ready" and f["fact"]["risk"]["tier"] == tier
    )
    c.clock["now"] = entry["ready_at"] + 0.5
    return entry


def test_websocket_sends_a_snapshot_of_every_fact(
    client: Any, replayed: tuple[Any, Any, Any]
) -> None:
    with client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
    facts = [r for r in replayed[2].rows if r.kind in ("fact_ready", "fact_rejected")]
    assert snap["type"] == "snapshot" and snap["ended"]
    assert len(snap["facts"]) == len(facts)
    assert snap["meta"]["home"] == "NOR" and snap["tick"]["score"] == {"NOR": 1, "HAR": 0}
    assert set(snap["routing"]["tiers"]) == {"T0", "T1", "T2"}


def test_a_decision_is_recorded_once(client: Any) -> None:
    # The live match has no T2 facts: the offline bundle's two are planted test errors.
    entry = _fact(client, "T1")
    fid = entry["fact"]["fact_id"]
    ok = client.post("/decisions", json={"fact_id": fid, "decision": "veto"})
    assert ok.status_code == 200 and ok.json()["state"] == "vetoed"
    again = client.post("/decisions", json={"fact_id": fid, "decision": "veto"})
    assert again.status_code == 409
    rows = [r for r in client.audit.rows if r.kind == "decision"]
    assert [(r.fact_id, r.payload["decision"]) for r in rows] == [(fid, "veto")]
    assert client.get("/state").json()["decisions"][fid]["by"] == "producer"


def test_decisions_follow_the_routing_rules(client: Any) -> None:
    t1 = _fact(client, "T1")["fact"]["fact_id"]
    wrong = client.post("/decisions", json={"fact_id": t1, "decision": "approve"})
    assert wrong.status_code == 422  # T1 facts can only be vetoed
    assert (
        client.post("/decisions", json={"fact_id": t1, "decision": "autopilot"}).status_code == 422
    )
    t0 = _fact(client, "T0")["fact"]["fact_id"]
    assert client.post("/decisions", json={"fact_id": t0, "decision": "veto"}).status_code == 422
    entry = _fact(client, "T1")
    client.clock["now"] = entry["ready_at"] + 60  # long after the veto window
    late = client.post("/decisions", json={"fact_id": t1, "decision": "veto"})
    assert late.status_code == 409 and "too late" in late.json()["detail"]
    missing = client.post("/decisions", json={"fact_id": "f_999999", "decision": "veto"})
    assert missing.status_code == 404
