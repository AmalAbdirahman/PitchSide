"""Read-only tools for the Scout's explore mode (P11), backed by the metric registry.

Each tool answers from the event log up to `now` (the moment's time) and nothing later, so the
Scout cannot see the future. The tools only read; they cannot change a fact, the log or a verdict,
and whatever the Scout writes is still recomputed by the verifier.

- list_metrics(): every metric in core/metrics.REGISTRY, with its kind and subject types;
- compute_metric(metric, subject_type, subject_id, start_t, end_t): the registry's value, n, k and
  evidence ids, the same computation the verifier runs;
- list_events(type, team, player_id, start_t, end_t): matching events, most recent last, capped.

Every call and its output is recorded, so a cached run can be replayed and re-checked offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.config import MetricContext
from core.events import Match
from core.facts import Subject, Window
from core.metrics import REGISTRY, compute_metric

ToolSpec = dict[str, Any]


def _params(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_NULL_STR = {"type": ["string", "null"]}
_NULL_NUM = {"type": ["number", "null"]}

TOOL_SPECS: list[ToolSpec] = [
    {
        "name": "list_metrics",
        "description": "List the metrics the verifier can recompute, with kind and subject types.",
        "parameters": _params({}),
    },
    {
        "name": "compute_metric",
        "description": (
            "Recompute one metric from the event log for a subject over a window of match seconds "
            "(start_t to end_t, inclusive). Returns value, n (events considered), k (events counted) "
            "and evidence_ids. Windows may not end after now."
        ),
        "parameters": _params(
            {
                "metric": {"type": "string", "enum": sorted(REGISTRY)},
                "subject_type": {"type": "string", "enum": ["team", "player", "match"]},
                "subject_id": {"type": "string"},
                "start_t": {"type": "number"},
                "end_t": {"type": "number"},
            }
        ),
    },
    {
        "name": "list_events",
        "description": (
            "List events up to now, filtered by type (goal, shot, pass, carry, substitution, card, "
            "possession_change, pressure, tackle), team, player_id and a window in match seconds. "
            "Null means no filter. Returns the most recent matches, oldest first."
        ),
        "parameters": _params(
            {
                "type": _NULL_STR,
                "team": _NULL_STR,
                "player_id": _NULL_STR,
                "start_t": _NULL_NUM,
                "end_t": _NULL_NUM,
            }
        ),
    },
]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: str  # JSON, as the model sent it
    output: str  # JSON, as the tool returned it


@dataclass
class ToolBox:
    """The tools for one request: the match up to `now`."""

    match: Match
    ctx: MetricContext
    now: float
    max_events: int
    calls: list[ToolCall] = field(default_factory=list)

    def call(self, name: str, arguments: str) -> str:
        """Run one tool and record it. Bad arguments give an error message, never an exception."""
        try:
            args = json.loads(arguments or "{}")
            out = self._dispatch(name, args)
        except (ValueError, KeyError, TypeError) as e:
            out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        text = json.dumps(out, ensure_ascii=False, sort_keys=True)
        self.calls.append(ToolCall(name, arguments, text))
        return text

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "list_metrics":
            return {"metrics": list_metrics()}
        if name == "compute_metric":
            return self._compute(args)
        if name == "list_events":
            return self._events(args)
        return {"error": f"unknown tool {name}"}

    def _known(self) -> list[Any]:
        return [e for e in self.match.events if e.t <= self.now]

    def _compute(self, a: dict[str, Any]) -> dict[str, Any]:
        start, end = float(a["start_t"]), float(a["end_t"])
        if end > self.now:
            return {"error": f"the window ends at {end}, after now ({self.now})"}
        subject = Subject(type=a["subject_type"], id=str(a["subject_id"]))
        w = Window(start_t=start, end_t=end)
        m = compute_metric(str(a["metric"]), subject, w, self._known(), self.ctx)
        if m is None:
            return {"error": f"{a['metric']} is not computable for that subject and window"}
        return {"value": round(m.value, 4), "n": m.n, "k": m.k, "evidence_ids": m.ids}

    def _events(self, a: dict[str, Any]) -> dict[str, Any]:
        lo = -1.0 if a.get("start_t") is None else float(a["start_t"])
        hi = self.now if a.get("end_t") is None else min(self.now, float(a["end_t"]))
        found = [
            e
            for e in self._known()
            if lo <= e.t <= hi
            and all(
                a.get(k) is None or getattr(e, k) == a[k] for k in ("type", "team", "player_id")
            )
        ]
        shown = found[-self.max_events :]
        clock = self.match.meta.clock
        return {
            "total": len(found),
            "shown": len(shown),
            "events": [
                {
                    "id": e.id,
                    "t": e.t,
                    "minute": clock.minute(e.t),
                    "type": e.type,
                    "team": e.team,
                    "player_id": e.player_id,
                    **({"outcome": e.outcome} if e.outcome else {}),
                    **({"xg": e.xg} if e.xg is not None else {}),
                }
                for e in shown
            ],
        }


def list_metrics() -> list[dict[str, Any]]:
    return [
        {"name": name, "kind": d.kind, "subjects": sorted(d.subjects)}
        for name, d in sorted(REGISTRY.items())
    ]
