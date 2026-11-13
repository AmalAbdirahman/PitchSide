"""Rule-based drafter. There is no LLM.

`draft_facts` turns moments into draft facts in loose natural wording, then adds
drafts with deliberately injected errors (tagged `injected: true`) so the
verifier has something to catch. The injected errors are test cases, not a
claim about how any model behaves.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from core.config import MetricContext, Settings, Verifier
from core.events import Match
from core.facts import (
    Claim,
    Evidence,
    Fact,
    FactText,
    Impact,
    Subject,
    TextNumber,
    Window,
    validate_fact,
)
from core.metrics import Metric, compute_metric
from core.moments import ordinal
from core.sources import League

Moment = dict[str, Any]
INTENT_CLAUSE = {
    "team": " as they tried to stretch the play",
    "player": " as he tried to make an impact",
    "match": " as both sides tried to force the issue",
}
INTENT_VERBS = ("gone", "lost", "generated", "completed", "had", "scores")


def team_name(match: Match, team: str) -> str:
    return match.meta.team_name(team)


def player_name(match: Match, pid: str) -> str:
    return match.meta.player_name(pid)


def _num(key: str, value: float, unit: str | None = None) -> TextNumber:
    return TextNumber(key=key, value=value, unit=unit)


# ---- text per moment rule ---------------------------------------------

Text = tuple[str, list[TextNumber]]


def _text_goal(m: Moment, match: Match) -> Text:
    home, away = match.meta.home, match.meta.away
    mn = match.meta.clock.minute(m["window"]["end_t"])
    s = m["score"]
    text = (
        f"GOAL, {team_name(match, home)} {s[home]}–{s[away]} {team_name(match, away)}. "
        f"{'Own goal, ' if m.get('own_goal') else ''}{player_name(match, m['subject']['id'])}, {mn}'."
    )
    nums = [_num("minute", mn), _num(f"score_{home}", s[home]), _num(f"score_{away}", s[away])]
    return text, nums


def _text_overload(m: Moment, match: Match, v: Verifier, force_plain: bool = False) -> Text:
    pct, since = round(m["value"]), match.meta.clock.minute(m["window"]["start_t"])
    team, lane = team_name(match, m["subject"]["id"]), m["lane"]
    hedge = "clear" if force_plain else str(m["hedge"])
    if hedge == "clear":
        text = f"Since the {ordinal(since)} minute, {pct}% of {team} attacks have gone down the {lane}."
    else:
        text = f"The numbers suggest {team} are favouring the {lane}: {pct}% of their attacks since the {ordinal(since)} minute."
    return text, [_num("value", pct, "%"), _num("since_minute", since)]


def _text_turnovers(m: Moment, match: Match) -> Text:
    c, since = int(m["value"]), match.meta.clock.minute(m["window"]["start_t"])
    text = f"{team_name(match, m['subject']['id'])} have lost the ball {c} times in their own third since the {ordinal(since)} minute."
    return text, [_num("value", c), _num("since_minute", since)]


def _text_half(m: Moment, match: Match) -> Text:
    rule, half = m["rule"], m["half"]
    if rule == "xg_sum":
        x = round(m["value"], 2)
        return f"{team_name(match, m['subject']['id'])} generated {x:.2f} xG in the {half} half.", [
            _num("value", x, "xG")
        ]
    if rule == "pass_accuracy":
        pct = round(m["value"])
        return (
            f"{team_name(match, m['subject']['id'])} completed {pct}% of their passes in the {half} half.",
            [_num("value", pct, "%")],
        )
    if rule == "top_speed":
        s = round(m["value"])
        return (
            f"Fastest shot of the {half} half: {s} km/h from {player_name(match, m['players'][0])}.",
            [_num("value", s, "km/h")],
        )
    if rule == "xt_leader":
        x = round(m["value"], 2)
        return (
            f"{player_name(match, m['subject']['id'])} added {x:.2f} xT with completed passes "
            f"and carries in the {half} half.",
            [_num("value", x, "xT")],
        )
    c = int(m["value"])
    return f"{player_name(match, m['subject']['id'])} had {c} shots in the {half} half.", [
        _num("value", c)
    ]


def _text_event(m: Moment, match: Match) -> Text:
    mn = match.meta.clock.minute(m["window"]["end_t"])
    if m["rule"] == "substitution":
        on, off = (player_name(match, p) for p in m["players"])
        text = f"Substitution for {team_name(match, m['subject']['id'])} in the {ordinal(mn)} minute: {on} on, {off} off."
    else:
        text = (
            f"{m['card'].capitalize()} card for {player_name(match, m['subject']['id'])} of "
            f"{team_name(match, m['team'])} in the {ordinal(mn)} minute."
        )
    return text, [_num("minute", mn)]


def render(m: Moment, match: Match, v: Verifier, force_plain: bool = False) -> Text:
    if m["rule"] == "goal":
        return _text_goal(m, match)
    if m["rule"] in ("substitution", "card"):
        return _text_event(m, match)
    if m["rule"] == "overload":
        return _text_overload(m, match, v, force_plain)
    if m["rule"] == "turnovers":
        return _text_turnovers(m, match)
    return _text_half(m, match)


# ---- fact construction ----------------------------------------------------


def claim_value(m: Moment, nums: list[TextNumber]) -> float:
    """The number the claim states: the rounded headline number if there is one."""
    return next((n.value for n in nums if n.key == "value"), m["value"])


def _baseline(m: Moment) -> float | None:
    """The league share of the lane, in per cent, that the moment detector compared against."""
    return float(m["baseline"]) if m["metric"].startswith("attack_share_") else None


def to_fact(m: Moment, match: Match, fact_id: str, v: Verifier, force_plain: bool = False) -> Fact:
    text, nums = render(m, match, v, force_plain)
    claim = Claim.model_validate(
        {
            "subject": m["subject"],
            "metric": m["metric"],
            "window": m["window"],
            "comparator": "eq",
            "value": claim_value(m, nums),
            "baseline": _baseline(m),
        }
    )
    fact = Fact.model_validate(
        {
            "fact_id": fact_id,
            "match_id": match.meta.match_id,
            "kind": m["kind"],
            "created_t": m["created_t"],
            "injected": False,
            "claim": claim,
            "text": FactText(draft_en=text, canonical_en=text, numbers=nums),
            "evidence": Evidence(
                event_ids=list(m["evidence_ids"]),
                players=list(m.get("players", [])),
                window=Window.model_validate(m["window"]),
            ),
            "verification": {"status": "pending", "checks": []},
            "approval": {"state": "not_routed"},
            "impact": Impact.model_validate(m["impact"]) if "impact" in m else None,
        }
    )
    validate_fact(fact)
    return fact


def _set_text(fact: Fact, text: str) -> None:
    fact.text.draft_en = text
    fact.text.canonical_en = text


def _injected(fact: Fact, fact_id: str) -> Fact:
    out = fact.model_copy(deep=True)
    out.fact_id, out.injected = fact_id, True
    return out


def _draft(fact: Fact) -> str:
    return fact.text.draft_en or fact.text.canonical_en


def _fmt(v: float, unit: str | None = None) -> str:
    return f"{v:.2f}" if unit == "xG" or v != int(v) else str(int(v))


def inject_wrong_number(fact: Fact, fact_id: str, bump: float) -> Fact:
    """Change the headline number (claim value, or the minute for goals) in the claim and the text."""
    out = _injected(fact, fact_id)
    nums = out.text.numbers
    num = next((n for n in nums if n.key == "value"), None) or nums[0]
    old, new = num.value, round(num.value + bump, 2)
    num.value = new
    if num.key == "value":
        out.claim.value = new
    pat = re.compile(rf"(?<![\d.]){re.escape(_fmt(old, num.unit))}(st|nd|rd|th)?(?![\d.])")

    def repl(mt: re.Match[str]) -> str:
        return ordinal(int(new)) if mt.group(1) else _fmt(new, num.unit)

    _set_text(out, pat.sub(repl, _draft(out), count=1))
    validate_fact(out)
    return out


def inject_wrong_player(fact: Fact, fact_id: str, wrong_pid: str, match: Match) -> Fact:
    """Name a different player than the one in the cited events."""
    out = _injected(fact, fact_id)
    right = (out.evidence.players or [])[0]
    _set_text(out, _draft(out).replace(player_name(match, right), player_name(match, wrong_pid)))
    if out.claim.subject.type == "player":
        out.claim.subject.id = wrong_pid
    out.evidence.players = [wrong_pid]
    validate_fact(out)
    return out


def inject_wrong_window(fact: Fact, fact_id: str, shift_s: float) -> Fact:
    """Claim the same thing about an earlier window."""
    out = _injected(fact, fact_id)
    for w in (out.claim.window, out.evidence.window):
        if w is not None:
            w.start_t = max(0.0, w.start_t - shift_s)
            w.end_t = w.end_t - shift_s
    validate_fact(out)
    return out


def inject_future_event(fact: Fact, fact_id: str, match: Match, mode: str) -> Fact:
    """Cite an event after the claim was made, or date the claim before its evidence."""
    out = _injected(fact, fact_id)
    if mode == "cite_later":
        later = next(e for e in match.events if e.t > out.created_t)
        out.evidence.event_ids.append(later.id)
    else:
        by_id = {e.id: e for e in match.events}
        out.created_t = max(by_id[i].t for i in out.evidence.event_ids) - 30
    validate_fact(out)
    return out


def inject_intent(fact: Fact, fact_id: str, style: str) -> Fact:
    """Add a word that asserts intent, which cannot be observed in event data."""
    out = _injected(fact, fact_id)
    text = _draft(out)
    if style == "deliberately":
        verb = next(v for v in INTENT_VERBS if f" {v} " in text)
        text = text.replace(f" {verb} ", f" deliberately {verb} ", 1)
    else:
        text = text[:-1] + INTENT_CLAUSE[out.claim.subject.type] + "."
    _set_text(out, text)
    validate_fact(out)
    return out


def small_sample_draft(
    team: str,
    lane: str,
    start: float,
    end: float,
    match: Match,
    fact_id: str,
    league: League,
    s: Settings,
    min_n: int = 1,
) -> Fact | None:
    """A lane-share claim over a window too short to support it, worded as a plain statement."""
    metric = f"attack_share_{lane}"
    subject, window = Subject(type="team", id=team), Window(start_t=start, end_t=end)
    m: Metric | None = compute_metric(metric, subject, window, match.events, MetricContext.of(s))
    if m is None or not min_n <= m.n < s.verifier.n_min or m.k == 0:
        return None
    moment = {
        "kind": "tactical",
        "rule": "overload",
        "metric": metric,
        "subject": {"type": "team", "id": team},
        "window": {"start_t": start, "end_t": end},
        "created_t": end,
        "value": m.value,
        "n": m.n,
        "k": m.k,
        "evidence_ids": m.ids,
        "baseline": round(100 * league.lane_shares[lane], 2),
        "hedge": "early",
        "lane": lane,
    }
    out = to_fact(moment, match, fact_id, s.verifier, force_plain=True)
    out.injected = True
    return out


# ---- the drafter -----------------------------------------------------------


def _ids(start: int) -> Iterator[str]:
    i = start
    while True:
        yield f"f_{i:06d}"
        i += 1


def _first(facts: list[Fact], metric_prefix: str) -> Fact | None:
    return next((f for f in facts if f.claim.metric.startswith(metric_prefix)), None)


def injected_drafts(
    good: list[Fact], match: Match, ids: Iterator[str], league: League, s: Settings
) -> list[Fact]:
    """One injected draft per error category, built from suitable good drafts."""
    out: list[Fact] = []
    goal, over = _first(good, "goals"), _first(good, "attack_share_")
    turn = _first(good, "turnovers_own_third")
    if over:
        out.append(inject_wrong_number(over, next(ids), 9))
        out.append(inject_future_event(over, next(ids), match, "cite_later"))
    if goal and goal.impact and goal.impact.subject_team:
        scored = goal.evidence.players or []
        mate = next(
            p
            for p in match.meta.teams[goal.impact.subject_team].players
            if p not in scored and not p.endswith("_01")
        )
        out.append(inject_wrong_player(goal, next(ids), mate, match))
    if turn:
        out.append(inject_wrong_window(turn, next(ids), 900))
        out.append(inject_intent(turn, next(ids), "clause"))
    start = _small_sample_start(match)
    small = first_small_sample(match.meta.home, "left", start, match, next(ids), league, s)
    if small:
        out.append(small)
    return out


def _small_sample_start(match: Match) -> float:
    """The home side's first substitution (a fresh window starts there), else the second half."""
    home = match.meta.home
    subs = [e.t for e in match.events if e.type == "substitution" and e.team == home]
    return subs[0] if subs else match.meta.clock.half_t


def first_small_sample(
    team: str,
    lane: str,
    start: float,
    match: Match,
    fact_id: str,
    league: League,
    s: Settings,
    min_n: int = 3,
) -> Fact | None:
    """Shortest window from `start` with at least `min_n` (but too few) attacks."""
    for end in range(int(start) + 60, int(start) + 1800, 30):
        fact = small_sample_draft(team, lane, start, float(end), match, fact_id, league, s, min_n)
        if fact:
            return fact
    return None


def draft_facts(moments: list[Moment], match: Match, league: League, s: Settings) -> list[Fact]:
    """Good drafts for every moment, followed by the injected drafts."""
    ids = _ids(1)
    good = [to_fact(m, match, next(ids), s.verifier) for m in moments]
    return good + injected_drafts(good, match, ids, league, s)
