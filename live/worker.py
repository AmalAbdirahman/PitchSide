"""Live worker: consumes the events topic, keeps match state in the StateStore, runs the pipeline
and publishes facts. Run against the stack: python -m live.worker (settings from environment
variables, see live/config.py)."""

from __future__ import annotations

import asyncio
import time

from app.agents_foundry import Scout
from app.build_bundle import make_scout
from core.config import Settings, load
from core.sources import League
from live.messages import End, EventMsg, FactMsg, Start, Tick, dump, parse
from live.pipeline import LivePipeline, Outcome
from live.ports import AuditLog, Bus, StateStore

EVENTS, FACTS = "events", "facts"


def _score(p: LivePipeline) -> dict[str, int]:
    goals = [e.team for e in p.events if e.type == "goal"]
    return {t: goals.count(t) for t in (p.meta.home, p.meta.away)}


class Worker:
    def __init__(
        self, bus: Bus, state: StateStore, audit: AuditLog, s: Settings, scout: Scout | None
    ):
        self.bus, self.state, self.audit, self.s, self.scout = bus, state, audit, s, scout
        self.pipes: dict[str, LivePipeline] = {}

    async def start(self, msg: Start) -> None:
        saved = await self.state.load(msg.match_id)
        if saved and saved.get("start"):  # a restart: resume from Redis
            self.pipes[msg.match_id] = LivePipeline.restore(saved, self.s, self.scout)
            return
        league = League(**msg.league)
        self.pipes[msg.match_id] = LivePipeline(msg.meta, league, self.s, self.scout)
        await self.state.save_start(msg.match_id, dump(msg))
        await self.audit.append(msg.match_id, "match_start", None, {"meta": dump(msg)["meta"]})
        await self.bus.publish(FACTS, msg.match_id, dump(msg))  # team names etc. for the clients

    async def event(self, msg: EventMsg) -> None:
        p = self.pipes[msg.match_id]
        if msg.seq < len(p.events):  # already applied before a restart
            return
        outcomes = p.add(msg.event)
        await self.state.save_event(msg.match_id, msg.event.model_dump(mode="json"))
        for key, o in outcomes:
            await self.emit(msg.match_id, key, o, msg.seq, msg.sent_at)
        tick = Tick(
            match_id=msg.match_id,
            sent_at=time.time(),
            t=msg.event.t,
            minute=p.meta.clock.minute(msg.event.t),
            score=_score(p),
        )
        await self.bus.publish(FACTS, msg.match_id, dump(tick))

    async def end(self, msg: End) -> None:
        for key, o in self.pipes[msg.match_id].due():
            await self.emit(msg.match_id, key, o, -1, msg.sent_at)
        await self.audit.append(msg.match_id, "match_end", None, {"t": msg.t})
        await self.bus.publish(
            FACTS, msg.match_id, dump(End(match_id=msg.match_id, sent_at=time.time(), t=msg.t))
        )

    async def emit(self, match_id: str, key: str, o: Outcome, seq: int, sent_at: float) -> None:
        latency = round(1000 * (time.time() - sent_at), 1)
        fact = o.fact.to_dict()
        kind = "fact_ready" if o.status == "ready" else "fact_rejected"
        payload = {"fact": fact, "trigger_seq": seq, "latency_ms": latency}
        await self.audit.append(match_id, kind, o.fact.fact_id, payload)
        msg = FactMsg(
            match_id=match_id,
            sent_at=time.time(),
            status=o.status,
            fact=fact,
            trigger_seq=seq,
            latency_ms=latency,
        )
        await self.bus.publish(FACTS, match_id, dump(msg))
        first = o.first_pass.to_dict() if o.first_pass else None
        await self.state.save_moment(match_id, key, first)

    async def run(self, once: bool = False) -> None:
        """Consume until cancelled, or until the first End with once=True."""
        async for raw in self.bus.subscribe(EVENTS, "worker"):
            msg = parse(raw)
            if isinstance(msg, Start):
                await self.start(msg)
            elif isinstance(msg, EventMsg):
                await self.event(msg)
            elif isinstance(msg, End):
                await self.end(msg)
                if once:
                    return


def scout_for(s: Settings) -> Scout | None:
    """The offline build's Scout: replays data/agent_cache.json (and the Reader's cache)."""
    return make_scout(False, s)


async def main() -> None:
    from live.adapters import connect

    s = load()
    bus, state, audit = await connect()
    try:
        await Worker(bus, state, audit, s, scout_for(s)).run()
    finally:
        for c in (bus, state, audit):
            await c.close()


if __name__ == "__main__":
    asyncio.run(main())
