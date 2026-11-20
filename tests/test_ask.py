"""Ask PitchSide (R5): the Analyst's answers are checked by code, with a mocked Foundry client."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from app.agents_foundry import CallResult
from app.ask import (
    Analyst,
    answer_question,
    ask_request,
    build_ask,
    cache_key,
    closest_fact,
    load_questions,
)
from app.ask_checks import check_answer
from app.ask_schema import REFUSAL, AnalystOutput, Question, analyst_schema
from app.build_bundle import load_match
from app.scout_tools import ToolBox, ToolCall
from core.config import ROOT, MetricContext, Settings, load
from core.events import Match
from core.facts import Fact, Subject, Window
from core.metrics import compute_metric

S: Settings = load()
MATCH: Match = load_match()[0]
FACTS = [
    Fact.model_validate(f)
    for f in json.loads((ROOT / "docs" / "data" / "hero.json").read_text("utf-8"))["facts"]
]
Q = Question(id="t1", text="How many shots did C. Nkemelu take after 30'?", at_t=3972)
SHOTS_ARGS = {
    "metric": "shots",
    "subject_type": "player",
    "subject_id": "NOR_09",
    "start_t": 1800,
    "end_t": Q.at_t,
}
TRUE = compute_metric(
    "shots",
    Subject(type="player", id="NOR_09"),
    Window(start_t=1800, end_t=Q.at_t),
    [e for e in MATCH.events if e.t <= Q.at_t],
    MetricContext.of(S),
)
assert TRUE is not None
SHOTS = int(TRUE.value)
TRUE_ALL = {  # each player's shot event ids over the match
    p: [e.id for e in MATCH.events if e.type == "shot" and e.player_id == p]
    for p in ("NOR_09", "HAR_09", "HAR_11")
}
assert SHOTS == 2 and TRUE.ids  # 55' and 63'

Script = Callable[[dict[str, Any], ToolBox], dict[str, Any]]


def _num(key: str, value: float, field: str, call: int = 0, **kw: Any) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "call": call,
        "field": field,
        "event_id": None,
        "scale": 1,
        **kw,
    }


def shots_answer(shots: float) -> Script:
    def run(request: dict[str, Any], tools: ToolBox) -> dict[str, Any]:
        out = json.loads(tools.call("compute_metric", json.dumps(SHOTS_ARGS)))
        return {
            "answerable": True,
            "text": f"C. Nkemelu took {shots:g} shots after 30'.",
            "numbers": [_num("shots", shots, "value"), _num("since", 30, "start_minute")],
            "evidence_ids": out["evidence_ids"],
        }

    return run


def refuse(request: dict[str, Any], tools: ToolBox) -> dict[str, Any]:
    tools.call("list_metrics", "{}")
    return {
        "answerable": False,
        "text": "The event log has no distance data.",
        "numbers": [],
        "evidence_ids": [],
    }


class Mock:
    """A stand-in for FoundryAnalyst: runs a script per call, wrong first if asked."""

    def __init__(self, first: Script, retry: Script | None = None) -> None:
        self.first, self.retry, self.requests = first, retry or first, []  # type: list[dict[str, Any]]

    def __call__(self, user: str, tools: ToolBox) -> CallResult:
        request = json.loads(user)
        self.requests.append(request)
        script = self.retry if "feedback" in request else self.first
        reply = script(request, tools)
        return CallResult(json.dumps(reply), "mock", "mock-model", 2, list(tools.calls))


def _run(mock: Mock) -> Any:
    analyst = Analyst(mock, {}, S.ask.max_calls)
    return answer_question(Q, MATCH, FACTS, analyst, S), analyst


def test_a_correct_answer_passes_first_time() -> None:
    answer, _ = _run(Mock(shots_answer(SHOTS)))
    assert answer.status == "verified"
    assert [a.outcome for a in answer.attempts] == ["verified"]
    assert all(n.verified for n in answer.numbers) and len(answer.numbers) == 2
    assert answer.evidence_ids == TRUE.ids
    assert answer.model == "mock-model"


def test_a_wrong_number_is_rejected_then_retried_with_the_reason() -> None:
    mock = Mock(shots_answer(SHOTS + 1), retry=shots_answer(SHOTS))
    answer, _ = _run(mock)
    assert [a.outcome for a in answer.attempts] == ["failed", "verified"]
    failed = {c.name: c for c in answer.attempts[0].checks}
    assert not failed["numbers_match"].passed
    assert "numbers_match" in mock.requests[1]["feedback"]
    assert answer.status == "verified" and answer.text.startswith(f"C. Nkemelu took {SHOTS} ")


def test_two_wrong_answers_store_the_refusal() -> None:
    answer, _ = _run(Mock(shots_answer(SHOTS + 1)))
    assert [a.outcome for a in answer.attempts] == ["failed", "failed"]
    assert answer.status == "refused" and answer.text == REFUSAL and answer.numbers == []


def test_an_unanswerable_question_yields_the_refusal() -> None:
    answer, analyst = _run(Mock(refuse))
    assert answer.status == "refused" and answer.text == REFUSAL
    assert [a.outcome for a in answer.attempts] == ["unanswerable"]
    assert analyst.calls == 2  # one conversation of two responses; no retry


def test_the_refusal_carries_the_closest_verified_fact() -> None:
    calls = [ToolCall("compute_metric", json.dumps({**SHOTS_ARGS, "end_t": 5400}), "{}")]
    near = closest_fact(FACTS, 5400, calls)
    assert near is not None
    fact = next(f for f in FACTS if f.fact_id == near.fact_id)
    assert fact.claim.subject.id == "NOR_09" or fact.claim.metric == "shots"
    assert fact.created_t <= 5400 and fact.text.canonical_en == near.text
    assert closest_fact(FACTS, 5400, []) is None


def test_replay_gives_the_same_answer_with_no_client_and_misses_are_not_built() -> None:
    live, analyst = _run(Mock(shots_answer(SHOTS)))
    replayed = answer_question(Q, MATCH, FACTS, Analyst(None, analyst.cache, S.ask.max_calls), S)
    assert replayed == live
    missing = answer_question(Q, MATCH, FACTS, Analyst(None, {}, S.ask.max_calls), S)
    assert missing.status == "not_built" and missing.attempts[0].outcome == "not_cached"


def test_a_cache_whose_tool_output_changed_is_not_replayed() -> None:
    _, analyst = _run(Mock(shots_answer(SHOTS)))
    ((key, rec),) = analyst.cache.items()
    rec = {**rec, "tool_calls": [{**rec["tool_calls"][0], "output": '{"value": 99}'}]}
    answer = answer_question(Q, MATCH, FACTS, Analyst(None, {key: rec}, S.ask.max_calls), S)
    assert answer.status == "not_built" and answer.attempts[0].outcome == "replay_mismatch"


def test_the_cache_key_covers_instructions_schema_tools_and_input() -> None:
    a, b = ask_request(Q, MATCH), ask_request(Q, MATCH, ("{}", "numbers_match: wrong"))
    assert cache_key(a) != cache_key(b) and cache_key(a) == cache_key(ask_request(Q, MATCH))
    assert analyst_schema()["additionalProperties"] is False


# ---- the checks on their own ------------------------------------------------------------


def _checked(
    text: str,
    numbers: list[dict[str, Any]],
    ids: list[int],
    calls: list[tuple[str, dict[str, Any]]],
    now: float = Q.at_t,
) -> dict[str, bool]:
    tools = ToolBox(MATCH, MetricContext.of(S), now, S.agents.max_events)
    for name, args in calls:
        tools.call(name, json.dumps(args))
    out = AnalystOutput.model_validate(
        {"answerable": True, "text": text, "numbers": numbers, "evidence_ids": ids}
    )
    checks, _ = check_answer(out, tools.calls, MATCH, now, S.verifier)
    return {c.name: c.passed for c in checks}


def test_an_unlisted_number_fails() -> None:
    got = _checked(
        f"C. Nkemelu took {SHOTS} shots after 30'.",
        [_num("shots", SHOTS, "value")],
        [],
        [("compute_metric", SHOTS_ARGS)],
    )
    assert got["numbers_listed"] is False and got["numbers_match"] is True


def test_a_misspelt_or_unseen_name_fails() -> None:
    nums = [_num("shots", SHOTS, "value")]
    assert (
        _checked(f"C. Nkemelo took {SHOTS} shots.", nums, [], [("compute_metric", SHOTS_ARGS)])[
            "entities"
        ]
        is False
    )
    assert (
        _checked(f"D. Varga took {SHOTS} shots.", nums, [], [("compute_metric", SHOTS_ARGS)])[
            "entities"
        ]
        is False
    )
    assert (
        _checked(f"Nkemelu took {SHOTS} shots.", nums, [], [("compute_metric", SHOTS_ARGS)])[
            "entities"
        ]
        is True
    )


def _shots_all(pid: str) -> tuple[str, dict[str, Any]]:
    args = {**SHOTS_ARGS, "subject_id": pid, "start_t": 0, "end_t": 5400}
    return ("compute_metric", args)


def test_a_name_must_belong_to_a_number_or_cited_event() -> None:
    """From the first live Analyst run (q6): every number was right and every name appeared in
    some tool call, but the answer gave the second top scorer's 4 shots to a player with 1."""
    calls = [_shots_all(p) for p in ("NOR_09", "HAR_09", "HAR_11")]
    ev = {p: TRUE_ALL[p] for p in ("NOR_09", "HAR_09")}
    nums = [_num("shots", 4, "k", call=0)]
    ids = ev["NOR_09"] + ev["HAR_09"]
    wrong = "C. Nkemelu and U. Kerridge have each taken the most shots (4)."
    right = "C. Nkemelu and T. Brannock have each taken the most shots (4)."
    assert _checked(wrong, nums, ids, calls, now=5400)["entities"] is False
    assert _checked(right, nums, ids, calls, now=5400)["entities"] is True
    # A name with no number and no cited event is not enough, even if a call asked about them.
    assert _checked(right, nums, ev["NOR_09"], calls, now=5400)["entities"] is False
    # A team is anchored by a player of theirs behind a number or event.
    team = "Harlow's T. Brannock took 4 shots."
    assert _checked(team, [_num("shots", 4, "k", call=1)], [], calls, now=5400)["entities"] is True


def test_evidence_must_come_from_a_tool_call_and_not_from_the_future() -> None:
    later = next(e.id for e in MATCH.events if e.t > Q.at_t)
    other = next(e.id for e in MATCH.events if e.t < 100)
    calls = [("compute_metric", SHOTS_ARGS)]
    assert _checked("Nkemelu took shots.", [], [later], calls)["evidence"] is False
    assert _checked("Nkemelu took shots.", [], [other], calls)["evidence"] is False
    assert _checked("Nkemelu took shots.", [], TRUE.ids, calls)["evidence"] is True


def test_a_window_past_the_question_fails() -> None:
    late = {**SHOTS_ARGS, "end_t": 5400}
    got = _checked(
        f"Nkemelu took {SHOTS} shots.",
        [_num("shots", SHOTS, "value")],
        [],
        [("compute_metric", late)],
    )
    assert got["window"] is False and got["numbers_match"] is False  # the tool refused: no value


def test_rounding_per_cents_and_event_fields() -> None:
    shot = next(e for e in MATCH.events if e.type == "shot" and e.xg)
    args = {"type": "shot", "team": None, "player_id": None, "start_t": None, "end_t": shot.t}
    xg = round(shot.xg or 0, 2)
    minute = MATCH.meta.clock.minute(shot.t)
    name = MATCH.meta.names()[shot.player_id]
    nums = [
        _num("xg", xg, "event_xg", event_id=shot.id),
        _num("minute", minute, "event_minute", event_id=shot.id),
    ]
    text = f"{name}'s shot in {minute}' was worth {xg:.2f} xG."
    assert all(_checked(text, nums, [shot.id], [("list_events", args)], now=shot.t).values())
    wrong = [{**nums[0], "value": round(xg + 0.02, 2)}, nums[1]]
    text = f"{name}'s shot in {minute}' was worth {xg + 0.02:.2f} xG."
    assert (
        _checked(text, wrong, [shot.id], [("list_events", args)], now=shot.t)["numbers_match"]
        is False
    )


# ---- the bundle --------------------------------------------------------------------------


def test_the_questions_include_one_the_log_cannot_answer() -> None:
    qs = load_questions()
    assert 6 <= len(qs) <= 8 and len({q.id for q in qs}) == len(qs)
    assert any("run" in q.text for q in qs)
    assert all(0 < q.at_t <= MATCH.meta.clock.end_t for q in qs)


def test_build_ask_is_stable_and_never_shows_an_unchecked_answer() -> None:
    a = build_ask(MATCH, FACTS, S, Analyst(None, {}, S.ask.max_calls), load_questions())
    b = build_ask(MATCH, FACTS, S, Analyst(None, {}, S.ask.max_calls), load_questions())
    assert a == b
    for ans in a["answers"]:
        assert ans["status"] in ("verified", "refused", "not_built")
        if ans["status"] != "verified":
            assert ans["numbers"] == []


def test_the_committed_ask_json_matches_an_offline_rebuild() -> None:
    from app.ask import load_cache

    path = ROOT / "docs" / "data" / "ask.json"
    built = build_ask(
        MATCH, FACTS, S, Analyst(None, load_cache(), S.ask.max_calls), load_questions()
    )
    assert json.loads(path.read_text("utf-8")) == built


@pytest.mark.parametrize("q", load_questions(), ids=lambda q: q.id)
def test_committed_verified_answers_pass_the_checks_again(q: Any) -> None:
    doc = json.loads((ROOT / "docs" / "data" / "ask.json").read_text("utf-8"))
    ans = next(a for a in doc["answers"] if a["question_id"] == q.id)
    if ans["status"] == "verified":
        assert all(n["verified"] for n in ans["numbers"])
        assert all(c["passed"] for c in ans["attempts"][-1]["checks"])
