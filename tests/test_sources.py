"""Event sources: each one yields our Match model and a league xG rate."""

from __future__ import annotations

import json

from core.config import ROOT
from core.events import Match
from core.sources import EventSource, SyntheticSource


def test_synthetic_source_is_the_committed_simulation(match: Match) -> None:
    source: EventSource = SyntheticSource()
    loaded, mu = source.load()
    assert loaded == match
    stats = json.loads((ROOT / "data" / "league_stats.json").read_text(encoding="utf-8"))
    assert mu == stats["mu_xg_per_team_minute"]
    assert "synthetic" in source.label
