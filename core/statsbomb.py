"""StatsBomb Open Data adapter: raw StatsBomb events -> our Event model and convention.

Every mapping decision is listed in data_sources.md; the main ones:
- Coordinates: StatsBomb gives each event from the acting team's view on a 120 x 80 pitch, attacking
  left to right with y = 0 on that team's left touchline. That is our convention, so x and y are
  only rescaled to the configured pitch (105 x 68), never mirrored. Own goals are the exception.
- Time: periods 1-4 are laid end to end. A period starts 2 s (rounded down) after the previous
  period's last event, so match time never goes backwards; meta.clock gives the official minute.
  The penalty shoot-out (period 5) is dropped.
- Kept types: Pass, Carry, Shot (plus a goal event after a scoring shot), Own Goal Against,
  Substitution, cards from Foul Committed and Bad Behaviour, Pressure, and a derived
  possession_change. Every other StatsBomb type is dropped.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.config import ROOT, Pitch, StatsBomb
from core.events import Clock, Event, Match, Meta, Team
from core.xg import ShotFeatures

RAW_DIR = ROOT / "data" / "external" / "statsbomb"
LAST_PERIOD = 4  # periods 1-2 are halves, 3-4 extra time; 5 is the shoot-out
SUB_XY = (52.5, 0.0)  # StatsBomb substitutions have no location; same spot the simulator uses
SHOT_OUTCOME = {
    "Goal": "goal",
    "Saved": "on_target",
    "Saved To Post": "on_target",
    "Saved to Post": "on_target",
    "Off T": "off_target",
    "Wayward": "off_target",
    "Post": "off_target",
    "Saved Off Target": "off_target",
    "Blocked": "blocked",
}
CARD = {"Yellow Card": "yellow", "Second Yellow": "second yellow", "Red Card": "red"}


# ---- raw StatsBomb shapes (only the fields we read) -------------------------------------


class _Raw(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class Ref(_Raw):
    id: int
    name: str


class SbPass(_Raw):
    end_location: list[float]
    outcome: Ref | None = None  # absent means completed


class SbCarry(_Raw):
    end_location: list[float]


class SbShot(_Raw):
    end_location: list[float]
    outcome: Ref
    statsbomb_xg: float | None = None
    body_part: Ref | None = None
    type: Ref | None = None


class SbSub(_Raw):
    replacement: Ref


class SbCard(_Raw):
    card: Ref | None = None


class SbEvent(_Raw):
    index: int
    period: int
    timestamp: str
    type: Ref
    team: Ref
    possession: int
    possession_team: Ref
    player: Ref | None = None
    location: list[float] | None = None
    under_pressure: bool = False
    pass_: SbPass | None = Field(default=None, alias="pass")
    carry: SbCarry | None = None
    shot: SbShot | None = None
    substitution: SbSub | None = None
    foul_committed: SbCard | None = None
    bad_behaviour: SbCard | None = None


class SbPlayer(_Raw):
    player_id: int
    player_name: str
    player_nickname: str | None = None


class SbLineup(_Raw):
    team_id: int
    team_name: str
    lineup: list[SbPlayer]


class SbSide(_Raw):
    id: int = Field(validation_alias="home_team_id")
    name: str = Field(validation_alias="home_team_name")


class SbAway(_Raw):
    id: int = Field(validation_alias="away_team_id")
    name: str = Field(validation_alias="away_team_name")


class SbMatch(_Raw):
    match_id: int
    home_team: SbSide
    away_team: SbAway


# ---- small helpers ----------------------------------------------------------------------


def seconds(timestamp: str) -> float:
    """'00:48:26.080' -> 2906.08 (seconds since the start of the period)."""
    h, m, s = timestamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def team_code(name: str, taken: str = "") -> str:
    """Three-letter code from a team name; changed if it equals the other side's code."""
    letters = re.sub(r"[^A-Za-z]", "", name).upper() or "TEAM"
    code = letters[:3]
    return code if code != taken else letters[:2] + letters[-1]


def display_name(p: SbPlayer) -> str:
    """Nickname if StatsBomb has one, else the full name; doubled apostrophes repaired."""
    return (p.player_nickname or p.player_name).replace("''", "'")


@dataclass(frozen=True)
class Scale:
    sx: float
    sy: float
    length: float
    width: float

    @classmethod
    def of(cls, sb: StatsBomb, pitch: Pitch) -> Scale:
        return cls(
            pitch.length / sb.source_length,
            pitch.width / sb.source_width,
            pitch.length,
            pitch.width,
        )

    def xy(self, loc: list[float]) -> tuple[float, float]:
        return round(loc[0] * self.sx, 1), round(loc[1] * self.sy, 1)

    def mirror(self, loc: list[float]) -> tuple[float, float]:
        x, y = self.xy(loc)
        return round(self.length - x, 1), round(self.width - y, 1)


# ---- clock ------------------------------------------------------------------------------


def make_clock(raw: list[SbEvent]) -> Clock:
    """Periods 1-4 end to end, each starting 2 s (rounded down) after the last event before it."""
    last = {p: max(seconds(e.timestamp) for e in raw if e.period == p) for p in _periods(raw)}
    starts: list[float] = [0]
    for p in sorted(last)[:-1]:
        starts.append(starts[-1] + math.floor(last[p]) + 2)
    final = sorted(last)[-1]
    return Clock(period_starts=starts, end_t=starts[-1] + math.floor(last[final]) + 1)


def _periods(raw: list[SbEvent]) -> list[int]:
    return sorted({e.period for e in raw if e.period <= LAST_PERIOD})


# ---- event mapping ----------------------------------------------------------------------


@dataclass
class Mapper:
    """Turns one match's raw events into our events, in StatsBomb's order."""

    codes: dict[int, str]  # StatsBomb team id -> our team code
    clock: Clock
    scale: Scale

    def t(self, e: SbEvent) -> float:
        return round(self.clock.period_starts[e.period - 1] + seconds(e.timestamp), 1)

    def pid(self, team_id: int, player: Ref) -> str:
        return f"{self.codes[team_id]}_{player.id}"

    def other(self, team_id: int) -> int:
        return next(k for k in self.codes if k != team_id)

    def base(self, e: SbEvent, etype: str, xy: tuple[float, float], **extra: Any) -> dict[str, Any]:
        assert e.player is not None
        return {
            "t": self.t(e),
            "period": e.period,
            "type": etype,
            "team": self.codes[e.team.id],
            "player_id": self.pid(e.team.id, e.player),
            "x": xy[0],
            "y": xy[1],
            **extra,
        }

    def map(self, e: SbEvent) -> list[dict[str, Any]]:
        """Our events for one StatsBomb event (none if its type is dropped)."""
        if e.period > LAST_PERIOD or e.player is None:
            return []
        kind = e.type.name
        if kind == "Pass" and e.pass_ and e.location:
            return [self._pass(e, e.pass_, e.location)]
        if kind == "Carry" and e.carry and e.location:
            ex, ey = self.scale.xy(e.carry.end_location)
            return [
                self.base(
                    e, "carry", self.scale.xy(e.location), end_x=ex, end_y=ey, outcome="success"
                )
            ]
        if kind == "Shot" and e.shot and e.location:
            return self._shot(e, e.shot, e.location)
        if kind == "Own Goal Against":
            return [self._own_goal(e)]
        if kind == "Substitution" and e.substitution:
            return [self._sub(e, e.substitution)]
        if kind == "Pressure" and e.location:
            return [self.base(e, "pressure", self.scale.xy(e.location))]
        return self._card(e)

    def _pass(self, e: SbEvent, p: SbPass, loc: list[float]) -> dict[str, Any]:
        (x, y), (ex, ey) = self.scale.xy(loc), self.scale.xy(p.end_location)
        return self.base(
            e,
            "pass",
            (x, y),
            end_x=ex,
            end_y=ey,
            outcome="fail" if p.outcome else "success",
            pass_distance=round(math.hypot(ex - x, ey - y), 1),
        )

    def _shot(self, e: SbEvent, s: SbShot, loc: list[float]) -> list[dict[str, Any]]:
        xy, (ex, ey) = self.scale.xy(loc), self.scale.xy(s.end_location)
        outcome = SHOT_OUTCOME[s.outcome.name]
        xg = None if s.statsbomb_xg is None else round(s.statsbomb_xg, 3)
        shot = self.base(e, "shot", xy, end_x=ex, end_y=ey, outcome=outcome, xg=xg)
        return [shot, self.base(e, "goal", xy)] if outcome == "goal" else [shot]

    def _own_goal(self, e: SbEvent) -> dict[str, Any]:
        """Credited to the other side, as on the scoreboard; the player is the one who put it in."""
        assert e.player is not None
        xy = (
            self.scale.mirror(e.location)
            if e.location
            else (self.scale.length, self.scale.width / 2)
        )
        out = self.base(e, "goal", xy, outcome="own_goal")
        out["team"] = self.codes[self.other(e.team.id)]
        return out

    def _sub(self, e: SbEvent, s: SbSub) -> dict[str, Any]:
        assert e.player is not None
        out = self.base(e, "substitution", SUB_XY, player_off=self.pid(e.team.id, e.player))
        out["player_id"] = self.pid(e.team.id, s.replacement)
        return out

    def _card(self, e: SbEvent) -> list[dict[str, Any]]:
        holder = e.foul_committed or e.bad_behaviour
        if holder is None or holder.card is None or holder.card.name not in CARD:
            return []
        xy = self.scale.xy(e.location) if e.location else SUB_XY
        return [self.base(e, "card", xy, card=CARD[holder.card.name])]


# ---- possession changes -----------------------------------------------------------------


def possession_changes(raw: list[SbEvent], m: Mapper) -> list[tuple[int, dict[str, Any]]]:
    """A possession_change for the side that lost the ball, placed before the next possession's
    first event. Not after a shot (the simulator does not count those either) or across periods."""
    out: list[tuple[int, dict[str, Any]]] = []
    for i in range(1, len(raw)):
        prev, cur = raw[i - 1], raw[i]
        lost = prev.possession_team.id
        if cur.possession == prev.possession or cur.possession_team.id == lost:
            continue
        if cur.period != prev.period or cur.period > LAST_PERIOD:
            continue
        last = _last_touch(raw[:i], prev.possession, lost)
        if last is None or last.type.name == "Shot":
            continue
        loc = last.pass_.end_location if last.pass_ else last.location
        assert loc is not None
        change = m.base(last, "possession_change", m.scale.xy(loc))
        change["t"] = m.t(cur)
        out.append((cur.index, change))
    return out


def _last_touch(before: list[SbEvent], possession: int, team_id: int) -> SbEvent | None:
    for e in reversed(before):
        if e.possession != possession:
            return None
        if e.team.id == team_id and e.player is not None and e.location is not None:
            return e
    return None


# ---- the source ---------------------------------------------------------------------------


def mu_match_ids(season: list[dict[str, Any]], match_id: int, n: int) -> list[int]:
    """The first `n` other matches of the season by id, for the league xG rate."""
    return sorted(r["match_id"] for r in season if r["match_id"] != match_id)[:n]


def to_match(row: SbMatch, raw: list[SbEvent], lineups: list[SbLineup], scale: Scale) -> Match:
    home = team_code(row.home_team.name)
    codes = {row.home_team.id: home, row.away_team.id: team_code(row.away_team.name, home)}
    clock = make_clock(raw)
    mapper = Mapper(codes, clock, scale)
    tagged = [(e.index, ev) for e in raw for ev in mapper.map(e)] + possession_changes(raw, mapper)
    # Possession changes sort before the event that starts the new possession.
    tagged.sort(key=lambda p: (p[1]["t"], p[0], p[1]["type"] != "possession_change"))
    events = [Event.model_validate({"id": i, **ev}) for i, (_, ev) in enumerate(tagged, 1)]
    teams = {codes[lu.team_id]: _team(lu, codes[lu.team_id]) for lu in lineups}
    meta = Meta(
        match_id=f"statsbomb_{row.match_id}",
        seed=None,
        home=home,
        away=codes[row.away_team.id],
        teams=teams,
        synthetic=False,
        clock=clock,
    )
    return Match(meta=meta, events=events)


def _team(lu: SbLineup, code: str) -> Team:
    return Team(
        name=lu.team_name, players={f"{code}_{p.player_id}": display_name(p) for p in lu.lineup}
    )


def xg_rate(match: Match) -> tuple[float, float]:
    """(total xG, team-minutes) for one match, for the league rate."""
    xg = sum(e.xg or 0.0 for e in match.events if e.type == "shot")
    return xg, 2 * match.meta.clock.end_t / 60


@dataclass(frozen=True)
class StatsBombOpenSource:
    """One StatsBomb Open Data match, read from files downloaded by scripts/fetch_statsbomb.py."""

    sb: StatsBomb
    pitch: Pitch
    match_id: int
    raw_dir: Path = RAW_DIR

    @property
    def label(self) -> str:
        return f"StatsBomb Open Data, match {self.match_id}"

    def _read(self, rel: str) -> Any:
        path = self.raw_dir / rel
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing; run python scripts/fetch_statsbomb.py --match {self.match_id}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def match(self, match_id: int) -> Match:
        season = self._read(f"matches/{self.sb.competition_id}/{self.sb.season_id}.json")
        row = SbMatch.model_validate(next(r for r in season if r["match_id"] == match_id))
        raw = [SbEvent.model_validate(e) for e in self._read(f"events/{match_id}.json")]
        lineups = [SbLineup.model_validate(t) for t in self._read(f"lineups/{match_id}.json")]
        return to_match(row, raw, lineups, Scale.of(self.sb, self.pitch))

    def load(self) -> tuple[Match, float]:
        """The match, and mu from the other downloaded matches of the season (never this one)."""
        season = self._read(f"matches/{self.sb.competition_id}/{self.sb.season_id}.json")
        others = [
            self.match(mid) for mid in mu_match_ids(season, self.match_id, self.sb.mu_matches)
        ]
        totals = [xg_rate(m) for m in others]
        return self.match(self.match_id), sum(x for x, _ in totals) / sum(n for _, n in totals)


# ---- shots for fitting xG (P7) ----------------------------------------------------------


@dataclass(frozen=True)
class ShotRow:
    match_id: int
    features: ShotFeatures
    goal: bool
    statsbomb_xg: float | None


def shot_features(e: SbEvent, shot: SbShot, loc: list[float], scale: Scale) -> ShotFeatures:
    """Body part Head -> header; anything but a foot or the head -> other_body. Shot type
    Free Kick -> free_kick, Penalty -> penalty; Open Play, Corner and Kick Off are open play."""
    body = shot.body_part.name if shot.body_part else "Right Foot"
    kind = shot.type.name if shot.type else "Open Play"
    x, y = scale.xy(loc)
    return ShotFeatures(
        x=x,
        y=y,
        header=body == "Head",
        other_body=body not in ("Head", "Right Foot", "Left Foot"),
        free_kick=kind == "Free Kick",
        under_pressure=e.under_pressure,
        penalty=kind == "Penalty",
    )


def shot_rows(raw: list[SbEvent], match_id: int, scale: Scale) -> list[ShotRow]:
    """Every shot in periods 1-4 (the shoot-out is not open play)."""
    out = []
    for e in raw:
        if e.type.name != "Shot" or e.shot is None or e.location is None or e.period > LAST_PERIOD:
            continue
        feats = shot_features(e, e.shot, e.location, scale)
        out.append(ShotRow(match_id, feats, e.shot.outcome.name == "Goal", e.shot.statsbomb_xg))
    return out


def read_shots(raw_dir: Path, match_id: int, scale: Scale) -> list[ShotRow]:
    path = raw_dir / "events" / f"{match_id}.json"
    raw = [SbEvent.model_validate(e) for e in json.loads(path.read_text(encoding="utf-8"))]
    return shot_rows(raw, match_id, scale)
