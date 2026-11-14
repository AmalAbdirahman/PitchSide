"""Live API: a WebSocket for the live control room and the overlay, and producer decisions.

python -m live.api   (serves http://localhost:8001: / the live control room, /docs the static
site including /docs/overlay.html, /ws the WebSocket, POST /decisions)

It reads the facts topic from the start, so a restarted API rebuilds its view from the log.
Decisions are checked against the same routing rules as the replay (app/router.resolve_state,
timed in real seconds from when the fact was ready), appended to the audit log (the first decision
on a fact wins; a second gets 409) and pushed to every client.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.router import RoutingRules, resolve_state, routing_rules
from core.config import ROOT, Settings, load
from live.messages import End, FactMsg, Start, Tick, parse
from live.ports import AuditLog, Bus, DecisionTaken, Json

Ports = Callable[[], Awaitable[tuple[Bus, AuditLog]]]


class DecisionIn(BaseModel):
    fact_id: str
    decision: str


@dataclass
class View:
    """What the clients see, rebuilt from the facts topic and the audit log."""

    rules: RoutingRules
    match_id: str | None = None
    meta: Json | None = None
    tick: Json | None = None
    facts: dict[str, Json] = field(default_factory=dict)  # fact_id -> {fact, status, ready_at}
    decisions: dict[str, Json] = field(default_factory=dict)
    ended: bool = False
    clients: set[WebSocket] = field(default_factory=set)

    def snapshot(self, now: float) -> Json:
        return {
            "type": "snapshot",
            "server_now": now,
            "routing": self.rules.model_dump(mode="json"),
            "match_id": self.match_id,
            "meta": self.meta,
            "tick": self.tick,
            "facts": list(self.facts.values()),
            "decisions": self.decisions,
            "ended": self.ended,
        }

    async def broadcast(self, msg: Json) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except (WebSocketDisconnect, RuntimeError):  # the client went away; drop it
                self.clients.discard(ws)


async def follow(view: View, bus: Bus, audit: AuditLog) -> None:
    """Keep the view up to date from the facts topic and push each change to the clients."""
    async for raw in bus.subscribe("facts", f"api-{uuid.uuid4().hex[:8]}"):
        msg = parse(raw)
        if isinstance(msg, Start):
            view.match_id, view.meta = msg.match_id, msg.model_dump(mode="json")["meta"]
            for row in await audit.entries(msg.match_id):
                if row.kind == "decision" and row.fact_id:
                    view.decisions[row.fact_id] = row.payload
            await view.broadcast({"type": "start", "match_id": msg.match_id, "meta": view.meta})
        elif isinstance(msg, Tick):
            view.tick = {"t": msg.t, "minute": msg.minute, "score": msg.score}
            await view.broadcast({"type": "tick", **view.tick})
        elif isinstance(msg, FactMsg):
            entry = {"fact": msg.fact, "status": msg.status, "ready_at": msg.sent_at}
            view.facts[msg.fact["fact_id"]] = entry
            await view.broadcast({"type": "fact", **entry})
        elif isinstance(msg, End):
            view.ended = True
            await view.broadcast({"type": "end"})


def decide(view: View, body: DecisionIn, now: float) -> Json:
    """Check a producer decision against the routing rules; the result to record, or an error."""
    entry = view.facts.get(body.fact_id)
    if entry is None:
        raise HTTPException(404, f"unknown fact {body.fact_id}")
    if entry["status"] != "ready":
        raise HTTPException(409, f"{body.fact_id} was rejected by the verifier")
    fact = entry["fact"]
    tier, reasons = fact["risk"]["tier"], fact["risk"].get("reasons", [])
    rule, ttl = view.rules.tiers[tier], fact["approval"].get("ttl_s", 0)
    if body.decision == "autopilot" or body.decision not in rule.decisions:
        raise HTTPException(422, f"{tier} facts take {sorted(set(rule.decisions) - {'autopilot'})}")
    elapsed = now - entry["ready_at"]
    state, _ = resolve_state(view.rules, tier, ttl, reasons, elapsed)
    if state != rule.initial.state:
        raise HTTPException(409, f"too late: {body.fact_id} is already {state}")
    state, by = resolve_state(view.rules, tier, ttl, reasons, elapsed, body.decision, elapsed)
    return {"decision": body.decision, "state": state, "by": by, "elapsed_s": round(elapsed, 3)}


def create_app(ports: Ports, s: Settings, clock: Callable[[], float] = time.time) -> FastAPI:
    view = View(routing_rules(s.router))
    held: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bus, audit = await ports()
        held.update(bus=bus, audit=audit, task=asyncio.create_task(follow(view, bus, audit)))
        yield
        held["task"].cancel()
        await bus.close()
        await audit.close()

    app = FastAPI(title="PitchSide live", lifespan=lifespan)
    app.state.view = view

    @app.get("/health")
    async def health() -> Json:
        return {
            "ok": True,
            "match_id": view.match_id,
            "facts": len(view.facts),
            "ended": view.ended,
        }

    @app.get("/state")
    async def state() -> Json:
        return view.snapshot(clock())

    @app.post("/decisions")
    async def decisions(body: DecisionIn) -> Json:
        now = clock()
        record = decide(view, body, now)
        try:
            await held["audit"].append(view.match_id or "", "decision", body.fact_id, record)
        except DecisionTaken:
            raise HTTPException(409, f"{body.fact_id} already has a decision") from None
        view.decisions[body.fact_id] = record
        await view.broadcast({"type": "decision", "fact_id": body.fact_id, **record})
        return {"fact_id": body.fact_id, **record}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_json(view.snapshot(clock()))
        view.clients.add(socket)
        try:
            while True:
                await socket.receive_text()  # clients only listen; this notices them leave
        except WebSocketDisconnect:
            view.clients.discard(socket)

    app.mount("/docs", StaticFiles(directory=ROOT / "docs", html=True), name="docs")
    app.mount("/", StaticFiles(directory=ROOT / "live" / "static", html=True), name="live")
    return app


async def _stack_ports() -> tuple[Bus, AuditLog]:
    from live.adapters import kafka_bus, postgres_audit

    return await kafka_bus(), await postgres_audit()


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(_stack_ports, load()), host="0.0.0.0", port=8001)  # noqa: S104 - in a container


if __name__ == "__main__":
    main()
