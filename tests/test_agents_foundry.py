"""Scout loop with a fake Foundry client: retry, fallback, cache replay and budget. No network."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import pytest

from app.agents_foundry import Scout, run_scout, scout_metrics, scout_request
from app.build_bundle import build
from app.drafter import draft_facts, render
from core.config import ROOT
from core.moments import detect_moments

Moment = dict[str, Any]


@pytest.fixture(scope="module")
def moments(match: dict[str, Any]) -> list[Moment]:
    mu = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))["mu_xg_per_team_minute"]
    return detect_moments(match["events"], ("NOR", "HAR"), mu)


def _key(subject: dict[str, str], metric: str, window: dict[str, float]) -> str:
    return json.dumps([subject, metric, window], sort_keys=True)


def good_output(moments: list[Moment], match: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Fake Scout answer that restates the moment the way the rule-based drafter would."""
    by_key = {_key(m["subject"], m["metric"], m["window"]): m for m in moments}

    def answer(req: dict[str, Any]) -> dict[str, Any]:
        mo = req["moment"]
        text, nums = render(by_key[_key(mo["subject"], mo["metric"], mo["window"])], match)
        claim = {"subject": mo["subject"], "metric": mo["metric"], "window": mo["window"], "comparator": "eq",
                 "value": req["claim_value"]}
        return {"claim": claim, "text": {"canonical_en": text, "numbers": nums}, "evidence_ids": req["evidence_ids"]}
    return answer


def wrong_number(out: dict[str, Any], bump: int = 5) -> dict[str, Any]:
    out = json.loads(json.dumps(out))
    num = next(n for n in out["text"]["numbers"] if n["key"] == "value")
    old, num["value"] = num["value"], num["value"] + bump
    out["claim"]["value"] = num["value"]
    out["text"]["canonical_en"] = re.sub(rf"\b{int(old)}\b", str(int(num["value"])), out["text"]["canonical_en"], count=1)
    return out


def fake_caller(answer: Callable[[dict[str, Any]], Any], log: list[dict[str, Any]] | None = None):
    def call(user: str) -> tuple[str, str, str]:
        req = json.loads(user)
        if log is not None:
            log.append(req)
        out = answer(req)
        return (out if isinstance(out, str) else json.dumps(out)), "foundry_agent", "fake-model"
    return call


def turnovers(moments: list[Moment]) -> list[Moment]:
    return [m for m in moments if m["metric"] == "turnovers_own_third"][:1]


def rules_for(ms: list[Moment], match: dict[str, Any]) -> list[dict[str, Any]]:
    return draft_facts(ms, match)[:len(ms)]


def test_good_scout_claim_verifies_first_time(moments: list[Moment], match: dict[str, Any]) -> None:
    ms = turnovers(moments)
    scout = Scout(fake_caller(good_output(moments, match)), {}, 40)
    [fact] = run_scout(scout, ms, rules_for(ms, match), match)
    assert fact["verification"]["status"] == "verified"
    assert [(s["agent"], s["attempt"]) for s in fact["audit"]["steps"]] == [("Scout", 1), ("Verifier", 1)]
    assert fact["audit"]["steps"][0]["path"] == "foundry_agent" and fact["audit"]["steps"][0]["model"] == "fake-model"
    assert scout_metrics([fact])["first_pass"] == 1


def test_rejected_claim_is_retried_once_with_feedback(moments: list[Moment], match: dict[str, Any]) -> None:
    ms, good, sent = turnovers(moments), good_output(moments, match), []
    scout = Scout(fake_caller(lambda r: good(r) if "feedback" in r else wrong_number(good(r)), sent), {}, 40)
    [fact] = run_scout(scout, ms, rules_for(ms, match), match)
    assert fact["verification"]["status"] == "verified"
    assert "recompute" in sent[1]["feedback"] and sent[1]["previous_output"]
    assert [s["outcome"] for s in fact["audit"]["steps"]] == ["drafted", "rejected", "drafted", "verified"]
    assert scout_metrics([fact])["after_retry"] == 1


def test_claim_rejected_twice_stays_rejected(moments: list[Moment], match: dict[str, Any]) -> None:
    ms, good = turnovers(moments), good_output(moments, match)
    scout = Scout(fake_caller(lambda r: wrong_number(good(r))), {}, 40)
    [fact] = run_scout(scout, ms, rules_for(ms, match), match)
    assert fact["verification"]["status"] == "rejected" and scout.calls == 2
    assert scout_metrics([fact])["rejected"] == 1


@pytest.mark.parametrize("answer", [lambda r: (_ for _ in ()).throw(TimeoutError("slow")), lambda r: "not json"])
def test_failure_falls_back_to_rules(moments: list[Moment], match: dict[str, Any], answer: Any) -> None:
    ms = turnovers(moments)
    [fact] = run_scout(Scout(fake_caller(answer), {}, 40), ms, rules_for(ms, match), match)
    agents = [(s["agent"], s.get("path")) for s in fact["audit"]["steps"]]
    assert ("Drafter", "rules") in agents and fact["verification"]["status"] == "verified"
    assert scout_metrics([fact])["rules"] == 1


def test_budget_caps_calls(moments: list[Moment], match: dict[str, Any]) -> None:
    ms = moments[:5]
    scout = Scout(fake_caller(good_output(moments, match)), {}, 3)
    facts = run_scout(scout, ms, rules_for(ms, match), match)
    assert scout.calls == 3
    assert [f["audit"]["steps"][0]["outcome"] for f in facts][3:] == ["no_budget", "no_budget"]


def test_cache_replays_the_same_bundle_with_no_client(moments: list[Moment], match: dict[str, Any]) -> None:
    good = good_output(moments, match)
    live = Scout(fake_caller(lambda r: good(r) if "feedback" in r or r["moment"]["rule"] != "turnovers"
                             else wrong_number(good(r))), {}, 40)
    hero_live, cues_live = build(live)
    replay = Scout(None, json.loads(json.dumps(live.cache)), 40)
    hero_replay, cues_replay = build(replay)
    assert hero_replay == hero_live and cues_replay == cues_live
    assert replay.calls == live.calls <= 40


def test_request_holds_only_input_facts(moments: list[Moment], match: dict[str, Any]) -> None:
    req = scout_request(turnovers(moments)[0], match)
    assert set(req) == {"moment", "minutes", "teams", "players", "numbers", "claim_value", "evidence_ids", "hedge"}
    assert "impact" not in req["moment"]


def test_live_rerun_reuses_good_answers_and_repeats_failures(moments: list[Moment], match: dict[str, Any]) -> None:
    ms, good = moments[:3], good_output(moments, match)
    flaky = Scout(fake_caller(lambda r: good(r) if r["moment"]["rule"] != "turnovers" else
                              (_ for _ in ()).throw(ConnectionError("down"))), {}, 40)
    run_scout(flaky, ms, rules_for(ms, match), match)
    sent: list[dict[str, Any]] = []
    rerun = Scout(fake_caller(good, sent), {}, 40, prior=flaky.cache)
    facts = run_scout(rerun, ms, rules_for(ms, match), match)
    assert [r["moment"]["rule"] for r in sent] == ["turnovers"]
    assert scout_metrics(facts)["first_pass"] == 3


def test_t0_facts_use_template_wording_but_keep_scout_trace(moments: list[Moment], match: dict[str, Any]) -> None:
    ms = [m for m in moments if m["metric"] in ("goals", "substitutions", "cards")]
    good = good_output(moments, match)

    def chatty(req: dict[str, Any]) -> dict[str, Any]:
        out = good(req)
        out["text"]["canonical_en"] = "Agent wording: " + out["text"]["canonical_en"]
        return out
    facts = run_scout(Scout(fake_caller(chatty), {}, 40), ms, rules_for(ms, match), match)
    for f, m in zip(facts, ms):
        assert f["text"]["canonical_en"] == render(m, match)[0]
        assert f["text"]["draft_en"].startswith("Agent wording: ")
        assert [s["agent"] for s in f["audit"]["steps"]] == ["Scout", "Drafter", "Verifier"]
        assert f["verification"]["status"] == "verified"
    assert scout_metrics(facts)["first_pass"] == len(ms) >= 4
    goal = next(f for f, m in zip(facts, ms) if m["metric"] == "goals")
    assert goal["text"]["canonical_en"] == "GOAL, Northbridge 1–0 Harlow. C. Nkemelu, 63'."
