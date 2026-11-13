"""Metric registry, and the verifier split into pure checks, corrections and an orchestrator."""

from __future__ import annotations

from typing import get_args

import pytest

from app.checks import (
    assess_sample,
    check_entities,
    check_evidence,
    check_numbers,
    check_recompute,
    check_sample,
)
from app.corrections import hedge_text, strip_intent
from core.config import MetricContext, load
from core.events import Match
from core.facts import MetricName, Subject, Window
from core.metrics import REGISTRY, compute_metric
from tests.conftest import SeededClaim, seeded_of

S = load()
CTX = MetricContext.of(S)
NOR = Subject(type="team", id="NOR")
WHOLE = Window(start_t=0.0, end_t=5400.0)


def test_registry_lists_every_computable_metric() -> None:
    assert set(REGISTRY) == {
        "goals",
        "shots",
        "substitutions",
        "cards",
        "turnovers_own_third",
        "xg_sum",
        "top_shot_speed",
        "pass_accuracy",
        "attack_share_left",
        "attack_share_centre",
        "attack_share_right",
        "xt_added",
    }
    assert set(REGISTRY) <= set(get_args(MetricName))
    assert all(name == d.name for name, d in REGISTRY.items())


@pytest.mark.parametrize("name", sorted(set(get_args(MetricName)) - set(REGISTRY)))
def test_metric_without_a_definition_is_not_computable(name: str, match: Match) -> None:
    assert compute_metric(name, NOR, WHOLE, match.events, CTX) is None


def test_kinds_and_tolerances_come_from_config() -> None:
    v = S.verifier
    expected = {
        "goals": ("count", 0.0),
        "turnovers_own_third": ("count", 0.0),
        "xg_sum": ("sum", v.tol_xg),
        "top_shot_speed": ("max", v.tol_speed),
        "xt_added": ("sum", v.tol_xt),
        "pass_accuracy": ("share", v.tol_share_pp),
        "attack_share_left": ("share", v.tol_share_pp),
    }
    for name, (kind, tol) in expected.items():
        assert (REGISTRY[name].kind, REGISTRY[name].tolerance(v)) == (kind, tol)
    assert {n for n, d in REGISTRY.items() if d.checks_sample} == {
        "pass_accuracy",
        "attack_share_left",
        "attack_share_centre",
        "attack_share_right",
    }


def test_value_matches_uses_the_tolerance() -> None:
    v, xg = S.verifier, REGISTRY["xg_sum"]
    assert xg.value_matches(1.00, 1.00 + v.tol_xg, v)
    assert not xg.value_matches(1.00, 1.01 + v.tol_xg, v)
    assert not REGISTRY["goals"].value_matches(1, 2, v)


def test_attack_share_is_only_defined_for_teams(match: Match) -> None:
    player = Subject(type="player", id="NOR_09")
    assert compute_metric("attack_share_left", player, WHOLE, match.events, CTX) is None
    assert compute_metric("shots", player, WHOLE, match.events, CTX) is not None


def test_counts_match_the_event_log(match: Match) -> None:
    m = compute_metric("goals", Subject(type="match", id="match"), WHOLE, match.events, CTX)
    assert m is not None
    assert m.value == sum(e.type == "goal" for e in match.events)


# ---- the split -------------------------------------------------------------------------


@pytest.mark.parametrize("entry", seeded_of("good")[:6] + seeded_of("small_sample")[:3])
def test_checks_and_corrections_do_not_change_the_fact(entry: SeededClaim, match: Match) -> None:
    fact, v = entry.fact, S.verifier
    before = fact.model_copy(deep=True)
    by_id = {e.id: e for e in match.events}
    d = REGISTRY[fact.claim.metric]
    m = compute_metric(fact.claim.metric, fact.claim.subject, fact.claim.window, match.events, CTX)
    assert m is not None
    check_evidence(fact, by_id)
    check_entities(fact, by_id, match, v)
    check_recompute(fact.claim, m, d, v)
    check_numbers(fact, m, d, by_id, match, v)
    sample = assess_sample(fact.claim, m, d, v)
    check_sample(sample, None)
    hedge_text(fact, m, sample, match, v)
    strip_intent(fact.text.canonical_en, v.intent_patterns)
    assert fact == before


def test_small_sample_is_restated_by_a_correction(match: Match) -> None:
    fact = seeded_of("small_sample")[0].fact
    d = REGISTRY[fact.claim.metric]
    m = compute_metric(fact.claim.metric, fact.claim.subject, fact.claim.window, match.events, CTX)
    assert m is not None
    fix = hedge_text(fact, m, assess_sample(fact.claim, m, d, S.verifier), match, S.verifier)
    assert fix is not None and fix.text.startswith("Early sign:")
    assert fix.correction.from_ == fact.text.canonical_en and fix.correction.to == fix.text


def test_strip_intent_reports_what_it_removed() -> None:
    text, fixes = strip_intent("They deliberately went left.", S.verifier.intent_patterns)
    assert "deliberately" not in text and len(fixes) == 1
    assert strip_intent("They went left.", S.verifier.intent_patterns) == ("They went left.", [])
