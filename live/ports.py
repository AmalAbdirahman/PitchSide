"""The three things the live services depend on, as small interfaces, with in-memory versions.

- Bus: a log of messages per topic (Redpanda in the stack). Every subscriber reads a topic from
  the start, in order, like a new Kafka consumer group with auto_offset_reset=earliest.
- StateStore: the worker's match state (Redis in the stack), so a restarted worker resumes.
- AuditLog: append-only, event-sourced record of facts and decisions (Postgres in the stack).
  One row per (match, kind, fact): appending a fact again (a restarted worker) returns the first
  row; a second decision on a fact raises DecisionTaken, so the first decision wins.

The in-memory versions run the whole pipeline in one process, for tests and the latency script.
The real adapters are in live/adapters.py.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

Json = dict[str, Any]


class DecisionTaken(Exception):
    """A decision for this fact is already in the audit log."""


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    match_id: str
    kind: str  # match_start, fact_ready, fact_rejected, decision, match_end
    fact_id: str | None
    payload: Json
    recorded_at: float


class Bus(Protocol):
    async def publish(self, topic: str, key: str, value: Json) -> None: ...

    def subscribe(self, topic: str, group: str) -> AsyncIterator[Json]: ...

    async def close(self) -> None: ...


class StateStore(Protocol):
    async def load(self, match_id: str) -> Json | None: ...

    async def save_event(self, match_id: str, event: Json) -> None: ...

    async def save_start(self, match_id: str, start: Json) -> None: ...

    async def save_moment(self, match_id: str, key: str, first_pass: Json | None) -> None: ...

    async def close(self) -> None: ...


class AuditLog(Protocol):
    async def append(self, match_id: str, kind: str, fact_id: str | None, payload: Json) -> int: ...

    async def entries(self, match_id: str) -> list[AuditEntry]: ...

    async def close(self) -> None: ...


# ---- in memory -------------------------------------------------------------------------


@dataclass
class InMemoryBus:
    topics: dict[str, list[Json]] = field(default_factory=lambda: defaultdict(list))
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    async def publish(self, topic: str, key: str, value: Json) -> None:
        self.topics[topic].append(value)
        self.changed.set()

    async def subscribe(self, topic: str, group: str) -> AsyncIterator[Json]:
        i = 0
        while True:
            while i < len(self.topics[topic]):
                yield self.topics[topic][i]
                i += 1
            self.changed.clear()
            await self.changed.wait()

    async def close(self) -> None:
        pass


@dataclass
class InMemoryState:
    """Same shape as RedisState: start message, events, moment keys and first-pass facts."""

    data: dict[str, Json] = field(default_factory=dict)

    def _m(self, match_id: str) -> Json:
        return self.data.setdefault(
            match_id, {"start": None, "events": [], "moments": [], "first_pass": []}
        )

    async def load(self, match_id: str) -> Json | None:
        return self.data.get(match_id)

    async def save_start(self, match_id: str, start: Json) -> None:
        self._m(match_id)["start"] = start

    async def save_event(self, match_id: str, event: Json) -> None:
        self._m(match_id)["events"].append(event)

    async def save_moment(self, match_id: str, key: str, first_pass: Json | None) -> None:
        m = self._m(match_id)
        m["moments"].append(key)
        if first_pass is not None:
            m["first_pass"].append(first_pass)

    async def close(self) -> None:
        pass


@dataclass
class InMemoryAudit:
    rows: list[AuditEntry] = field(default_factory=list)

    async def append(self, match_id: str, kind: str, fact_id: str | None, payload: Json) -> int:
        same = [
            r
            for r in self.rows
            if fact_id is not None and (r.match_id, r.kind, r.fact_id) == (match_id, kind, fact_id)
        ]
        if same and kind == "decision":
            raise DecisionTaken(fact_id)
        if same:  # a fact recorded again after a worker restart: keep the first
            return same[0].seq
        seq = len(self.rows) + 1
        self.rows.append(AuditEntry(seq, match_id, kind, fact_id, payload, time.time()))
        return seq

    async def entries(self, match_id: str) -> list[AuditEntry]:
        return [r for r in self.rows if r.match_id == match_id]

    async def close(self) -> None:
        pass
