"""The worker's logic, without any I/O: events in, facts out.

After each event the moment rules run on the events so far, and every moment whose time has come
is drafted, verified, routed and rendered with the same functions as the offline build
(app/build_bundle.py). Moment detection is causal (a moment found from the events up to its time
equals the one found from the whole match; tests/test_live.py checks it), and moments are handled
in the offline order with the same Scout memory, so the cached Scout and Reader answers replay
and a full match ends with the offline bundle's facts.

Not live: the planted test errors of the offline bundle (app/drafter.injected_drafts). They are
built from the whole match, so a live worker cannot make them.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agents_foundry import Scout, first_pass, second_pass, verify_rules
from app.drafter import Moment, to_fact
from app.renditions import with_renditions
from app.router import route
from app.scout_memory import ROUTABLE, memory_for
from core.config import Settings
from core.events import Event, Match, Meta
from core.facts import Fact
from core.moments import detect_moments
from core.sources import League


@dataclass(frozen=True)
class Outcome:
    fact: Fact
    status: Literal["ready", "rejected"]
    first_pass: Fact | None  # what the Scout's memory keeps for later moments


def moment_key(m: Moment) -> str:
    return json.dumps(m, sort_keys=True, default=str)


@dataclass
class LivePipeline:
    meta: Meta
    league: League
    s: Settings
    scout: Scout | None
    events: list[Event] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    first_passes: list[Fact] = field(default_factory=list)

    @classmethod
    def restore(cls, state: dict[str, Any], s: Settings, scout: Scout | None) -> LivePipeline:
        """Rebuild from what the StateStore kept (live/ports.py), after a restart."""
        start = state["start"]
        p = cls(Meta.model_validate(start["meta"]), League(**start["league"]), s, scout)
        p.events = [Event.model_validate(e) for e in state["events"]]
        p.seen = set(state["moments"])
        p.first_passes = [Fact.model_validate(f) for f in state["first_pass"]]
        return p

    def match(self) -> Match:
        return Match.model_construct(meta=self.meta, events=list(self.events))

    def add(self, e: Event) -> list[tuple[str, Outcome]]:
        """Take one event; return (moment key, outcome) for every moment now due."""
        self.events.append(e)
        return self.due(e.t)

    def due(self, now: float = math.inf) -> list[tuple[str, Outcome]]:
        teams = (self.meta.home, self.meta.away)
        found = detect_moments(self.events, teams, self.league, self.s, self.meta.clock)
        fresh = [m for m in found if m["created_t"] <= now and moment_key(m) not in self.seen]
        out = []
        for m in fresh:
            key = moment_key(m)
            self.seen.add(key)
            out.append((key, self.handle(m)))
        return out

    def handle(self, m: Moment) -> Outcome:
        """One moment, as app/build_bundle.checked_drafts and build handle it."""
        match, s = self.match(), self.s
        draft = to_fact(m, match, f"f_{len(self.seen):06d}", s.verifier)
        first: Fact | None = None
        if self.scout is None:
            fact = verify_rules(draft, match, self.league, s)
        else:
            memory = memory_for(
                self.first_passes, float(m["created_t"]), match, s.agents.memory_size
            )
            first, pending = first_pass(
                self.scout, m, draft, match, self.league, s, s.agents.mode, memory
            )
            self.first_passes.append(first)
            fact = second_pass(self.scout, m, first, pending, match, self.league, s, s.agents.mode)
        if fact.verification.status not in ROUTABLE:
            return Outcome(fact, "rejected", first)
        return Outcome(with_renditions(route(fact, s.router), match, s), "ready", first)
