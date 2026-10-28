"""Rule-based drafter. There is no LLM.

`draft_facts` turns moments into draft facts in loose natural wording, then adds
drafts with deliberately injected errors (tagged `injected: true`) so the
verifier has something to catch. The injected errors are test cases, not a
claim about how any model behaves.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from app.verifier import validate_fact
from core.config import load
from core.moments import Metric, compute_metric, minute, ordinal
from core.wilson import hedge_for, wilson

Fact = dict[str, Any]
Match = dict[str, Any]
INTENT_CLAUSE = {"team": " as they tried to stretch the play", "player": " as he tried to make an impact",
                 "match": " as both sides tried to force the issue"}
INTENT_VERBS = ("gone", "lost", "generated", "completed", "had", "scores")


def team_name(match: Match, team: str) -> str:
    return match["meta"]["teams"][team]["name"]


def player_name(match: Match, pid: str) -> str:
    for team in match["meta"]["teams"].values():
        if pid in team["players"]:
            return team["players"][pid]
    raise KeyError(pid)


def _num(key: str, value: float, unit: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key, "value": value}
    if unit:
        out["unit"] = unit
    return out


def share_hedge(k: int, n: int, baseline_pct: float | None) -> str:
    v = load()["verifier"]
    lo, hi = wilson(k, n, v["z"])
    return hedge_for(n, lo, hi, None if baseline_pct is None else baseline_pct / 100, v["n_min"])


# ---- text per moment rule ---------------------------------------------

def _text_goal(m: dict[str, Any], match: Match) -> tuple[str, list[dict[str, Any]]]:
    home, away = match["meta"]["home"], match["meta"]["away"]
    mn = minute(m["window"]["end_t"])
    s = m["score"]
    text = (f"GOAL, {team_name(match, home)} {s[home]}–{s[away]} {team_name(match, away)}. "
            f"{player_name(match, m['subject']['id'])}, {mn}'.")
    nums = [_num("minute", mn), _num(f"score_{home}", s[home]), _num(f"score_{away}", s[away])]
    return text, nums


def _text_overload(m: dict[str, Any], match: Match, force_plain: bool = False) -> tuple[str, list[dict[str, Any]]]:
    pct, since = round(m["value"]), minute(m["window"]["start_t"])
    team, lane = team_name(match, m["subject"]["id"]), m["lane"]
    hedge = "clear" if force_plain else share_hedge(m["k"], m["n"], 100 * load()["verifier"]["lane_baseline"])
    if hedge == "clear":
        text = f"Since the {ordinal(since)} minute, {pct}% of {team} attacks have gone down the {lane}."
    else:
        text = f"The numbers suggest {team} are favouring the {lane}: {pct}% of their attacks since the {ordinal(since)} minute."
    return text, [_num("value", pct, "%"), _num("since_minute", since)]


def _text_turnovers(m: dict[str, Any], match: Match) -> tuple[str, list[dict[str, Any]]]:
    c, since = int(m["value"]), minute(m["window"]["start_t"])
    text = f"{team_name(match, m['subject']['id'])} have lost the ball {c} times in their own third since the {ordinal(since)} minute."
    return text, [_num("value", c), _num("since_minute", since)]


def _text_half(m: dict[str, Any], match: Match) -> tuple[str, list[dict[str, Any]]]:
    rule, half = m["rule"], m["half"]
    if rule == "xg_sum":
        x = round(m["value"], 2)
        return f"{team_name(match, m['subject']['id'])} generated {x:.2f} xG in the {half} half.", [_num("value", x, "xG")]
    if rule == "pass_accuracy":
        pct = round(m["value"])
        return (f"{team_name(match, m['subject']['id'])} completed {pct}% of their passes in the {half} half.",
                [_num("value", pct, "%")])
    if rule == "top_speed":
        s = round(m["value"])
        return (f"Fastest shot of the {half} half: {s} km/h from {player_name(match, m['players'][0])}.",
                [_num("value", s, "km/h")])
    c = int(m["value"])
    return f"{player_name(match, m['subject']['id'])} had {c} shots in the {half} half.", [_num("value", c)]


def _text_event(m: dict[str, Any], match: Match) -> tuple[str, list[dict[str, Any]]]:
    mn = minute(m["window"]["end_t"])
    if m["rule"] == "substitution":
        on, off = (player_name(match, p) for p in m["players"])
        text = f"Substitution for {team_name(match, m['subject']['id'])} in the {ordinal(mn)} minute: {on} on, {off} off."
    else:
        text = (f"{m['card'].capitalize()} card for {player_name(match, m['subject']['id'])} of "
                f"{team_name(match, m['team'])} in the {ordinal(mn)} minute.")
    return text, [_num("minute", mn)]


def render(m: dict[str, Any], match: Match, force_plain: bool = False) -> tuple[str, list[dict[str, Any]]]:
    if m["rule"] == "goal":
        return _text_goal(m, match)
    if m["rule"] in ("substitution", "card"):
        return _text_event(m, match)
    if m["rule"] == "overload":
        return _text_overload(m, match, force_plain)
    if m["rule"] == "turnovers":
        return _text_turnovers(m, match)
    return _text_half(m, match)


# ---- fact construction ----------------------------------------------------

def _claim_value(m: dict[str, Any], nums: list[dict[str, Any]]) -> float:
    return next((n["value"] for n in nums if n["key"] == "value"), m["value"])


def to_fact(m: dict[str, Any], match: Match, fact_id: str, force_plain: bool = False) -> Fact:
    text, nums = render(m, match, force_plain)
    claim: dict[str, Any] = {"subject": m["subject"], "metric": m["metric"], "window": dict(m["window"]),
                             "comparator": "eq", "value": _claim_value(m, nums)}
    if m["metric"].startswith("attack_share_"):
        claim["baseline"] = round(100 * load()["verifier"]["lane_baseline"], 2)
    fact: Fact = {
        "fact_id": fact_id, "match_id": match["meta"]["match_id"], "kind": m["kind"], "created_t": m["created_t"],
        "injected": False, "claim": claim,
        "text": {"draft_en": text, "canonical_en": text, "numbers": nums},
        "evidence": {"event_ids": list(m["evidence_ids"]), "players": list(m.get("players", [])),
                     "window": dict(m["window"])},
        "verification": {"status": "pending", "checks": []},
        "approval": {"state": "not_routed"},
    }
    if "impact" in m:
        fact["impact"] = dict(m["impact"])
    validate_fact(fact)
    return fact


def _set_text(fact: Fact, text: str) -> None:
    fact["text"]["draft_en"] = text
    fact["text"]["canonical_en"] = text


def _injected(fact: Fact, fact_id: str) -> Fact:
    out = copy.deepcopy(fact)
    out["fact_id"], out["injected"] = fact_id, True
    return out


def _fmt(v: float, unit: str | None = None) -> str:
    return f"{v:.2f}" if unit == "xG" or v != int(v) else str(int(v))


def inject_wrong_number(fact: Fact, fact_id: str, bump: float) -> Fact:
    """Change the headline number (claim value, or the minute for goals) in the claim and the text."""
    out = _injected(fact, fact_id)
    num = next((n for n in out["text"]["numbers"] if n["key"] == "value"), None) or out["text"]["numbers"][0]
    old, new = num["value"], round(num["value"] + bump, 2)
    num["value"] = new
    if num["key"] == "value":
        out["claim"]["value"] = new
    unit = num.get("unit")
    pat = re.compile(rf"(?<![\d.]){re.escape(_fmt(old, unit))}(st|nd|rd|th)?(?![\d.])")
    repl = lambda mt: ordinal(int(new)) if mt.group(1) else _fmt(new, unit)  # noqa: E731
    _set_text(out, pat.sub(repl, out["text"]["draft_en"], count=1))
    validate_fact(out)
    return out


def inject_wrong_player(fact: Fact, fact_id: str, wrong_pid: str, match: Match) -> Fact:
    """Name a different player than the one in the cited events."""
    out = _injected(fact, fact_id)
    right = out["evidence"]["players"][0]
    _set_text(out, out["text"]["draft_en"].replace(player_name(match, right), player_name(match, wrong_pid)))
    if out["claim"]["subject"]["type"] == "player":
        out["claim"]["subject"]["id"] = wrong_pid
    out["evidence"]["players"] = [wrong_pid]
    validate_fact(out)
    return out


def inject_wrong_window(fact: Fact, fact_id: str, shift_s: float) -> Fact:
    """Claim the same thing about an earlier window."""
    out = _injected(fact, fact_id)
    for w in (out["claim"]["window"], out["evidence"]["window"]):
        w["start_t"] = max(0.0, w["start_t"] - shift_s)
        w["end_t"] = w["end_t"] - shift_s
    validate_fact(out)
    return out


def inject_future_event(fact: Fact, fact_id: str, match: Match, mode: str) -> Fact:
    """Cite an event after the claim was made, or date the claim before its evidence."""
    out = _injected(fact, fact_id)
    if mode == "cite_later":
        later = next(e for e in match["events"] if e["t"] > out["created_t"])
        out["evidence"]["event_ids"].append(later["id"])
    else:
        by_id = {e["id"]: e for e in match["events"]}
        out["created_t"] = max(by_id[i]["t"] for i in out["evidence"]["event_ids"]) - 30
    validate_fact(out)
    return out


def inject_intent(fact: Fact, fact_id: str, style: str) -> Fact:
    """Add a word that asserts intent, which cannot be observed in event data."""
    out = _injected(fact, fact_id)
    text = out["text"]["draft_en"]
    if style == "deliberately":
        verb = next(v for v in INTENT_VERBS if f" {v} " in text)
        text = text.replace(f" {verb} ", f" deliberately {verb} ", 1)
    else:
        text = text[:-1] + INTENT_CLAUSE[out["claim"]["subject"]["type"]] + "."
    _set_text(out, text)
    validate_fact(out)
    return out


def small_sample_draft(team: str, lane: str, start: float, end: float, match: Match, fact_id: str,
                       min_n: int = 1) -> Fact | None:
    """A lane-share claim over a window too short to support it, worded as a plain statement."""
    metric = f"attack_share_{lane}"
    m: Metric | None = compute_metric(metric, {"type": "team", "id": team}, {"start_t": start, "end_t": end},
                                      match["events"])
    if m is None or not min_n <= m.n < load()["verifier"]["n_min"] or m.k == 0:
        return None
    moment = {"kind": "tactical", "rule": "overload", "metric": metric, "subject": {"type": "team", "id": team},
              "window": {"start_t": start, "end_t": end}, "created_t": end, "value": m.value, "n": m.n, "k": m.k,
              "evidence_ids": m.ids, "lane": lane}
    out = to_fact(moment, match, fact_id, force_plain=True)
    out["injected"] = True
    return out


# ---- the drafter -----------------------------------------------------------

def _ids(start: int):
    i = start
    while True:
        yield f"f_{i:06d}"
        i += 1


def _first(facts: list[Fact], metric_prefix: str, **want: Any) -> Fact | None:
    for f in facts:
        if f["claim"]["metric"].startswith(metric_prefix) and all(
                f["claim"]["subject"].get(k) == v for k, v in want.items()):
            return f
    return None


def injected_drafts(good: list[Fact], match: Match, ids: Any) -> list[Fact]:
    """One injected draft per error category, built from suitable good drafts."""
    s = load()["sim"]
    out: list[Fact] = []
    goal, over = _first(good, "goals"), _first(good, "attack_share_")
    turn = _first(good, "turnovers_own_third")
    if over:
        out.append(inject_wrong_number(over, next(ids), 9))
        out.append(inject_future_event(over, next(ids), match, "cite_later"))
    if goal:
        scorer_team = goal["impact"]["subject_team"]
        mate = next(p for p in match["meta"]["teams"][scorer_team]["players"] if p not in goal["evidence"]["players"]
                    and not p.endswith("_01"))
        out.append(inject_wrong_player(goal, next(ids), mate, match))
    if turn:
        out.append(inject_wrong_window(turn, next(ids), 900))
        out.append(inject_intent(turn, next(ids), "clause"))
    small = first_small_sample("NOR", "left", s["hero_sub_t"], match, next(ids))
    if small:
        out.append(small)
    return out


def first_small_sample(team: str, lane: str, start: float, match: Match, fact_id: str,
                       min_n: int = 3) -> Fact | None:
    """Shortest window from `start` with at least `min_n` (but too few) attacks."""
    for end in range(int(start) + 60, int(start) + 1800, 30):
        fact = small_sample_draft(team, lane, start, float(end), match, fact_id, min_n)
        if fact:
            return fact
    return None


def draft_facts(moments: list[dict[str, Any]], match: Match) -> list[Fact]:
    """Good drafts for every moment, followed by the injected drafts."""
    ids = _ids(1)
    good = [to_fact(m, match, next(ids)) for m in moments]
    return good + injected_drafts(good, match, ids)
