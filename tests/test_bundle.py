"""Bundle contents (SPEC section 12) and the Task 2 done criteria."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.build_bundle import CUE_SCHEMA, build, write
from app.renditions import all_preserved
from app.verifier import validate_fact


@pytest.fixture(scope="module")
def bundle() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return build()


def test_done_criteria(bundle: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
    hero, _ = bundle
    facts, log = hero["facts"], hero["log"]
    assert len(facts) >= 8
    assert sum(f["verification"]["status"] == "corrected" for f in facts) >= 1
    assert sum(f["verification"]["status"] == "rejected" for f in log) >= 2
    assert sum(f["risk"]["tier"] == "T1" for f in facts) >= 1


def test_every_fact_validates_and_only_routable_are_routed(bundle: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
    hero, _ = bundle
    for f in hero["facts"]:
        validate_fact(f)
        assert f["verification"]["status"] in ("verified", "corrected")
        assert f["risk"]["tier"] in ("T0", "T1", "T2")
    for f in hero["log"]:
        validate_fact(f)
        assert f["verification"]["status"] in ("rejected", "unverifiable")
        assert "risk" not in f and "renditions" not in f


def test_cues_validate_and_match_facts(bundle: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
    hero, cues = bundle
    Draft202012Validator(CUE_SCHEMA).validate(cues)
    by_id = {f["fact_id"]: f for f in hero["facts"]}
    assert cues == sorted(cues, key=lambda c: (c["start_t"], c["fact_id"], c["lang"]))
    assert len({c["cue_id"] for c in cues}) == len(cues)
    for c in cues:
        f = by_id[c["fact_id"]]
        assert f["risk"]["tier"] in ("T0", "T1") and all_preserved(f)
        assert c["start_t"] >= f["created_t"]
    assert not any(by_id[c["fact_id"]]["risk"]["tier"] == "T2" for c in cues)


def test_bundle_shape(bundle: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
    hero, _ = bundle
    assert set(hero) == {"meta", "events", "score_timeline", "facts", "log", "metrics", "series"}
    assert hero["meta"]["win_prob_label"] == "model estimate"
    assert hero["score_timeline"][-1] == {"t": hero["score_timeline"][-1]["t"], "NOR": 1, "HAR": 0}
    assert {"id", "t", "type", "team", "player"} <= set(hero["events"][0])


def test_write_produces_json_files(tmp_path: Path) -> None:
    hero, cues = write(tmp_path)
    assert json.loads((tmp_path / "hero.json").read_text(encoding="utf-8"))["metrics"] == hero["metrics"]
    assert len(json.loads((tmp_path / "cues.json").read_text(encoding="utf-8"))) == len(cues)
