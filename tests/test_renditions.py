"""Renditions and the number-preservation check (SPEC section 11)."""

from __future__ import annotations

import re

import pytest

from app.build_bundle import build
from app.renditions import cardinal, numbers_preserved, ordinal_words, speak, words_to_numbers
from core.config import load
from core.facts import Fact

S = load()


@pytest.fixture(scope="module")
def facts() -> list[Fact]:
    return [Fact.model_validate(f, strict=True) for f in build(S)[0]["facts"]]


def _fact(facts: list[Fact], metric: str) -> Fact:
    return next(f for f in facts if f.claim.metric == metric)


def _text(f: Fact, kind: str, lang: str) -> str:
    return next(r.text for r in f.renditions or [] if r.kind == kind and r.lang == lang)


def test_number_preservation_en_es(facts: list[Fact]) -> None:
    for f in facts:
        kinds = {(r.kind, r.lang) for r in f.renditions or []}
        assert kinds == {
            ("cue", "en"),
            ("cue", "es"),
            ("caption", "en"),
            ("caption", "es"),
            ("audio_script", "en"),
        }
        for r in f.renditions or []:
            assert r.numbers_preserved, (f.fact_id, r)


def test_tampered_spanish_fails(facts: list[Fact]) -> None:
    f = _fact(facts, "turnovers_own_third")
    es = _text(f, "caption", "es")
    assert numbers_preserved(es, "caption", "es", f)
    changed = es.replace("desde el minuto ", "desde el minuto 1")
    assert not numbers_preserved(changed, "caption", "es", f)
    dropped = es.split(" desde")[0] + "."
    assert not numbers_preserved(dropped, "caption", "es", f)


def test_spanish_decimal_comma(facts: list[Fact]) -> None:
    f = _fact(facts, "xg_sum")
    es = _text(f, "caption", "es")
    assert "," in es
    assert numbers_preserved(es, "caption", "es", f)
    assert not numbers_preserved(es.replace(",", ".", 1), "caption", "es", f)


def test_tampered_audio_fails(facts: list[Fact]) -> None:
    f = _fact(facts, "goals")
    audio = _text(f, "audio_script", "en")
    assert "sixty-third" in audio and re.search(
        r"\bone\b", audio
    )  # minute 63, score 1-0 (wording varies)
    assert numbers_preserved(audio, "audio_script", "en", f)
    assert not numbers_preserved(
        audio.replace("sixty-third", "sixty-fourth"), "audio_script", "en", f
    )
    assert not numbers_preserved(re.sub(r"\bone\b", "two", audio, count=1), "audio_script", "en", f)


@pytest.mark.parametrize("n", list(range(0, 126)))
def test_cardinal_roundtrip(n: int) -> None:
    assert words_to_numbers(f"about {cardinal(n)} times") == [n]


@pytest.mark.parametrize("n", list(range(1, 100)))
def test_ordinal_roundtrip(n: int) -> None:
    assert words_to_numbers(f"in the {ordinal_words(n)} minute") == [n]


def test_speak_decimals_and_units() -> None:
    text = speak("Harlow generated 0.32 xG; shot at 104 km/h; 71% of attacks.")
    assert text == (
        "Harlow generated nought point three two expected goals; shot at one hundred and four "
        "kilometres per hour; seventy-one per cent of attacks."
    )
    assert words_to_numbers(text) == [0.32, 104, 71]


def test_match_phrases_are_not_numbers() -> None:
    assert words_to_numbers("in the first half and the second half, in their own third") == []


def test_goal_audio_reads_score_and_minute() -> None:
    assert (
        speak("GOAL, Northbridge 1–0 Harlow. C. Nkemelu, 63'.")
        == "GOAL, Northbridge one nil Harlow. C. Nkemelu, sixty-third minute."
    )
