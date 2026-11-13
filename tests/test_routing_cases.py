"""tests/routing_cases.json against the Python reference. tests/js/routing.test.mjs runs the
same file against docs/js/routing.js, so the two implementations cannot drift apart."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.router import AUTOPILOT_OK, resolve_state, routing_rules
from core.config import ROOT, load

CASES: list[dict[str, Any]] = json.loads(
    (ROOT / "tests" / "routing_cases.json").read_text(encoding="utf-8")
)
RULES = routing_rules(load().router)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_routing_case(case: dict[str, Any]) -> None:
    state, by = resolve_state(
        RULES,
        case["tier"],
        case["ttl_s"],
        [AUTOPILOT_OK] if case["autopilot_ok"] else [],
        case["elapsed_s"],
        case["decision"],
        case["decision_at_s"],
    )
    assert {"state": state, "by": by} == case["expect"]


def test_cases_use_the_configured_timeouts() -> None:
    # The table hard-codes ttl_s; it must agree with config so the cases describe the real bundle.
    for c in CASES:
        assert c["ttl_s"] == (RULES.tiers[c["tier"]].ttl_s or 0), c["id"]


def test_bundle_carries_the_rules() -> None:
    hero = json.loads((ROOT / "docs" / "data" / "hero.json").read_text(encoding="utf-8"))
    assert hero["meta"]["routing"] == RULES.model_dump(mode="json")
