"""Verifier acceptance tests, driven by tests/seeded_claims.json (SPEC sections 7, 8, 15)."""
from __future__ import annotations

import copy
from typing import Any

import pytest

from app.verifier import verify
from core.config import load
from tests.conftest import seeded_of

CATEGORIES = ("wrong_number", "wrong_player", "wrong_window", "future_event", "intent_word", "small_sample")


def _check(fact: dict[str, Any], name: str) -> bool:
    return next(c["passed"] for c in fact["verification"]["checks"] if c["name"] == name)


def _ids(entries: list[dict[str, Any]]) -> list[str]:
    return [e["id"] for e in entries]


def test_seeded_set_has_enough_per_category(seeded: list[dict[str, Any]]) -> None:
    for cat in CATEGORIES:
        assert len([c for c in seeded if c["category"] == cat]) >= 5, cat
    assert len([c for c in seeded if c["category"] == "good"]) >= 10
    assert all(c["fact"]["injected"] for c in seeded if c["category"] != "good")


@pytest.mark.parametrize("entry", seeded_of("wrong_number"), ids=_ids(seeded_of("wrong_number")))
def test_verifier_rejects_wrong_number(entry: dict[str, Any], match: dict[str, Any]) -> None:
    out = verify(entry["fact"], match)
    assert out["verification"]["status"] == "rejected"
    assert not _check(out, "recompute") or not _check(out, "numbers_match")


@pytest.mark.parametrize("entry", seeded_of("future_event"), ids=_ids(seeded_of("future_event")))
def test_verifier_rejects_future_event(entry: dict[str, Any], match: dict[str, Any]) -> None:
    out = verify(entry["fact"], match)
    assert out["verification"]["status"] == "rejected"
    assert not _check(out, "evidence_exists")


@pytest.mark.parametrize("entry", seeded_of("wrong_player"), ids=_ids(seeded_of("wrong_player")))
def test_verifier_rejects_wrong_player(entry: dict[str, Any], match: dict[str, Any]) -> None:
    out = verify(entry["fact"], match)
    assert out["verification"]["status"] == "rejected"
    assert not _check(out, "entities")


@pytest.mark.parametrize("entry", seeded_of("wrong_window"), ids=_ids(seeded_of("wrong_window")))
def test_verifier_rejects_wrong_window(entry: dict[str, Any], match: dict[str, Any]) -> None:
    out = verify(entry["fact"], match)
    assert out["verification"]["status"] == "rejected"
    assert not _check(out, "evidence_exists")


@pytest.mark.parametrize("entry", seeded_of("intent_word"), ids=_ids(seeded_of("intent_word")))
def test_intent_words_stripped_and_logged(entry: dict[str, Any], match: dict[str, Any]) -> None:
    out = verify(entry["fact"], match)
    v = out["verification"]
    assert v["status"] == "corrected"
    assert any(c["reason"].startswith("intent") for c in v["corrections"])
    text = out["text"]["canonical_en"].lower()
    for word in ("deliberately", "tried to", "wanted to", "decided to"):
        assert word not in text
    assert out["text"]["draft_en"] == entry["fact"]["text"]["draft_en"]


@pytest.mark.parametrize("entry", seeded_of("small_sample"), ids=_ids(seeded_of("small_sample")))
def test_small_sample_downgrades_hedge(entry: dict[str, Any], match: dict[str, Any]) -> None:
    out = verify(entry["fact"], match)
    if out["verification"]["status"] == "unverifiable":
        return
    assert out["uncertainty"]["hedge"] == "early"
    assert out["uncertainty"]["n"] < load()["verifier"]["n_min"]
    assert "early sign" in out["text"]["canonical_en"].lower()


@pytest.mark.parametrize("entry", seeded_of("good"), ids=_ids(seeded_of("good")))
def test_good_claims_verify(entry: dict[str, Any], match: dict[str, Any]) -> None:
    out = verify(entry["fact"], match)
    assert out["verification"]["status"] == "verified", out["verification"]
    assert all(c["passed"] for c in out["verification"]["checks"])


def test_verify_does_not_mutate_input(seeded: list[dict[str, Any]], match: dict[str, Any]) -> None:
    fact = seeded[0]["fact"]
    before = copy.deepcopy(fact)
    verify(fact, match)
    assert fact == before


def test_unknown_metric_is_unverifiable(seeded: list[dict[str, Any]], match: dict[str, Any]) -> None:
    fact = copy.deepcopy(seeded_of("good")[0]["fact"])
    fact["claim"]["metric"] = "duels_lost"
    assert verify(fact, match)["verification"]["status"] == "unverifiable"
