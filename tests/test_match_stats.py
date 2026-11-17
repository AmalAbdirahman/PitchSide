"""R3: npxG by interval, shot outcomes and result probability, from registry to bundle."""

from __future__ import annotations

from typing import Any

import pytest

from app.build_bundle import build
from app.drafter import RULES_ONLY_FIRST_ID, draft_facts, scout_can_draft
from app.series import build_series, whole_percent
from app.templates_es import caption_es
from core.config import MetricContext, load
from core.events import Clock, Event, Match
from core.facts import AGENT_METRICS, Subject, Window
from core.intervals import intervals
from core.metrics import require_metric
from core.moments import detect_moments
from core.winprob import win_probability
from tests.conftest import LEAGUE

S = load()
CTX = MetricContext.of(S)
TEAMS = ("NOR", "HAR")
WHOLE = Window(start_t=0.0, end_t=5400.0)
OUTCOMES = ("shots_on_target", "shots_off_target", "shots_blocked")


def _shot(i: int, outcome: str) -> Event:
    return Event(
        id=i, t=float(i), period=1, type="shot", team="NOR", player_id="NOR_9",
        x=95, y=34, outcome=outcome, xg=0.1,
    )  # fmt: skip


@pytest.fixture(scope="module")
def moments(match: Match) -> list[dict[str, Any]]:
    return detect_moments(match.events, TEAMS, LEAGUE, S, match.meta.clock)


@pytest.fixture(scope="module")
def series(match: Match) -> dict[str, Any]:
    return build_series(match.events, TEAMS, LEAGUE, S, match.meta.clock)


# ---- intervals -----------------------------------------------------------------------------


def test_regulation_has_six_blocks_with_stoppage_in_the_last_of_each_half() -> None:
    blocks = intervals(Clock(period_starts=[0, 2820], end_t=5700), 15)
    assert [b.label for b in blocks] == ["1–15", "16–30", "31–45+", "46–60", "61–75", "76–90+"]
    assert blocks[2].start_t == 1800 and blocks[2].end_t == pytest.approx(2819.999)
    assert blocks[3].start_t == 2820 and blocks[-1].end_t == 5700


def test_blocks_do_not_overlap_and_cover_every_event(match: Match) -> None:
    blocks = intervals(match.meta.clock, 15)
    assert all(a.end_t < b.start_t for a, b in zip(blocks, blocks[1:], strict=False))
    for e in match.events:
        assert sum(b.start_t <= e.t <= b.end_t for b in blocks) == 1, e.id


def test_extra_time_periods_are_one_block_each() -> None:
    blocks = intervals(Clock(period_starts=[0, 2700, 5400, 6300], end_t=7200), 15)
    assert [b.label for b in blocks][-2:] == ["91–105+", "106–120+"]


# ---- shot outcomes -------------------------------------------------------------------------


def test_a_goal_is_on_target_and_n_counts_every_shot() -> None:
    evs = [_shot(1, "goal"), _shot(2, "on_target"), _shot(3, "off_target"), _shot(4, "blocked")]
    nor = Subject(type="team", id="NOR")
    on = require_metric("shots_on_target", nor, WHOLE, evs, CTX)
    assert (on.value, on.k, on.n, on.ids) == (2, 2, 4, [1, 2])
    assert require_metric("shots_blocked", nor, WHOLE, evs, CTX).ids == [4]


def test_outcomes_add_up_to_shots(match: Match) -> None:
    for team in TEAMS:
        subj = Subject(type="team", id=team)
        total = require_metric("shots", subj, WHOLE, match.events, CTX).value
        parts = [require_metric(m, subj, WHOLE, match.events, CTX).value for m in OUTCOMES]
        assert sum(parts) == total


# ---- moments -------------------------------------------------------------------------------


def test_npxg_interval_moments_recompute_and_meet_the_threshold(
    moments: list[dict[str, Any]], match: Match
) -> None:
    found = [m for m in moments if m["rule"] == "npxg_interval"]
    assert found
    for m in found:
        w, subj = Window(**m["window"]), Subject(**m["subject"])
        assert m["value"] >= S.moments.npxg_interval_min
        assert m["value"] == require_metric("npxg_sum", subj, w, match.events, CTX).value
        assert m["created_t"] == w.end_t


def test_shot_outcome_moments_need_enough_shots(moments: list[dict[str, Any]]) -> None:
    found = [m for m in moments if m["rule"] == "shot_outcomes"]
    assert found and all(m["n"] >= S.moments.shot_outcomes_min_shots for m in found)


def test_new_rules_are_rule_only_and_numbered_apart(
    moments: list[dict[str, Any]], match: Match
) -> None:
    good = draft_facts(moments, match, LEAGUE, S)[: len(moments)]
    scout = [f.fact_id for m, f in zip(moments, good, strict=True) if scout_can_draft(m)]
    rules = [f.fact_id for m, f in zip(moments, good, strict=True) if not scout_can_draft(m)]
    assert scout == [f"f_{i:06d}" for i in range(1, len(scout) + 1)]
    assert rules == [f"f_{i:06d}" for i in range(RULES_ONLY_FIRST_ID, RULES_ONLY_FIRST_ID + 5)]
    assert {"npxg_sum", "shots_on_target"}.isdisjoint(AGENT_METRICS)


def test_new_facts_air_from_rules_with_spanish_captions(match: Match) -> None:
    hero, _ = build(S)
    new = [f for f in hero["facts"] if f["claim"]["metric"] in ("npxg_sum", "shots_on_target")]
    assert len(new) == 5
    for f in new:
        assert f["verification"]["status"] == "verified"
        assert not any(st["agent"] == "Scout" for st in (f.get("audit") or {}).get("steps", []))
    texts = [r["text"] for f in new for r in f["renditions"] if r["lang"] == "es"]
    assert any("npxG desde el minuto" in t for t in texts)
    assert any("remató a puerta" in t for t in texts)


def test_spanish_caption_states_the_text_numbers(match: Match, moments: list[Any]) -> None:
    good = draft_facts(moments, match, LEAGUE, S)[: len(moments)]
    on = next(
        f for f in good if f.claim.metric == "shots_on_target" and f.claim.subject.id == "NOR"
    )
    assert caption_es(on, match).startswith("Northbridge remató a puerta 5 de sus 8 disparos")


# ---- series --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("probs", "pct"),
    [
        ((1 / 3, 1 / 3, 1 / 3), [34, 33, 33]),
        ((0.456, 0.274, 0.27), [46, 27, 27]),
        ((0.004, 0.006, 0.99), [0, 1, 99]),
    ],
)
def test_whole_percent_adds_to_100(probs: tuple[float, ...], pct: list[int]) -> None:
    assert whole_percent(probs) == pct and sum(pct) == 100


def test_result_pct_follows_the_win_probability(series: dict[str, Any]) -> None:
    for wp, pct in zip(series["win_prob"], series["result_pct"], strict=True):
        assert sum(pct) == 100
        assert all(abs(p - 100 * w) <= 1 for p, w in zip(pct, wp, strict=True))


def test_result_pct_at_kick_off_matches_the_model(series: dict[str, Any], match: Match) -> None:
    probs = win_probability(0.0, 0, 0, 0.0, 0.0, LEAGUE.mu, S.winprob)
    assert series["result_pct"][0] == whole_percent(probs)


def test_npxg_by_interval_matches_the_registry(series: dict[str, Any], match: Match) -> None:
    blocks = series["intervals"]
    assert [b["label"] for b in blocks][::5] == ["1–15", "76–90+"]
    for team in TEAMS:
        rows, subj = series["teams"][team]["npxg_by_interval"], Subject(type="team", id=team)
        assert rows[0] == [0.0]  # at kick-off only the first block has started
        for b, v in zip(blocks, rows[-1], strict=True):
            w = Window(start_t=b["start_t"], end_t=b["end_t"])
            assert v == round(require_metric("npxg_sum", subj, w, match.events, CTX).value, 3)
        total = series["teams"][team]["npxg_sum"][-1]
        assert sum(rows[-1]) == pytest.approx(total, abs=0.003)


def test_shot_outcome_series_end_at_the_match_counts(series: dict[str, Any], match: Match) -> None:
    for team in TEAMS:
        subj = Subject(type="team", id=team)
        for m in OUTCOMES:
            final = require_metric(m, subj, WHOLE, match.events, CTX).value
            assert series["teams"][team][m][-1] == final
