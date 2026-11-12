"""The event log and match metadata as typed, frozen models.

`data/events.json` is written by the simulator and read through `Match`, so a
malformed event fails at load rather than deep inside a metric.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, WithJsonSchema

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


class Meta(_Frozen):
    match_id: str
    seed: int
    home: str
    away: str
    teams: dict[str, Team]
    synthetic: bool

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
