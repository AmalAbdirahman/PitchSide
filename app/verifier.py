"""Deterministic verifier (SPEC sections 7 and 8). Code decides truth.

`verify(fact, match)` returns a new fact with a verification record:
verified (all checks passed untouched), corrected (passed after deterministic
fixes), rejected (a check failed) or unverifiable (the metric cannot be recomputed).
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from core.config import ROOT, load
from core.moments import Metric, compute_metric, minute, ordinal
from core.wilson import hedge_for, wilson

Fact = dict[str, Any]
Match = dict[str, Any]
Check = dict[str, Any]

_VALIDATOR = Draft202012Validator(json.loads((ROOT / "fact.schema.json").read_text(encoding="utf-8")))
COUNT_METRICS = {"goals", "shots", "turnovers_own_third", "substitutions", "cards"}
SHARE_METRICS = {"attack_share_left", "attack_share_centre", "attack_share_right", "pass_accuracy"}
HEDGE_LEVEL = {"early": 0, "suggests": 1, "clear": 2, "none": 2}
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def validate_fact(fact: Fact) -> None:
    """Raise ValueError if the fact does not match fact.schema.json."""
    errors = sorted(_VALIDATOR.iter_errors(fact), key=lambda e: list(e.path))
    if errors:
        raise ValueError("; ".join(f"{list(e.path)}: {e.message}" for e in errors))


def _check(name: str, passed: bool, detail: str) -> Check:
    return {"name": name, "passed": passed, "detail": detail}


def tolerance(metric: str) -> float:
    v = load()["verifier"]
    if metric in COUNT_METRICS:
        return 0.0
    if metric in SHARE_METRICS:
        return v["tol_share_pp"]
    return v["tol_xg"] if metric == "xg_sum" else v["tol_speed"]


# ---- individual checks -------------------------------------------------------

def strip_intent(text: str) -> tuple[str, list[dict[str, str]]]:
    """Remove words asserting intent. Returns new text and corrections."""
    new = text
    for pat in load()["verifier"]["intent_patterns"]:
        new = re.sub(pat, "", new, flags=re.IGNORECASE)
    new = re.sub(r"\s+([.,:;])", r"\1", re.sub(r"\s{2,}", " ", new)).strip()
    if new == text:
        return text, []
    return new, [{"field": "text.canonical_en", "from": text, "to": new,
                  "reason": "intent: words asserting intent removed; only the observable part is kept"}]


def check_evidence(fact: Fact, by_id: dict[int, dict[str, Any]]) -> Check:
    w, created, problems = fact["claim"]["window"], fact["created_t"], []
    for i in fact["evidence"]["event_ids"]:
        e = by_id.get(i)
        if e is None:
            problems.append(f"event {i} does not exist")
        elif e["t"] > created:
            problems.append(f"event {i} at t={e['t']} is after the claim was made (t={created})")
        elif not w["start_t"] <= e["t"] <= w["end_t"]:
            problems.append(f"event {i} at t={e['t']} is outside the claim window")
    if w["end_t"] > created:
        problems.append(f"claim window ends at t={w['end_t']}, after the claim was made (t={created})")
    return _check("evidence_exists", not problems, "; ".join(problems) or "all cited events exist, in window, in the past")


def _names(match: Match) -> dict[str, str]:
    return {name: pid for team in match["meta"]["teams"].values() for pid, name in team["players"].items()}


def check_entities(fact: Fact, by_id: dict[int, dict[str, Any]], match: Match) -> Check:
    cited = [by_id[i] for i in fact["evidence"]["event_ids"] if i in by_id]
    teams = {e["team"] for e in cited}
    players = {e["player_id"] for e in cited} | {e["player_off"] for e in cited if "player_off" in e}
    subj, problems = fact["claim"]["subject"], []
    if subj["type"] == "team" and subj["id"] not in teams:
        problems.append(f"team {subj['id']} does not appear in the cited events")
    if subj["type"] == "player" and subj["id"] not in players:
        problems.append(f"player {subj['id']} does not appear in the cited events")
    for pid in fact["evidence"].get("players", []):
        if pid not in players:
            problems.append(f"named player {pid} does not appear in the cited events")
    for name, pid in _names(match).items():
        if name in fact["text"]["canonical_en"] and pid not in players:
            problems.append(f"text names {name} ({pid}), who is not in the cited events")
    return _check("entities", not problems, "; ".join(problems) or "teams and players match the cited events")


def check_recompute(claim: dict[str, Any], m: Metric) -> Check:
    obs, val, cmp = m.value, claim["value"], claim["comparator"]
    if cmp == "eq":
        ok = abs(obs - val) <= tolerance(claim["metric"]) + 1e-9
    elif cmp == "lt":
        ok = obs < val
    else:
        ok = obs > val
    return _check("recompute", ok, f"claimed {cmp} {val}, recomputed {round(obs, 3)} from {m.n} events")


def _number_ok(num: dict[str, Any], fact: Fact, m: Metric, by_id: dict[int, dict[str, Any]], match: Match) -> bool:
    key, val, claim = num["key"], num["value"], fact["claim"]
    cited = [by_id[i] for i in fact["evidence"]["event_ids"] if i in by_id]
    if key == "value":
        return claim["comparator"] != "eq" or abs(val - m.value) <= tolerance(claim["metric"]) + 1e-9
    if key in ("n", "k"):
        return val == getattr(m, key)
    if key == "minute":
        return any(minute(e["t"]) == val for e in cited)
    if key == "since_minute":
        return minute(claim["window"]["start_t"]) == val
    if key.startswith("score_") and cited:
        team, t = key.removeprefix("score_"), max(e["t"] for e in cited)
        return val == sum(e["type"] == "goal" and e["team"] == team and e["t"] <= t for e in match["events"])
    return False


def check_numbers(fact: Fact, m: Metric, by_id: dict[int, dict[str, Any]], match: Match) -> Check:
    listed, problems = fact["text"]["numbers"], []
    for token in NUMBER.findall(fact["text"]["canonical_en"]):
        if not any(abs(float(token) - n["value"]) < 1e-9 for n in listed):
            problems.append(f"{token} in the text is not in text.numbers")
    for num in listed:
        if not _number_ok(num, fact, m, by_id, match):
            problems.append(f"{num['key']}={num['value']} does not match the event log")
    return _check("numbers_match", not problems, "; ".join(problems) or "every number traced to the event log")


# ---- sample size and hedge ----------------------------------------------------

def _strength(text: str) -> int:
    v, low = load()["verifier"], text.lower()
    if any(w in low for w in v["early_markers"]):
        return 0
    return 1 if any(w in low for w in v["suggest_markers"]) else 2


def _count_text(fact: Fact, m: Metric, match: Match) -> tuple[str, list[dict[str, Any]]]:
    claim = fact["claim"]
    team = match["meta"]["teams"][claim["subject"]["id"]]["name"]
    if claim["metric"] == "pass_accuracy":
        return f"Early sign: {team} completed {m.k} of {m.n} passes in this spell.", [
            {"key": "k", "value": m.k}, {"key": "n", "value": m.n}]
    since = minute(claim["window"]["start_t"])
    lane = claim["metric"].removeprefix("attack_share_")
    return (f"Early sign: {m.k} of {m.n} {team} attacks have gone down the {lane} since the {ordinal(since)} minute.",
            [{"key": "k", "value": m.k}, {"key": "n", "value": m.n}, {"key": "since_minute", "value": since}])


def _soften(text: str, hedge: str, match: Match) -> str:
    if hedge == "early":
        return f"Early sign: {text}"
    starts_with_name = any(text.startswith(t["name"]) for t in match["meta"]["teams"].values())
    return f"The numbers suggest that {text if starts_with_name else text[0].lower() + text[1:]}"


def calibrate(fact: Fact, m: Metric, match: Match) -> tuple[Check, list[dict[str, str]]]:
    """Set uncertainty and hedge; rewrite text that sounds stronger than the evidence."""
    v, claim, text = load()["verifier"], fact["claim"], fact["text"]["canonical_en"]
    if claim["metric"] not in SHARE_METRICS:
        fact["uncertainty"] = {"n": m.n, "ci_method": "none", "hedge": "none"}
        return _check("sample_size", True, "plain value from the event log; no inference"), []
    lo, hi = wilson(m.k, m.n, v["z"])
    base = claim.get("baseline")
    hedge = hedge_for(m.n, lo, hi, None if base is None else base / 100, v["n_min"])
    fact["uncertainty"] = {"n": m.n, "ci_method": "wilson", "ci_low": round(lo, 4), "ci_high": round(hi, 4),
                           "hedge": hedge}
    if m.n < v["n_min"]:
        new, nums = _count_text(fact, m, match)
        reason = f"sample_size: n={m.n} is below {v['n_min']}; restated as a plain count with hedge early"
    elif _strength(text) > HEDGE_LEVEL[hedge]:
        new, nums = _soften(text, hedge, match), fact["text"]["numbers"]
        reason = f"hedge: wording was stronger than '{hedge}' allows"
    else:
        return _check("sample_size", True, f"n={m.n}, Wilson [{lo:.2f}, {hi:.2f}], hedge {hedge}"), []
    fact["text"]["canonical_en"], fact["text"]["numbers"] = new, nums
    return (_check("sample_size", True, reason),
            [{"field": "text.canonical_en", "from": text, "to": new, "reason": reason}])


# ---- entry point ------------------------------------------------------------------

def _status(checks: list[Check], corrections: list[dict[str, str]], computable: bool) -> str:
    failed = [c["name"] for c in checks if not c["passed"]]
    if not computable:
        return "rejected" if any(n != "recompute" for n in failed) else "unverifiable"
    if failed:
        return "rejected"
    return "corrected" if corrections else "verified"


def verify(fact: Fact, match: Match) -> Fact:
    """Run every check on a copy of the fact and return it with a verification record."""
    out = copy.deepcopy(fact)
    by_id = {e["id"]: e for e in match["events"]}
    known = [e for e in match["events"] if e["t"] <= out["created_t"]]
    out["text"].setdefault("draft_en", out["text"]["canonical_en"])
    out["text"]["canonical_en"], corrections = strip_intent(out["text"]["canonical_en"])
    checks = [_check("intent_stripped", True, "intent words removed" if corrections else "no intent words"),
              check_evidence(out, by_id), check_entities(out, by_id, match)]
    claim = out["claim"]
    m = compute_metric(claim["metric"], claim["subject"], claim["window"], known)
    if m is not None and claim["metric"] in SHARE_METRICS and m.n == 0:
        m = None  # a share of nothing cannot be stated
    if m is None:
        checks.append(_check("recompute", False, f"{claim['metric']} cannot be recomputed from the event log here"))
    else:
        checks.append(check_recompute(claim, m))
        sample, fixes = calibrate(out, m, match)
        checks += [sample, check_numbers(out, m, by_id, match)]
        corrections += fixes
    status = _status(checks, corrections, m is not None)
    out["verification"] = {"status": status, "checks": checks}
    if corrections:
        out["verification"]["corrections"] = corrections
    if status in ("rejected", "unverifiable"):
        out["verification"]["reject_reason"] = "; ".join(c["detail"] for c in checks if not c["passed"])
    validate_fact(out)
    return out
