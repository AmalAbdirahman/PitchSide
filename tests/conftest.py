from __future__ import annotations

import json
from typing import Any

import pytest

from core.config import ROOT


@pytest.fixture(scope="session")
def match() -> dict[str, Any]:
    return json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def seeded() -> list[dict[str, Any]]:
    return json.loads((ROOT / "tests" / "seeded_claims.json").read_text(encoding="utf-8"))


def seeded_of(category: str) -> list[dict[str, Any]]:
    data = json.loads((ROOT / "tests" / "seeded_claims.json").read_text(encoding="utf-8"))
    return [c for c in data if c["category"] == category]
