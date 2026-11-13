"""P13: typed broadcast cards on cues, their layouts and their number check."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.build_bundle import build
from app.cards import LABELS, layout_for, other_value
from app.renditions import card_numbers_ok
from core.config import MetricContext, load
from core.events import Match
from core.facts import Fact, Rendition, StatComparisonCard, Subject
from core.metrics import compute_metric

S = load()


@pytest.fixture(scope="module")
def hero() -> dict[str, Any]:
    return build(S)[0]


@pytest.fixture(scope="module")
def facts(hero: dict[str, Any]) -> list[Fact]:
    return [Fact.model_validate(f) for f in hero["facts"]]


def _cue(f: Fact, lang: str = "en") -> Rendition:
    return next(r for r in f.renditions or [] if r.kind == "cue" and r.lang == lang)


def _first(facts: list[Fact], metric: str) -> Fact:
    return next(f for f in facts if f.claim.metric == metric)


def test_every_cue_carries_a_card_of_its_layout(facts: list[Fact]) -> None:
    for f in facts:
        for lang in ("en", "es"):
            r = _cue(f, lang)
            assert r.card is not None and r.card.layout == r.layout
            assert r.card.text == r.text


@pytest.mark.parametrize(
    ("metric", "layout"),
    [
        ("xg_sum", "stat_comparison"),
        ("pass_accuracy", "stat_comparison"),
        ("shots", "player_card"),
        ("xt_added", "player_card"),
        ("most_central_player", "player_card"),
        ("top_shot_speed", "ticker"),
        ("turnovers_own_third", "lower_third"),
        ("goals", "lower_third"),
    ],
)
def test_layout_follows_config(facts: list[Fact], metric: str, layout: str) -> None:
    f = _first(facts, metric)
    assert layout_for(f, S) == layout == _cue(f).layout


def test_comparison_recomputes_the_other_team(facts: list[Fact], match: Match) -> None:
    f = _first(facts, "pass_accuracy")
    card = _cue(f).card
    assert isinstance(card, StatComparisonCard) and card.subject == f.claim.subject.id
    other = "HAR" if f.claim.subject.id == "NOR" else "NOR"
    known = [e for e in match.events if e.t <= f.created_t]
    m = compute_metric(
        f.claim.metric,
        Subject(type="team", id=other),
        f.claim.window,
        known,
        MetricContext.of(S),
    )
    assert m is not None and other_value(f, match, S) == m.value
    them = card.away if other == card.away.team else card.home
    assert them.shown == f"{round(m.value)}%"
    # The paired half-time fact about the other team states the same value.
    pair = next(
        g for g in facts if g.claim.metric == f.claim.metric and g.claim.subject.id == other
    )
    assert them.shown == f"{round(pair.claim.value)}%"


def test_player_card_numbers_come_from_the_sentence(facts: list[Fact]) -> None:
    f = _first(facts, "most_central_player")
    card = _cue(f).card
    nums = {n.key: round(n.value) for n in f.text.numbers}
    assert card is not None and card.layout == "player_card"
    assert card.figure == f"{nums['k']} of {nums['n']}"
    assert _cue(f, "es").card.figure == f"{nums['k']} de {nums['n']}"  # type: ignore[union-attr]


def test_a_wrong_number_on_a_card_fails_the_check(facts: list[Fact], match: Match) -> None:
    for metric in ("xg_sum", "shots"):
        f = _first(facts, metric)
        card = _cue(f).card
        assert card is not None and card_numbers_ok(card, f, match, S, "en")
        if isinstance(card, StatComparisonCard):
            bad = card.model_copy(update={"home": card.home.model_copy(update={"shown": "9.99"})})
        else:
            bad = card.model_copy(update={"figure": "99"})
        assert not card_numbers_ok(bad, f, match, S, "en")


def test_card_layout_must_match_the_rendition(facts: list[Fact]) -> None:
    r = _cue(_first(facts, "shots"))
    with pytest.raises(ValidationError):
        Rendition.model_validate({**r.model_dump(), "layout": "lower_third"})


def test_labels_cover_every_card_metric() -> None:
    r = S.renditions
    assert set(r.comparison_metrics) | set(r.player_card_metrics) <= set(LABELS)
    assert all(not any(ch.isdigit() for ch in en + es) for en, es in LABELS.values())
