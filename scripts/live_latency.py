"""P14: end-to-end latency from event to fact-ready, in live mode.

Run: python scripts/live_latency.py --speed 20            (in-process, in-memory adapters)
     python scripts/live_latency.py --from-postgres        (read a stack run's audit log)

Latency of a fact = wall clock from the replayer sending the event that made its moment due to the
worker having the fact verified, routed and rendered (FactMsg.latency_ms, also in the audit log).
At 20x, events are about 0.45 s apart on average, far more than the worker needs per event, so
nothing queues and the figure is what real-time play would see. In-process runs leave out the
network hops through Redpanda, Redis and Postgres; the stack run includes them.
Writes data/live_latency.json.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import ROOT, load  # noqa: E402
from core.sources import SyntheticSource  # noqa: E402
from live.ports import AuditEntry, InMemoryAudit, InMemoryBus, InMemoryState  # noqa: E402
from live.replayer import replay  # noqa: E402
from live.worker import Worker, scout_for  # noqa: E402
from scripts.fit_xg import commit  # noqa: E402

OUT = ROOT / "data" / "live_latency.json"


def summary(rows: list[AuditEntry]) -> dict[str, Any]:
    facts = [r for r in rows if r.kind in ("fact_ready", "fact_rejected")]
    ms = sorted(float(r.payload["latency_ms"]) for r in facts)
    at_end = [float(r.payload["latency_ms"]) for r in facts if r.payload["trigger_seq"] == -1]
    during = [float(r.payload["latency_ms"]) for r in facts if r.payload["trigger_seq"] != -1]

    def stats(xs: list[float]) -> dict[str, float] | None:
        if not xs:
            return None
        xs = sorted(xs)
        return {
            "n": len(xs),
            "median_ms": round(statistics.median(xs), 1),
            "p95_ms": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))], 1),
            "max_ms": round(xs[-1], 1),
        }

    return {"all": stats(ms), "during_play": stats(during), "at_final_whistle": stats(at_end)}


async def in_process(speed: float) -> list[AuditEntry]:
    s = load()
    bus, state, audit = InMemoryBus(), InMemoryState(), InMemoryAudit()
    match, league = SyntheticSource().load()
    worker = Worker(bus, state, audit, s, scout_for(s))
    await asyncio.gather(replay(bus, match, league, speed), worker.run(once=True))
    return audit.rows


async def from_postgres() -> list[AuditEntry]:
    from live.adapters import postgres_audit

    match, _ = SyntheticSource().load()
    audit = await postgres_audit()
    try:
        return await audit.entries(match.meta.match_id)
    finally:
        await audit.close()


def main(speed: float, stack: bool) -> None:
    t0 = time.perf_counter()
    rows = asyncio.run(from_postgres() if stack else in_process(speed))
    record = {
        "run_date": datetime.date.today().isoformat(),
        "commit": commit(),
        "setup": "docker compose stack (Postgres audit log)"
        if stack
        else "in-process, in-memory adapters",
        "speed": None if stack else speed,
        "machine": f"{platform.system()} {platform.release()}, Python {platform.python_version()}",
        "wall_s": None if stack else round(time.perf_counter() - t0, 1),
        "latency": summary(rows),
    }
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record["latency"], indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Event-to-fact latency in live mode.")
    ap.add_argument("--speed", type=float, default=20.0)
    ap.add_argument("--from-postgres", action="store_true", help="read the stack's audit log")
    args = ap.parse_args()
    main(args.speed, args.from_postgres)
