"""The match clock: monotonic match time in, official football minutes out."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.events import Clock, Match


def test_simulator_clock_keeps_plain_minutes(match: Match) -> None:
    clock = match.meta.clock
    assert clock.half_t == 2700
    for t in range(0, int(clock.end_t) + 1, 7):
        assert clock.minute(t) == t // 60 + 1


def test_second_half_restarts_at_45_after_first_half_stoppage() -> None:
    # First half ran to 48:26, so the second half starts at match time 2908 s.
    clock = Clock(period_starts=[0, 2908], end_t=5913)
    assert clock.minute(17 * 60 + 57) == 18
    assert clock.minute(2906) == 49  # stoppage time, shown as the running first-half minute
    assert clock.minute(2908) == 46
    assert clock.minute(2908 + 13 * 60 + 34) == 59


def test_extra_time_periods_start_at_90_and_105() -> None:
    clock = Clock(period_starts=[0, 2800, 5600, 6600], end_t=7600)
    assert clock.minute(5600) == 91
    assert clock.minute(6600 + 59) == 106


@pytest.mark.parametrize(
    "starts,end",
    [([10, 2700], 5400), ([0, 2700, 2600], 5400), ([0, 2700], 2700), ([], 5400), ([0] * 5, 1)],
)
def test_clock_rejects_bad_periods(starts: list[int], end: int) -> None:
    with pytest.raises(ValidationError):
        Clock(period_starts=starts, end_t=end)
