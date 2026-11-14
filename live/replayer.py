"""Replayer: publishes a match's events to the events topic in match time, or N times faster.

python -m live.replayer --speed 10     (10x; --speed 0 sends as fast as possible)
Sends Start (metadata and league priors), each event in order, then End at the final whistle.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import time

from core.events import Match
from core.sources import League, SyntheticSource
from live.messages import End, EventMsg, Start, dump
from live.ports import Bus

EVENTS = "events"


async def replay(bus: Bus, match: Match, league: League, speed: float) -> None:
    mid = match.meta.match_id
    start = Start(
        match_id=mid, sent_at=time.time(), meta=match.meta, league=dataclasses.asdict(league)
    )
    await bus.publish(EVENTS, mid, dump(start))
    t0, wall0 = 0.0, time.monotonic()
    for i, e in enumerate(match.events):
        if speed > 0:
            await asyncio.sleep(max(0.0, (e.t - t0) / speed - (time.monotonic() - wall0)))
        await bus.publish(
            EVENTS, mid, dump(EventMsg(match_id=mid, sent_at=time.time(), seq=i, event=e))
        )
    end_t = float(match.meta.clock.end_t)
    if speed > 0:
        await asyncio.sleep(max(0.0, (end_t - t0) / speed - (time.monotonic() - wall0)))
    await bus.publish(EVENTS, mid, dump(End(match_id=mid, sent_at=time.time(), t=end_t)))


async def main(speed: float) -> None:
    from live.adapters import kafka_bus

    bus = await kafka_bus()
    try:
        match, league = SyntheticSource().load()
        await replay(bus, match, league, speed)
    finally:
        await bus.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Publish the synthetic match to the events topic.")
    ap.add_argument(
        "--speed", type=float, default=1.0, help="match seconds per real second; 0 = no waiting"
    )
    asyncio.run(main(ap.parse_args().speed))
