"""Deterministic checks on an Analyst answer (R5). Code decides whether an answer is shown.

The Analyst may only use numbers that its own tool calls returned. Each recorded call has been
re-run from the event log before these checks (replay), so a call's output is the registry's
output. The checks:
- numbers_listed: every number in the text is in `numbers`;
- numbers_match: every listed number equals the field of the tool call it names, at the precision
  written in the text (a minute may name either side of a window boundary: "after 60'" is a window
  starting at 60:00, which is in the 61st minute);
- entities: every player and team named in the text is behind one of the answer's numbers (the
  subject of the call it comes from, or the player of its event) or a cited evidence event, and no
  name is misspelt. Appearing in some tool call is not enough: in the first live run the Analyst
  gave one player's 4 shots to another player it had also asked about;
- window: no tool call looks past the question's time;
- evidence: every evidence id exists, is not later than the question, and came back from a call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.ask_schema import AnalystOutput, AskCheck, AskNumber, OutAskNumber
from app.checks import NUMBER
from app.names import WORD, name_problems, named_players, roster
from app.scout_tools import ToolCall
from core.config import Verifier
from core.events import Event, Match


@dataclass(frozen=True)
class Recorded:
    name: str
    args: dict[str, Any]
    out: dict[str, Any]

    @property
    def ids(self) -> set[int]:
        """Event ids this call returned."""
        if self.name == "compute_metric":
            return set(self.out.get("evidence_ids", []))
        return {e["id"] for e in self.out.get("events", [])}


def parse_calls(calls: list[ToolCall]) -> list[Recorded]:
    return [Recorded(c.name, json.loads(c.arguments or "{}"), json.loads(c.output)) for c in calls]


def _check(name: str, problems: list[str], ok_detail: str) -> AskCheck:
    return AskCheck(name=name, passed=not problems, detail="; ".join(problems) or ok_detail)  # type: ignore[arg-type]


def _decimals(value: float, text: str) -> int:
    """Decimal places of the value as the text writes it."""
    written = [t for t in NUMBER.findall(text) if abs(float(t) - value) < 1e-9]
    return max((len(t.partition(".")[2]) for t in written), default=0)


def _minutes(t: float | None, match: Match, now: float, end: bool) -> set[int]:
    """Minutes that may name a window boundary at t (either side of it)."""
    clock = match.meta.clock
    t = (now if end else 0.0) if t is None else float(t)
    return {clock.minute(t)} | ({clock.minute(t - 1e-3)} if t > 0 else set())


def _event(num: OutAskNumber, rec: Recorded, by_id: dict[int, Event]) -> Event | None:
    if num.event_id is None or num.event_id not in rec.ids:
        return None
    return by_id.get(num.event_id)


def _truth(
    num: OutAskNumber, rec: Recorded, match: Match, now: float, by_id: dict[int, Event]
) -> tuple[set[int] | float | None, str]:
    """What the named field of the call holds: a value, a set of minutes, or None."""
    f, a = num.field, rec.args
    if f in ("value", "n", "k") and rec.name == "compute_metric":
        where = (
            f"{a.get('metric')} for {a.get('subject_id')}, {a.get('start_t')}-{a.get('end_t')} s"
        )
        return rec.out.get(f), f"compute_metric {where}: {f}"
    if f == "total" and rec.name == "list_events":
        return rec.out.get("total"), "list_events: total"
    if f in ("start_minute", "end_minute"):
        end = f == "end_minute"
        return _minutes(a.get("end_t" if end else "start_t"), match, now, end), f"{rec.name}: {f}"
    if f in ("event_minute", "event_xg"):
        e = _event(num, rec, by_id)
        if e is None:
            return None, f"{rec.name}: event {num.event_id} not returned"
        if f == "event_minute":
            return {match.meta.clock.minute(e.t)}, f"event {e.id}: minute"
        return e.xg, f"event {e.id}: xG"
    return None, f"{rec.name} has no {f}"


def number_ok(
    num: OutAskNumber, calls: list[Recorded], text: str, match: Match, now: float
) -> tuple[bool, str]:
    if not 0 <= num.call < len(calls):
        return False, f"there is no tool call {num.call}"
    rec = calls[num.call]
    if "error" in rec.out:
        return False, f"tool call {num.call} returned an error"
    truth, source = _truth(num, rec, match, now, {e.id: e for e in match.events})
    if isinstance(truth, set):
        return num.value in truth, source
    if truth is None:
        return False, source
    tol = 0.5 * 10 ** -_decimals(num.value, text) + 1e-9
    return abs(float(truth) * num.scale - num.value) <= tol, source


def check_numbers(
    out: AnalystOutput, calls: list[Recorded], match: Match, now: float
) -> tuple[list[AskCheck], list[AskNumber]]:
    listed = [n.value for n in out.numbers]
    missing = [
        t for t in NUMBER.findall(out.text) if not any(abs(float(t) - v) < 1e-9 for v in listed)
    ]
    numbers, wrong = [], []
    for n in out.numbers:
        ok, source = number_ok(n, calls, out.text, match, now)
        numbers.append(AskNumber(key=n.key, value=n.value, verified=ok, source=source))
        if not ok:
            wrong.append(f"{n.key}={n.value:g} does not match {source}")
    return [
        _check(
            "numbers_listed",
            [f"{t} in the text is not in numbers" for t in missing],
            "every number in the text is listed",
        ),
        _check("numbers_match", wrong, "every number traced to a tool call"),
    ], numbers


def _anchored(out: AnalystOutput, calls: list[Recorded], match: Match) -> tuple[set[str], set[str]]:
    """Players and teams that one of the answer's numbers or cited events is about."""
    by_id = {e.id: e for e in match.events}
    team_of = {pid: k for k, t in match.meta.teams.items() for pid in t.players}
    players: set[str] = set()
    teams: set[str] = set()
    for num in out.numbers:
        c = calls[num.call] if 0 <= num.call < len(calls) else None
        if c is not None and "error" not in c.out and c.name == "compute_metric":
            kind = c.args.get("subject_type")
            if kind in ("player", "team"):
                (players if kind == "player" else teams).add(str(c.args.get("subject_id")))
    ids = set(out.evidence_ids) | {n.event_id for n in out.numbers if n.event_id is not None}
    for e in (by_id[i] for i in ids if i in by_id):
        players |= {e.player_id} if e.player_id else set()
        teams.add(e.team)
    return players, teams | {team_of[p] for p in players if p in team_of}


def check_entities(
    out: AnalystOutput, calls: list[Recorded], match: Match, v: Verifier
) -> AskCheck:
    text = out.text
    squad, names = roster(match.meta), {t.name: k for k, t in match.meta.teams.items()}
    players, teams = _anchored(out, calls, match)
    problems = [
        f"text names {p.full}, but no listed number or cited event is about them"
        for p in squad
        if p.pid in named_players(text, squad) and p.pid not in players
    ]
    words = {w for w in WORD.findall(text)}
    problems += [
        f"text names {n}, but no listed number or cited event is about them"
        for n, k in names.items()
        if n in words and k not in teams
    ]
    problems += name_problems(text, squad, set(names), v.name_max_edits, v.name_min_length)
    return _check(
        "entities", problems, "every player and team named is behind a number or cited event"
    )


def check_window(calls: list[Recorded], now: float) -> AskCheck:
    late = [
        f"{c.name} looks to {c.args['end_t']} s, after the question at {now} s"
        for c in calls
        if c.args.get("end_t") is not None and float(c.args["end_t"]) > now
    ]
    return _check("window", late, "no tool call looks past the question")


def check_evidence(ids: list[int], calls: list[Recorded], match: Match, now: float) -> AskCheck:
    by_id, returned = {e.id: e for e in match.events}, set().union(*(c.ids for c in calls))
    problems = [
        f"event {i} {'does not exist' if i not in by_id else 'is after the question' if by_id[i].t > now else 'came from no tool call'}"
        for i in ids
        if i not in by_id or by_id[i].t > now or i not in returned
    ]
    return _check("evidence", problems, "every evidence id came from a tool call")


def check_answer(
    out: AnalystOutput, calls: list[ToolCall], match: Match, now: float, v: Verifier
) -> tuple[list[AskCheck], list[AskNumber]]:
    rec = parse_calls(calls)
    checks, numbers = check_numbers(out, rec, match, now)
    checks += [
        check_entities(out, rec, match, v),
        check_window(rec, now),
        check_evidence(out.evidence_ids, rec, match, now),
    ]
    return checks, numbers
