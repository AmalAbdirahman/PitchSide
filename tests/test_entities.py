"""Player names in the text are matched against the roster as tokens, including near misses.

Each case cites one event by one player, then checks what the text may name.
C. Nkemelu (NOR_09) and F. Moreau-Lake (NOR_10) are Northbridge players.
"""

from __future__ import annotations

import pytest

from app.checks import check_entities
from core.config import load
from core.events import Match
from core.facts import Fact, Subject
from tests.conftest import seeded_of

S = load()


def _citing(pid: str, text: str, match: Match) -> Fact:
    """A team fact whose only evidence is one event by `pid`, with the given wording."""
    event = next(e for e in match.events if e.player_id == pid)
    base = next(c.fact for c in seeded_of("good") if c.fact.claim.subject.type == "team")
    base.claim.subject = Subject(type="team", id=event.team)
    base.evidence.event_ids = [event.id]
    base.evidence.players = []
    base.text.canonical_en = text
    return base


def _detail(pid: str, text: str, match: Match) -> tuple[bool, str]:
    by_id = {e.id: e for e in match.events}
    check = check_entities(_citing(pid, text, match), by_id, match, S.verifier)
    return check.passed, check.detail or ""


OTHER = "NOR_02"  # L. Corrigan: cited instead of Nkemelu in the "not cited" cases


@pytest.mark.parametrize(
    "text",
    [
        "C. Nkemelu went close.",  # full name (caught before tokenising too)
        "Nkemelu went close.",  # surname only
        "C.Nkemelu went close.",  # no space after the initial
        "Nkemelu's shot went close.",  # possessive
        "Northbridge, through Nkemelu, went close.",
    ],
)
def test_naming_an_uncited_player_is_rejected(text: str, match: Match) -> None:
    passed, detail = _detail(OTHER, text, match)
    assert not passed
    assert "C. Nkemelu (NOR_09)" in detail


@pytest.mark.parametrize(
    "text",
    [
        "C. Nkemelu went close.",
        "Nkemelu went close.",
        "Nkemelu's shot went close.",
        "C.Nkemelu went close.",
    ],
)
def test_naming_the_cited_player_passes(text: str, match: Match) -> None:
    assert _detail("NOR_09", text, match)[0]


@pytest.mark.parametrize("near", ["Nkemelo", "Nkemelus", "Nkemel", "C. Nkemelo"])
def test_near_miss_of_a_roster_name_is_rejected(near: str, match: Match) -> None:
    # Even when the real player is cited: a misspelt name must not air.
    passed, detail = _detail("NOR_09", f"{near} went close.", match)
    assert not passed
    assert "not on either roster" in detail and "C. Nkemelu" in detail


def test_wrong_initial_is_rejected(match: Match) -> None:
    passed, detail = _detail("NOR_09", "D. Nkemelu went close.", match)
    assert not passed
    assert "D. Nkemelu" in detail and "C. Nkemelu" in detail


def test_hyphenated_surname_is_one_name(match: Match) -> None:
    assert not _detail(OTHER, "Moreau-Lake went close.", match)[0]
    assert _detail("NOR_10", "F. Moreau-Lake went close.", match)[0]
    # Half of a double-barrelled name is not a roster name, and is too far to be a near miss.
    assert _detail(OTHER, "Moreau went close.", match)[0]


@pytest.mark.parametrize(
    "text",
    [
        "GOAL, Northbridge 1–0 Harlow. L. Corrigan, 63'.",
        "Early sign: 4 of 5 Northbridge attacks have gone down the left since the 58th minute.",
        "The numbers suggest Harlow are favouring the centre.",
        "Fastest shot of the first half: 109 km/h from L. Corrigan.",
        "Substitution for Northbridge in the 58th minute.",
    ],
)
def test_ordinary_capitalised_words_are_not_names(text: str, match: Match) -> None:
    assert _detail(OTHER, text, match)[0]
