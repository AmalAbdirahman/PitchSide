"""The real adapters behind live/ports.py: Redpanda (Kafka API) with aiokafka, Redis with
redis-py, Postgres with psycopg. Imported only by the live services, never by the static build.

Connection settings come from environment variables (defaults suit docker compose's published
ports on localhost):
  PITCHSIDE_BROKERS   localhost:19092
  PITCHSIDE_REDIS     redis://localhost:6379/0
  PITCHSIDE_POSTGRES  postgresql://pitchside:pitchside@localhost:5432/pitchside
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from live.ports import AuditEntry, DecisionTaken, Json

BROKERS = os.environ.get("PITCHSIDE_BROKERS", "localhost:19092")
REDIS_URL = os.environ.get("PITCHSIDE_REDIS", "redis://localhost:6379/0")
POSTGRES_URL = os.environ.get(
    "PITCHSIDE_POSTGRES", "postgresql://pitchside:pitchside@localhost:5432/pitchside"
)


class KafkaBus:
    def __init__(self, producer: Any) -> None:
        self.producer = producer
        self.consumers: list[Any] = []

    async def publish(self, topic: str, key: str, value: Json) -> None:
        await self.producer.send_and_wait(topic, json.dumps(value).encode(), key=key.encode())

    async def subscribe(self, topic: str, group: str) -> AsyncIterator[Json]:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            topic, bootstrap_servers=BROKERS, group_id=group, auto_offset_reset="earliest"
        )
        await consumer.start()
        self.consumers.append(consumer)
        async for record in consumer:
            yield json.loads(record.value)

    async def close(self) -> None:
        for c in self.consumers:
            await c.stop()
        await self.producer.stop()


TOPICS = ("events", "facts")


async def ensure_topics() -> None:
    """Create the topics if they are missing (one partition each: one match, in order)."""
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic
    from aiokafka.errors import TopicAlreadyExistsError

    admin = AIOKafkaAdminClient(bootstrap_servers=BROKERS)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        missing = [
            NewTopic(t, num_partitions=1, replication_factor=1) for t in TOPICS if t not in existing
        ]
        if missing:
            try:
                await admin.create_topics(missing)
            except TopicAlreadyExistsError:  # another service created it first; nothing to do
                pass
    finally:
        await admin.close()


async def kafka_bus() -> KafkaBus:
    from aiokafka import AIOKafkaProducer

    await ensure_topics()
    producer = AIOKafkaProducer(bootstrap_servers=BROKERS, acks="all", enable_idempotence=True)
    await producer.start()
    return KafkaBus(producer)


class RedisState:
    """pitchside:<match>:start (JSON), :events (list), :moments (list), :first_pass (list)."""

    def __init__(self, client: Any) -> None:
        self.r = client

    def _k(self, match_id: str, part: str) -> str:
        return f"pitchside:{match_id}:{part}"

    async def load(self, match_id: str) -> Json | None:
        start = await self.r.get(self._k(match_id, "start"))
        if start is None:
            return None
        lists = {}
        for part in ("events", "moments", "first_pass"):
            raw = await self.r.lrange(self._k(match_id, part), 0, -1)
            lists[part] = [x.decode() if part == "moments" else json.loads(x) for x in raw]
        return {"start": json.loads(start), **lists}

    async def save_start(self, match_id: str, start: Json) -> None:
        await self.r.set(self._k(match_id, "start"), json.dumps(start))

    async def save_event(self, match_id: str, event: Json) -> None:
        await self.r.rpush(self._k(match_id, "events"), json.dumps(event))

    async def save_moment(self, match_id: str, key: str, first_pass: Json | None) -> None:
        pipe = self.r.pipeline(transaction=True)
        pipe.rpush(self._k(match_id, "moments"), key)
        if first_pass is not None:
            pipe.rpush(self._k(match_id, "first_pass"), json.dumps(first_pass))
        await pipe.execute()

    async def close(self) -> None:
        await self.r.aclose()


async def redis_state() -> RedisState:
    import redis.asyncio as redis

    return RedisState(redis.from_url(REDIS_URL))


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  seq         bigserial PRIMARY KEY,
  match_id    text NOT NULL,
  kind        text NOT NULL,
  fact_id     text,
  payload     jsonb NOT NULL,
  recorded_at double precision NOT NULL
);
-- One fact row and at most one decision per (match, kind, fact): the first decision wins and a
-- restarted worker cannot record a fact twice.
CREATE UNIQUE INDEX IF NOT EXISTS audit_one_per_fact
  ON audit_log (match_id, kind, fact_id) WHERE fact_id IS NOT NULL;
"""


class PostgresAudit:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def append(self, match_id: str, kind: str, fact_id: str | None, payload: Json) -> int:
        from psycopg.types.json import Jsonb

        async with self.conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO audit_log (match_id, kind, fact_id, payload, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING seq",
                (match_id, kind, fact_id, Jsonb(payload), time.time()),
            )
            row = await cur.fetchone()
            if row is not None:
                return int(row[0])
            if kind == "decision":
                raise DecisionTaken(fact_id)
            await cur.execute(
                "SELECT seq FROM audit_log WHERE match_id=%s AND kind=%s AND fact_id=%s",
                (match_id, kind, fact_id),
            )
            found = await cur.fetchone()
            return int(found[0])

    async def entries(self, match_id: str) -> list[AuditEntry]:
        async with self.conn.cursor() as cur:
            await cur.execute(
                "SELECT seq, match_id, kind, fact_id, payload, recorded_at FROM audit_log "
                "WHERE match_id=%s ORDER BY seq",
                (match_id,),
            )
            return [AuditEntry(*row) for row in await cur.fetchall()]

    async def close(self) -> None:
        await self.conn.close()


async def postgres_audit() -> PostgresAudit:
    import psycopg

    conn = await psycopg.AsyncConnection.connect(POSTGRES_URL, autocommit=True)
    await conn.execute(SCHEMA)
    return PostgresAudit(conn)


async def connect() -> tuple[KafkaBus, RedisState, PostgresAudit]:
    return await kafka_bus(), await redis_state(), await postgres_audit()
