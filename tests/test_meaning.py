"""P12: the Reader, the meaning_matches check, its retry, the audit trail and the labelled set.
No network: the Reader here is a fake caller or a cache."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents_foundry import CallResult, Scout, run_scout, scout_metrics
from app.drafter import draft_facts, render
from app.meaning import check_meaning, meaning_problems
from app.reader import Reader, ReadResult, parse_reading, reader_request
from app.scout_tools import ToolBox
from app.verifier import verify
from core.config import load
from core.events import Match
from core.facts import Claim, Fact, Reading
from core.metrics import REGISTRY
from core.moments import detect_moments
from scripts.eval_meaning import load_cases, scores
from tests.conftest import LEAGUE

S = load()
Moment = dict[str, Any]


def _claim(metric: str = "attack_share_centre", sid: str = "NOR", start: float = 600.0) -> Claim:
    return Claim.model_validate(
        {
            "subject": {"type": "team", "id": sid},
            "metric": metric,
            "window": {"start_t": start, "end_t": start + 900},
            "comparator": "eq",
            "value": 69.0,
        }
    )


def _reading(**kw: Any) -> Reading:
    base: dict[str, Any] = {
        "subject": {"type": "team", "id": "NOR"},
        "metric": "attack_share_centre",
        "window": {"kind": "since_minute", "minute": 11},
        "value": 69.0,
    }
    return Reading.model_validate({**base, **kw})


# ---- the check -------------------------------------------------------------------------


def test_a_faithful_reading_passes(match: Match) -> None:
    c = check_meaning(_reading(), _claim(), match.meta.clock, S.verifier)
    assert c.name == "meaning_matches" and c.passed
    unstated = _reading(window={"kind": "unstated", "minute": None})
    assert check_meaning(unstated, _claim(), match.meta.clock, S.verifier).passed


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"metric": "attack_share_left"}, "metric attack_share_left"),
        ({"metric": None}, "no listed metric"),
        ({"subject": {"type": "team", "id": "HAR"}}, "subject team HAR"),
        ({"window": {"kind": "since_minute", "minute": 10}}, "window 'since minute 10'"),
        ({"window": {"kind": "second_half", "minute": None}}, "window 'second half'"),
        ({"value": 31.0}, "value 31.0"),
    ],
)
def test_each_difference_fails_the_check(match: Match, change: dict[str, Any], says: str) -> None:
    c = check_meaning(_reading(**change), _claim(), match.meta.clock, S.verifier)
    assert not c.passed and c.detail is not None and says in c.detail


def test_halves_follow_the_clock(match: Match) -> None:
    clock = match.meta.clock
    first = _claim(start=0.0)
    second = _claim(start=clock.half_t)
    fh = _reading(window={"kind": "first_half", "minute": None})
    sh = _reading(window={"kind": "second_half", "minute": None})
    assert not meaning_problems(fh, first, clock, S.verifier)
    assert meaning_problems(sh, first, clock, S.verifier)
    assert not meaning_problems(sh, second, clock, S.verifier)
    assert meaning_problems(fh, second, clock, S.verifier)


def test_value_uses_the_metric_tolerance(match: Match) -> None:
    tol = REGISTRY["attack_share_centre"].tolerance(S.verifier)
    near = _reading(value=69.0 + tol)
    assert not meaning_problems(near, _claim(), match.meta.clock, S.verifier)


# ---- the Reader --------------------------------------------------------------------------


def test_reader_sees_only_the_sentence_and_roster(match: Match) -> None:
    req = reader_request("Harlow had 0.86 xG.", match)
    assert set(req) == {"sentence", "roster"}
    assert set(req["roster"]) == {"NOR", "HAR"}
    assert req["roster"]["HAR"]["players"]["HAR_07"] == "Y. Castell"


def test_reading_is_parsed_strictly() -> None:
    good = _reading().model_dump()
    assert parse_reading(json.dumps(good)) == _reading()
    for bad in ({**good, "value": "69"}, {**good, "extra": 1}, {k: good[k] for k in ("metric",)}):
        with pytest.raises(ValidationError):
            parse_reading(json.dumps(bad))


def _fake_reader(answers: dict[str, Reading | str], log: list[str] | None = None) -> Any:
    def call(user: str) -> ReadResult:
        sentence = json.loads(user)["sentence"]
        if log is not None:
            log.append(sentence)
        a = answers[sentence]
        return ReadResult(a if isinstance(a, str) else a.model_dump_json(), "foundry_agent", "fake")

    return call


def test_reader_caches_replays_and_labels_failures(match: Match) -> None:
    ok, bad = "Northbridge had 0.27 xG in the first half.", "Harlow had 0.86 xG."
    live = Reader(_fake_reader({ok: _reading(), bad: "not json"}), {}, 5)
    assert live.read(ok, match).label == "read" and live.read(bad, match).label == "invalid_output"
    replay = Reader(None, json.loads(json.dumps(live.cache)), 5)
    assert replay.read(ok, match).reading == _reading()
    assert replay.read("Unseen.", match).label == "not_cached"
    assert Reader(_fake_reader({}), {}, 0).read(ok, match).label == "no_budget"


# ---- in the Scout loop ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def moments(match: Match) -> list[Moment]:
    return detect_moments(match.events, ("NOR", "HAR"), LEAGUE, S, match.meta.clock)


def _output(m: Moment, text: str, match: Match) -> str:
    _, nums = render(m, match, S.verifier)
    return json.dumps(
        {
            "claim": {
                "subject": m["subject"],
                "metric": m["metric"],
                "window": m["window"],
                "comparator": "eq",
                "value": float(m["value"]),
            },
            "text": {
                "canonical_en": text,
                "numbers": [{"unit": None, **n.model_dump(exclude_none=True)} for n in nums],
            },
            "evidence_ids": list(m["evidence_ids"]),
        }
    )


def _scout_caller(first: str, retry: str) -> Any:
    def call(user: str, mode: str, tools: ToolBox | None) -> CallResult:
        text = retry if "feedback" in json.loads(user) else first
        return CallResult(text, "foundry_agent", "fake-model", 1, [])

    return call


def _setup(moments: list[Moment], match: Match) -> tuple[Moment, Fact, str, str, Reading]:
    m = next(x for x in moments if x["metric"] == "turnovers_own_third")
    rules = draft_facts([m], match, LEAGUE, S)[0]
    right, _ = render(m, match, S.verifier)
    wrong = right.replace("in their own third", "in the opposition third")
    team, minute = m["subject"]["id"], match.meta.clock.minute(m["window"]["start_t"])
    true = Reading.model_validate(
        {
            "subject": m["subject"],
            "metric": m["metric"],
            "window": {"kind": "since_minute", "minute": minute},
            "value": float(m["value"]),
        }
    )
    assert team and wrong != right
    return m, rules, right, wrong, true


def test_a_meaning_mismatch_is_retried_and_audited(moments: list[Moment], match: Match) -> None:
    m, rules, right, wrong, true = _setup(moments, match)
    misread = true.model_copy(update={"metric": None})
    sent: list[str] = []
    reader = Reader(_fake_reader({wrong: misread, right: true}, sent), {}, 10)
    first, retry = _output(m, wrong, match), _output(m, right, match)
    scout = Scout(_scout_caller(first, retry), {}, 10, reader=reader)
    [fact] = run_scout(scout, [m], [rules], match, LEAGUE, S)
    assert fact.verification.status == "verified" and sent == [wrong, right]
    assert scout_metrics([fact])["after_retry"] == 1
    assert fact.audit is not None
    trail = [(st.agent, st.attempt, st.outcome) for st in fact.audit.steps]
    assert trail == [
        ("Scout", 1, "drafted"),
        ("Reader", 1, "read"),
        ("Verifier", 1, "rejected"),
        ("Scout", 2, "drafted"),
        ("Reader", 2, "read"),
        ("Verifier", 2, "verified"),
    ]
    assert fact.audit.steps[4].reading == true
    names = {c.name: c.passed for c in fact.verification.checks}
    assert names["meaning_matches"] is True
    # Replay from both caches, with no callers, gives the same fact.
    replay_reader = Reader(None, json.loads(json.dumps(reader.cache)), 10)
    replay = Scout(None, json.loads(json.dumps(scout.cache)), 10, reader=replay_reader)
    [again] = run_scout(replay, [m], [rules], match, LEAGUE, S)
    assert again.to_dict() == fact.to_dict()


def test_feedback_names_the_meaning_problem(moments: list[Moment], match: Match) -> None:
    m, rules, right, wrong, true = _setup(moments, match)
    seen: list[dict[str, Any]] = []

    def call(user: str, mode: str, tools: ToolBox | None) -> CallResult:
        seen.append(json.loads(user))
        return CallResult(_output(m, wrong, match), "foundry_agent", "fake", 1, [])

    other_team = "HAR" if true.subject.id == "NOR" else "NOR"
    misread = true.model_copy(
        update={"subject": true.subject.model_copy(update={"id": other_team})}
    )
    reader = Reader(_fake_reader({wrong: misread}), {}, 10)
    [fact] = run_scout(Scout(call, {}, 10, reader=reader), [m], [rules], match, LEAGUE, S)
    assert fact.verification.status == "rejected"
    assert "meaning_matches" in seen[1]["feedback"] and other_team in seen[1]["feedback"]


def test_t0_template_wording_is_not_read(moments: list[Moment], match: Match) -> None:
    g = next(x for x in moments if x["metric"] == "goals")
    rules = draft_facts([g], match, LEAGUE, S)[0]
    text, _ = render(g, match, S.verifier)
    log: list[str] = []
    reader = Reader(_fake_reader({}, log), {}, 10)
    scout = Scout(_scout_caller(_output(g, text, match), ""), {}, 10, reader=reader)
    [fact] = run_scout(scout, [g], [rules], match, LEAGUE, S)
    assert log == [] and "meaning_matches" not in {c.name for c in fact.verification.checks}


def test_without_a_reading_the_verifier_is_unchanged(moments: list[Moment], match: Match) -> None:
    m, rules, *_ = _setup(moments, match)
    plain = verify(rules, match, LEAGUE, S)
    assert "meaning_matches" not in {c.name for c in plain.verification.checks}


# ---- the labelled set and its scores ----------------------------------------------------------


def _written(v: float) -> str:
    return f"{v:.2f}" if v != int(v) else str(int(v))


def test_labelled_set_is_big_balanced_and_right_in_numbers() -> None:
    cases = load_cases()
    labels = [c["label"] for c in cases]
    assert len(cases) >= 60 and labels.count("same") >= 25 and labels.count("different") >= 25
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        claim = Claim.model_validate(c["claim"])
        assert claim.metric in REGISTRY and c["label"] in ("same", "different")
        if REGISTRY[claim.metric].kind != "rank":  # the rank (1) is never written
            assert _written(claim.value) in c["sentence"], c["id"]


def test_scores_count_flags_against_labels() -> None:
    rows = [
        {"label": "different", "flagged": True, "read": "read"},
        {"label": "different", "flagged": False, "read": "read"},
        {"label": "same", "flagged": True, "read": "read"},
        {"label": "same", "flagged": False, "read": "not_cached"},
    ]
    got = scores(rows)
    assert (got["tp"], got["fp"], got["fn"], got["tn"], got["unread"]) == (1, 1, 1, 1, 1)
    assert got["precision"] == 0.5 and got["recall"] == 0.5
