"""The event log and match metadata as typed, frozen models.

`data/events.json` is written by the simulator and read through `Match`, so a
malformed event fails at load rather than deep inside a metric.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, WithJsonSchema, model_validator

# A JSON number. int stays int and float stays float, so values written back
# into the bundle keep their exact form (71 is not rewritten as 71.0).
Number = Annotated[int | float, WithJsonSchema({"type": "number"})]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Event(_Frozen):
    id: int
    t: Number
    period: int
    type: str
    team: str
    player_id: str
    x: Number
    y: Number
    end_x: Number | None = None
    end_y: Number | None = None
    outcome: str | None = None
    player_speed: Number | None = None
    pass_distance: Number | None = None
    pass_difficulty: Number | None = None
    ball_speed: Number | None = None
    xg: Number | None = None
    shot_speed: Number | None = None
    card: str | None = None
    player_off: str | None = None


class Team(_Frozen):
    name: str
    players: dict[str, str]


# Official minute at which each period starts: two halves, then two halves of extra time
# (Laws of the Game). A penalty shoot-out is not a period of play and is not on the clock.
PERIOD_START_MINUTE = (0, 45, 90, 105)


class Clock(_Frozen):
    """Match time in seconds runs on without gaps across periods, so windows and orderings work.
    The official minute restarts at 45 for the second half, so it is derived per period."""

    period_starts: list[Number]  # match time at which each period starts; the first is 0
    end_t: Number  # match time of the final whistle

    @model_validator(mode="after")
    def _check(self) -> Clock:
        s = self.period_starts
        if not 1 <= len(s) <= len(PERIOD_START_MINUTE) or s[0] != 0:
            raise ValueError("period_starts must hold 1 to 4 periods and start at 0")
        if any(b <= a for a, b in zip(s, s[1:], strict=False)) or self.end_t <= s[-1]:
            raise ValueError("period starts must increase and end before end_t")
        return self

    @property
    def half_t(self) -> float:
        """Match time at which the second half starts."""
        return self.period_starts[1] if len(self.period_starts) > 1 else self.end_t

    def minute(self, t: float) -> int:
        """Football minute for match time t (the first 59 s of a period is its first minute)."""
        i = max(k for k, start in enumerate(self.period_starts) if start <= t or k == 0)
        return PERIOD_START_MINUTE[i] + int((t - self.period_starts[i]) // 60) + 1


class Meta(_Frozen):
    match_id: str
    seed: int | None  # None for real matches; the simulator's seed otherwise
    home: str
    away: str
    teams: dict[str, Team]
    synthetic: bool
    clock: Clock

    def team_name(self, team: str) -> str:
        return self.teams[team].name

    def names(self) -> dict[str, str]:
        """Player id -> display name, for both squads."""
        return {pid: name for t in self.teams.values() for pid, name in t.players.items()}

    def player_name(self, pid: str) -> str:
        return self.names()[pid]

    def team_of(self, pid: str) -> str:
        """Team id of a player."""
        return next(tid for tid, t in self.teams.items() if pid in t.players)


class Match(_Frozen):
    meta: Meta
    events: list[Event]
