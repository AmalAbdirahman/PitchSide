"""Renditions of an approved fact (SPEC section 11): cue, EN/ES captions, audio script.

Each rendition passes a deterministic number check: every number it contains is
in the fact's `text.numbers`, and every number in the canonical text survives.
Audio scripts spell numbers out, so they are parsed back from words to check.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from app.templates_es import caption_es
from app.verifier import validate_fact
from core.config import load

Fact = dict[str, Any]
Match = dict[str, Any]

ONES = ["nought", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
        "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}
ORD_IRREGULAR = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
                 "nine": "ninth", "twelve": "twelfth"}
# Ordinal words that name a part of the match or pitch, not a number.
NON_NUMERIC_PHRASES = ("first half", "second half", "own third", "middle third", "final third")


# ---- spelling -----------------------------------------------------------------

def cardinal(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")
    rest = n % 100
    return f"{ONES[n // 100]} hundred" + (f" and {cardinal(rest)}" if rest else "")


def ordinal_words(n: int) -> str:
    words = cardinal(n)
    head, sep, last = words.rpartition("-") if "-" in words else words.rpartition(" ")
    if last in ORD_IRREGULAR:
        last = ORD_IRREGULAR[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


def decimal_words(token: str) -> str:
    whole, frac = token.split(".")
    return f"{cardinal(int(whole))} point " + " ".join(ONES[int(d)] for d in frac)


def speak(text: str) -> str:
    """Speakable English with every number spelled out."""
    out = re.sub(r"\d+\.\d+", lambda m: decimal_words(m.group()), text)
    out = re.sub(r"(\d+)[-–](\d+)", lambda m: " ".join("nil" if g == "0" else cardinal(int(g)) for g in m.groups()), out)
    out = re.sub(r"(\d+)'", lambda m: f"{ordinal_words(int(m.group(1)))} minute", out)
    out = re.sub(r"(\d+)(st|nd|rd|th)\b", lambda m: ordinal_words(int(m.group(1))), out)
    out = re.sub(r"(\d+)%", lambda m: f"{cardinal(int(m.group(1)))} per cent", out)
    out = re.sub(r"\d+", lambda m: cardinal(int(m.group())), out)
    return out.replace("km/h", "kilometres per hour").replace("xG", "expected goals")


# ---- parsing numbers back ---------------------------------------------------------

UNIT_VAL = {w: i for i, w in enumerate(ONES)} | {"zero": 0}
TENS_VAL = {w: k * 10 for k, w in TENS.items()}
ORD_VAL = {ordinal_words(i): i for i in range(1, 100)}


def _can_extend(cur: int | None, val: int) -> bool:
    if cur is None:
        return False
    if cur >= 100 and cur % 100 == 0:
        return True
    return cur % 100 >= 20 and cur % 10 == 0 and val < 10


def words_to_numbers(text: str) -> list[float]:
    """Numbers spelled out in an English audio script, in order."""
    low = text.lower()
    for phrase in NON_NUMERIC_PHRASES:
        low = low.replace(phrase, " ")
    found: list[float] = []
    cur: int | None = None
    frac: str | None = None

    def flush() -> None:
        nonlocal cur, frac
        if cur is not None:
            found.append(cur + (float(f"0.{frac}") if frac else 0.0))
        cur, frac = None, None

    for tok in re.findall(r"[a-z]+", low):
        if frac is not None and tok in UNIT_VAL and UNIT_VAL[tok] < 10:
            frac += str(UNIT_VAL[tok])
        elif tok == "nil":
            flush()
            found.append(0.0)
        elif tok in UNIT_VAL or tok in TENS_VAL or tok in ORD_VAL:
            val = UNIT_VAL.get(tok, TENS_VAL.get(tok, ORD_VAL.get(tok, 0)))
            cur = cur + val if _can_extend(cur, val) else (flush() or val)
            if tok in ORD_VAL:
                flush()
        elif tok == "hundred" and cur is not None:
            cur *= 100
        elif tok == "and" and cur is not None and cur >= 100:
            continue
        elif tok == "point" and cur is not None and frac is None:
            frac = ""
        else:
            flush()
    flush()
    return found


def digits_in(text: str, lang: str) -> list[float]:
    """Numbers written with digits; Spanish uses a decimal comma."""
    pat = r"\d+(?:,\d+)?" if lang == "es" else r"\d+(?:\.\d+)?"
    return [float(t.replace(",", ".")) for t in re.findall(pat, text)]


def numbers_preserved(text: str, kind: str, lang: str, fact: Fact) -> bool:
    """No new numbers, and none of the canonical numbers lost."""
    found = words_to_numbers(text) if kind == "audio_script" else digits_in(text, lang)
    allowed = [n["value"] for n in fact["text"]["numbers"]]
    canonical = digits_in(fact["text"]["canonical_en"], "en")
    close = lambda a, b: abs(a - b) < 1e-9  # noqa: E731
    return (all(any(close(f, a) for a in allowed) for f in found)
            and all(any(close(c, f) for f in found) for c in canonical))


# ---- building renditions --------------------------------------------------------------

def air_time(fact: Fact) -> float | None:
    """Match time a cue airs with no producer action: T0 now, T1 after the veto window, T2 never."""
    tier = fact["risk"]["tier"]
    if tier == "T0":
        return fact["created_t"]
    if tier == "T1":
        return fact["created_t"] + load()["router"]["veto_window_s"]
    return None


def _cue(fact: Fact, lang: str, text: str) -> dict[str, Any]:
    r = load()["renditions"]
    ticker = fact["claim"]["metric"] in r["ticker_metrics"]
    cue: dict[str, Any] = {"kind": "cue", "lang": lang, "text": text, "layout": "ticker" if ticker else "lower_third",
                           "duration_s": r["ticker_duration_s"] if ticker else r["cue_duration_s"]}
    start = air_time(fact)
    if start is not None:
        cue["start_t"] = start
    return cue


def build_renditions(fact: Fact, match: Match) -> list[dict[str, Any]]:
    en, es = fact["text"]["canonical_en"], caption_es(fact, match)
    items = [_cue(fact, "en", en), _cue(fact, "es", es),
             {"kind": "caption", "lang": "en", "text": en}, {"kind": "caption", "lang": "es", "text": es},
             {"kind": "audio_script", "lang": "en", "text": speak(en)}]
    for r in items:
        r["numbers_preserved"] = numbers_preserved(r["text"], r["kind"], r["lang"], fact)
    return items


def with_renditions(fact: Fact, match: Match) -> Fact:
    """Copy of a routed fact with its renditions attached and validated."""
    out = copy.deepcopy(fact)
    out["renditions"] = build_renditions(out, match)
    validate_fact(out)
    return out


def all_preserved(fact: Fact) -> bool:
    return all(r["numbers_preserved"] for r in fact.get("renditions", []))
