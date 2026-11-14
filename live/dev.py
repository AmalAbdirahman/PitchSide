"""Live mode in one process, without Docker: replayer, worker and API on the in-memory ports.

python -m live.dev --speed 20     then open http://localhost:8001/

Same code paths as the stack except the adapters (no Redpanda, Redis or Postgres), so it shows the
live control room and overlay working, not the infrastructure. Needs the `live` extras.
"""

from __future__ import annotations

import argparse
import asyncio

from core.config import load
from core.sources import SyntheticSource
from live.api import create_app
from live.ports import InMemoryAudit, InMemoryBus, InMemoryState
from live.replayer import replay
from live.worker import Worker, scout_for


async def run(speed: float, port: int, delay: float) -> None:
    import uvicorn

    s = load()
    bus, state, audit = InMemoryBus(), InMemoryState(), InMemoryAudit()

    async def ports() -> tuple[InMemoryBus, InMemoryAudit]:
        return bus, audit

    server = uvicorn.Server(uvicorn.Config(create_app(ports, s), host="127.0.0.1", port=port))
    match, league = SyntheticSource().load()

    async def replay_later() -> None:
        await asyncio.sleep(delay)  # time to open the page before kick-off
        await replay(bus, match, league, speed)

    print(f"live control room: http://127.0.0.1:{port}/  (kick-off in {delay:.0f} s, {speed}x)")
    await asyncio.gather(
        server.serve(), Worker(bus, state, audit, s, scout_for(s)).run(), replay_later()
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Live mode in one process (no Docker).")
    ap.add_argument("--speed", type=float, default=20.0, help="match seconds per real second")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--delay", type=float, default=5.0, help="seconds before kick-off")
    args = ap.parse_args()
    asyncio.run(run(args.speed, args.port, args.delay))
