"""P11: structured output, read-only tools, explore mode, replay checks and memory. No network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents_foundry import CallResult, Scout, run_scout, scout_metrics
from app.drafter import draft_facts, render
from app.scout_memory import duplicates, memory_for
from app.scout_schema import ScoutOutputError, output_schema, parse_scout
from app.scout_tools import TOOL_SPECS, ToolBox
from core.config import MetricContext, load
from core.events import Match
from core.facts import Fact, Subject, Window
from core.metrics import compute_metric
from core.moments import detect_moments
from tests.conftest import LEAGUE

S = load()
CTX = MetricContext.of(S)
Moment = dict[str, Any]


@pytest.fixture(scope="module")
def moments(match: Match) -> list[Moment]:
    return detect_moments(match.events, ("NOR", "HAR"), LEAGUE, S, match.meta.clock)


def _rules(ms: list[Moment], match: Match) -> list[Fact]:
    return draft_facts(ms, match, LEAGUE, S)[: len(ms)]


def _turnovers(moments: list[Moment]) -> list[Moment]:
    return [m for m in moments if m["metric"] == "turnovers_own_third"][:2]


def _output(m: Moment, match: Match, value: float, ids: list[int]) -> dict[str, Any]:
    text, nums = render(m, match, S.verifier)
    return {
        "claim": {
            "subject": m["subject"],
            "metric": m["metric"],
            "window": m["window"],
            "comparator": "eq",
            "value": value,
        },
        "text": {
            "canonical_en": text,
            "numbers": [{"unit": None, **n.model_dump(exclude_none=True)} for n in nums],
        },
        "evidence_ids": ids,
    }


# ---- schema ------------------------------------------------------------------------


def _all_objects(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        own = [node] if node.get("type") == "object" else []
        return own + [o for v in node.values() for o in _all_objects(v)]
    if isinstance(node, list):
        return [o for v in node for o in _all_objects(v)]
    return []


def test_schema_is_strict_for_structured_output() -> None:
    objs = _all_objects(output_schema())
    assert len(objs) >= 6
    for o in objs:
        assert o["additionalProperties"] is False and sorted(o["required"]) == sorted(
            o["properties"]
        )
    for spec in TOOL_SPECS:
        p = spec["parameters"]
        assert p["additionalProperties"] is False and sorted(p["required"]) == sorted(
            p["properties"]
        )


@pytest.mark.parametrize(
    "bad",
    [
        "```json\n{}\n```",  # fences are not stripped any more
        "not json",
        '{"claim": {}, "text": {}, "evidence_ids": []}',
    ],
)
def test_invalid_replies_raise_a_typed_error(bad: str) -> None:
    with pytest.raises(ScoutOutputError):
        parse_scout(bad)


def test_strict_types_and_no_extra_keys(moments: list[Moment], match: Match) -> None:
    m = _turnovers(moments)[0]
    good = _output(m, match, float(m["value"]), list(m["evidence_ids"]))
    assert parse_scout(json.dumps(good)).claim.metric == m["metric"]
    as_text = json.loads(json.dumps(good))
    as_text["claim"]["value"] = "3"
    extra = {**good, "note": "hi"}
    for bad in (as_text, extra):
        with pytest.raises(ScoutOutputError):
            parse_scout(json.dumps(bad))


# ---- tools ---------------------------------------------------------------------------


def test_compute_metric_tool_is_the_registry_and_never_sees_the_future(match: Match) -> None:
    box = ToolBox(match, CTX, 1800.0, 40)
    args = {
        "metric": "shots",
        "subject_type": "team",
        "subject_id": "NOR",
        "start_t": 0,
        "end_t": 1800,
    }
    out = json.loads(box.call("compute_metric", json.dumps(args)))
    m = compute_metric(
        "shots", Subject(type="team", id="NOR"), Window(start_t=0, end_t=1800), match.events, CTX
    )
    assert m is not None and (out["value"], out["n"], out["evidence_ids"]) == (m.value, m.n, m.ids)
    late = json.loads(box.call("compute_metric", json.dumps({**args, "end_t": 1801})))
    assert "after now" in late["error"]
    assert "error" in json.loads(box.call("compute_metric", '{"metric": "shots"}'))
    assert "error" in json.loads(box.call("delete_everything", "{}"))
    assert len(box.calls) == 4


def test_list_events_filters_caps_and_stops_at_now(match: Match) -> None:
    box = ToolBox(match, CTX, 2000.0, 5)
    out = json.loads(
        box.call(
            "list_events",
            json.dumps(
                {"type": "pass", "team": "HAR", "player_id": None, "start_t": None, "end_t": None}
            ),
        )
    )
    assert out["shown"] == 5 and out["total"] > 5
    assert all(e["type"] == "pass" and e["team"] == "HAR" and e["t"] <= 2000 for e in out["events"])
    assert [e["t"] for e in out["events"]] == sorted(e["t"] for e in out["events"])
    names = {m["name"] for m in json.loads(box.call("list_metrics", "{}"))["metrics"]}
    assert {"most_central_player", "xt_added", "goals"} <= names


# ---- the loop -------------------------------------------------------------------------


def _caller(answer: Any, log: list[dict[str, Any]] | None = None) -> Any:
    def call(user: str, mode: str, tools: ToolBox | None) -> CallResult:
        req = json.loads(user)
        if log is not None:
            log.append(req)
        text = answer(req, tools)
        return CallResult(
            text,
            "foundry_agent",
            "fake-model",
            1 + len(tools.calls if tools else []),
            list(tools.calls) if tools else [],
        )

    return call


def test_invalid_reply_is_retried_once_then_falls_back(moments: list[Moment], match: Match) -> None:
    ms = _turnovers(moments)[:1]
    m = ms[0]
    good = json.dumps(_output(m, match, float(m["value"]), list(m["evidence_ids"])))
    sent: list[dict[str, Any]] = []
    scout = Scout(_caller(lambda r, _t: good if "feedback" in r else "oops", sent), {}, 40)
    [fact] = run_scout(scout, ms, _rules(ms, match), match, LEAGUE, S)
    assert fact.verification.status == "verified" and "invalid" in sent[1]["feedback"]
    assert scout_metrics([fact])["after_retry"] == 1
    twice = Scout(_caller(lambda r, _t: "oops"), {}, 40)
    [fallback] = run_scout(twice, ms, _rules(ms, match), match, LEAGUE, S)
    assert scout_metrics([fallback])["rules"] == 1 and twice.calls == 2
    assert fallback.audit is not None
    assert [s.outcome[:14] for s in fallback.audit.steps][:2] == ["invalid_output"] * 2


def _explorer(moments: list[Moment], match: Match) -> Any:
    """A fake explore-mode Scout: reads the lead, asks compute_metric, words the answer."""
    by_lead = {json.dumps([m["subject"], m["window"]], sort_keys=True): m for m in moments}

    def answer(req: dict[str, Any], tools: ToolBox | None) -> str:
        assert tools is not None and "numbers" not in req and "evidence_ids" not in req
        lead = req["lead"]
        m = by_lead[json.dumps([lead["subject"], lead["window"]], sort_keys=True)]
        w = m["window"]
        got = json.loads(
            tools.call(
                "compute_metric",
                json.dumps(
                    {
                        "metric": m["metric"],
                        "subject_type": m["subject"]["type"],
                        "subject_id": m["subject"]["id"],
                        "start_t": w["start_t"],
                        "end_t": w["end_t"],
                    }
                ),
            )
        )
        return json.dumps(_output(m, match, float(got["value"]), got["evidence_ids"]))

    return answer


def test_explore_mode_finds_numbers_with_tools_and_replays(
    moments: list[Moment], match: Match
) -> None:
    ms = _turnovers(moments)
    live = Scout(_caller(_explorer(moments, match)), {}, 40)
    facts = run_scout(live, ms, _rules(ms, match), match, LEAGUE, S, "explore")
    assert [f.verification.status for f in facts] == ["verified", "verified"]
    steps = [s for f in facts for s in (f.audit.steps if f.audit else []) if s.agent == "Scout"]
    assert all(s.mode == "explore" and s.tool_calls == 1 for s in steps)
    cache = json.loads(json.dumps(live.cache))
    replay = run_scout(Scout(None, cache, 40), ms, _rules(ms, match), match, LEAGUE, S, "explore")
    assert [f.to_dict() for f in replay] == [f.to_dict() for f in facts]
    # A record whose tool output no longer matches the event log is refused, not trusted.
    for rec in cache.values():
        rec["tool_calls"][0]["output"] = rec["tool_calls"][0]["output"].replace('"k"', '"kk"')
    stale = run_scout(Scout(None, cache, 40), ms, _rules(ms, match), match, LEAGUE, S, "explore")
    assert scout_metrics(stale)["rules"] == 2
    assert stale[0].audit is not None and stale[0].audit.steps[0].outcome == "replay_mismatch"


def test_explore_may_not_invent_a_t0_claim(moments: list[Moment], match: Match) -> None:
    ms = _turnovers(moments)[:1]
    goal = next(m for m in moments if m["metric"] == "goals")
    bad = json.dumps(_output(goal, match, float(goal["value"]), list(goal["evidence_ids"])))
    scout = Scout(_caller(lambda r, _t: bad), {}, 40)
    [fact] = run_scout(scout, ms, _rules(ms, match), match, LEAGUE, S, "explore")
    assert scout_metrics([fact])["rules"] == 1
    assert fact.audit is not None and "lead itself" in fact.audit.steps[0].outcome


# ---- memory ---------------------------------------------------------------------------


def test_memory_carries_earlier_facts_and_is_audited(moments: list[Moment], match: Match) -> None:
    ms = _turnovers(moments)
    sent: list[dict[str, Any]] = []
    answer = _explorer(moments, match)
    scout = Scout(_caller(answer, sent), {}, 40)
    facts = run_scout(scout, ms, _rules(ms, match), match, LEAGUE, S, "explore")
    assert sent[0]["memory"]["aired"] == []
    assert [a["fact_id"] for a in sent[1]["memory"]["aired"]] == [facts[0].fact_id]
    assert facts[1].audit is not None and facts[1].audit.memory == [facts[0].fact_id]
    mem = memory_for(facts, float(ms[1]["created_t"]), match, 1)
    team = ms[0]["subject"]["id"]
    assert mem["last_by_team"][team] == facts[-1].text.canonical_en


def test_duplicates_count_repeats_of_aired_facts(moments: list[Moment], match: Match) -> None:
    ms = _turnovers(moments)[:1]
    [f] = run_scout(
        Scout(_caller(_explorer(moments, match)), {}, 40),
        ms,
        _rules(ms, match),
        match,
        LEAGUE,
        S,
        "explore",
    )
    again = f.model_copy(deep=True)
    again.fact_id = "f_999999"
    assert duplicates([f]) == 0 and duplicates([f, again]) == 1
