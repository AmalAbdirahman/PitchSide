"""Fixed match intervals (15-minute blocks by default) on the official clock.

Each period is cut into blocks of `minutes` nominal minutes; the last block of a period runs on to
the period's end, so stoppage time belongs to it (31-45+). The moment rule for npxG by interval and
the chart series use the same blocks, so a claim and its chart cover the same events.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.events import PERIOD_START_MINUTE, Clock

NOMINAL_MINUTES = (45, 45, 15, 15)  # two halves, then two halves of extra time (Laws of the Game)
# Event times have at most millisecond resolution (StatsBomb), so a block that ends this far
# before the next one starts holds every event before that start and none at it.
GAP_S = 0.001


@dataclass(frozen=True)
class Interval:
    start_t: float
    end_t: float  # inclusive, like every metric window
    first_minute: int
    last_minute: int  # nominal; the period's last block also holds its stoppage time
    stoppage: bool  # True for the last block of a period

    @property
    def label(self) -> str:
        return f"{self.first_minute}–{self.last_minute}{'+' if self.stoppage else ''}"


def _period_end(clock: Clock, i: int) -> float:
    starts = clock.period_starts
    return float(starts[i + 1]) - GAP_S if i + 1 < len(starts) else float(clock.end_t)


def intervals(clock: Clock, minutes: int) -> list[Interval]:
    """Every block of the match, in time order."""
    out: list[Interval] = []
    for i, start in enumerate(clock.period_starts):
        count = max(1, NOMINAL_MINUTES[i] // minutes)
        end = _period_end(clock, i)
        for k in range(count):
            last = k == count - 1
            a = float(start) + 60 * minutes * k
            b = end if last else min(end, a + 60 * minutes - GAP_S)
            first_min = PERIOD_START_MINUTE[i] + minutes * k + 1
            out.append(Interval(a, b, first_min, first_min + minutes - 1, last))
            if b >= end:
                break
    return out
