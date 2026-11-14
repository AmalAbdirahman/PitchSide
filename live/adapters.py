"""The real adapters behind live/ports.py: Redpanda (Kafka API) with aiokafka, Redis with
redis-py, Postgres with psycopg. Imported only by the live services, never by the static build.

Connection settings come from environment variables (defaults suit docker compose's published
ports on localhost):
  PITCHSIDE_BROKERS   localhost:19092
  PITCHSIDE_REDIS     redis://localhost:6379/0
  PITCHSIDE_POSTGRES  postgresql://pitchside:pitchside@localhost:5432/pitchside

For the thin Azure slice (P15, infra/; not deployed) the same code takes, also from the environment:
  PITCHSIDE_KAFKA_SASL_PASSWORD   set -> SASL_SSL with PLAIN (Event Hubs: its connection string)
  PITCHSIDE_KAFKA_SASL_USERNAME   default "$ConnectionString", Event Hubs' fixed user name
  PITCHSIDE_AUDIT                 "postgres" (default) or "stdout" (JSON lines, for the slice)
  PITCHSIDE_REDIS                 rediss://:<key>@<host>:<port>/0 for TLS; redis-py reads it as is
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any, TextIO

from live.ports import AuditEntry, AuditLog, DecisionTaken, InMemoryAudit, Json

BROKERS = os.environ.get("PITCHSIDE_BROKERS", "localhost:19092")
REDIS_URL = os.environ.get("PITCHSIDE_REDIS", "redis://localhost:6379/0")
POSTGRES_URL = os.environ.get(
    "PITCHSIDE_POSTGRES", "postgresql://pitchside:pitchside@localhost:5432/pitchside"
)


def kafka_security(env: Mapping[str, str]) -> dict[str, Any]:
    """aiokafka keyword arguments for the broker's security. None for Redpanda in docker compose;
    SASL_SSL with PLAIN when a password is set (Azure Event Hubs' Kafka endpoint)."""
    password = env.get("PITCHSIDE_KAFKA_SASL_PASSWORD", "")
    if not password:
        return {}
    return {
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "PLAIN",
        "sasl_plain_username": env.get("PITCHSIDE_KAFKA_SASL_USERNAME", "$ConnectionString"),
        "sasl_plain_password": password,
        "ssl_context": ssl.create_default_context(),
    }


def _kafka(**kwargs: Any) -> dict[str, Any]:
    return {"bootstrap_servers": BROKERS, **kafka_security(os.environ), **kwargs}


class KafkaBus:
    def __init__(self, producer: Any) -> None:
        self.producer = producer
        self.consumers: list[Any] = []

    async def publish(self, topic: str, key: str, value: Json) -> None:
        await self.producer.send_and_wait(topic, json.dumps(value).encode(), key=key.encode())

    async def subscribe(self, topic: str, group: str) -> AsyncIterator[Json]:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(topic, **_kafka(group_id=group, auto_offset_reset="earliest"))
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

    admin = AIOKafkaAdminClient(**_kafka())
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
    producer = AIOKafkaProducer(**_kafka(acks="all", enable_idempotence=True))
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


class StdoutAudit(InMemoryAudit):
    """The audit log as JSON lines on standard output, for the thin Azure slice, which has no
    Postgres (Container Apps sends the lines to Log Analytics). Same rules as the other logs
    while the process runs; not durable, so a restarted worker forgets which facts it recorded.
    The design (docs/architecture/azure.md) keeps Postgres."""

    def __init__(self, out: TextIO | None = None) -> None:
        super().__init__()
        self.out = out if out is not None else sys.stdout

    async def append(self, match_id: str, kind: str, fact_id: str | None, payload: Json) -> int:
        before = len(self.rows)
        seq = await super().append(match_id, kind, fact_id, payload)
        if len(self.rows) > before:
            row = self.rows[-1]
            line = {"audit": True, "seq": seq, "match_id": match_id, "kind": kind}
            line |= {"fact_id": fact_id, "payload": payload, "recorded_at": row.recorded_at}
            print(json.dumps(line), file=self.out, flush=True)
        return seq


AUDIT_KINDS = ("postgres", "stdout")


def audit_kind(env: Mapping[str, str]) -> str:
    kind = env.get("PITCHSIDE_AUDIT", "postgres")
    if kind not in AUDIT_KINDS:
        raise ValueError(f"PITCHSIDE_AUDIT must be one of {AUDIT_KINDS}, not {kind!r}")
    return kind


async def audit_log() -> AuditLog:
    return StdoutAudit() if audit_kind(os.environ) == "stdout" else await postgres_audit()


async def connect() -> tuple[KafkaBus, RedisState, AuditLog]:
    return await kafka_bus(), await redis_state(), await audit_log()
