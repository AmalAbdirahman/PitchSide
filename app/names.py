"""Find roster players named in a sentence, by tokens rather than substrings.

A player is named when the text contains their surname as a whole word, with or
without the initial ("C. Nkemelu", "C.Nkemelu", "Nkemelu", "Nkemelu's"). A
double-barrelled surname is one word ("Moreau-Lake"). Two mistakes are reported:
a roster surname written with the wrong initial, and a capitalised word one or two
letters away from a roster surname (a misspelt name), which must never air.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.events import Meta

# Letters, with hyphens inside a word. Apostrophes split, so "Varga's" gives "Varga".
WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
INITIAL_BEFORE = re.compile(r"(?<![^\W\d_])([^\W\d_])\.\s*$")


@dataclass(frozen=True)
class RosterName:
    pid: str
    full: str
    initial: str | None
    surname: str


def roster(meta: Meta) -> list[RosterName]:
    """Every player, in roster order, split into initial and surname."""
    out = []
    for pid, full in meta.names().items():
        initial, sep, surname = full.partition(". ")
        if sep:
            out.append(RosterName(pid, full, initial, surname))
        else:
            out.append(RosterName(pid, full, None, full))
    return out


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insert, delete, substitute)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _words(text: str) -> list[tuple[str, str | None]]:
    """Each word with the single-letter initial written just before it, if any."""
    out = []
    for m in WORD.finditer(text):
        before = INITIAL_BEFORE.search(text[: m.start()])
        out.append((m.group(), before.group(1) if before else None))
    return out


def named_players(text: str, players: list[RosterName]) -> set[str]:
    """Ids of roster players whose surname appears as a word."""
    by_surname = {p.surname: p.pid for p in players}
    return {by_surname[w] for w, _ in _words(text) if w in by_surname}


def name_problems(
    text: str, players: list[RosterName], team_names: set[str], max_edits: int, min_length: int
) -> list[str]:
    """Wrong initials and misspelt roster names in the text."""
    by_surname = {p.surname: p for p in players}
    problems = []
    for word, initial in _words(text):
        player = by_surname.get(word)
        if player is not None:
            if initial and player.initial and initial != player.initial:
                problems.append(
                    f"text writes {initial}. {word}, but the roster name is "
                    f"{player.full} ({player.pid})"
                )
            continue
        if not word[0].isupper() or word in team_names:
            continue
        close = [
            p
            for p in players
            if len(p.surname) >= min_length
            and edit_distance(word.lower(), p.surname.lower()) <= max_edits
        ]
        if close:
            problems.append(
                f"text names {word}, which is not on either roster "
                f"(closest: {close[0].full} ({close[0].pid}))"
            )
    return problems
