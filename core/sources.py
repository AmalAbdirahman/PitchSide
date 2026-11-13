"""Where a match comes from.

Every source hands the pipeline the same thing: a `Match` of our `Event` models in our convention
(metres on the configured pitch, each team's events attacking left to right with y = 0 on that
team's left touchline, match time in seconds running on across periods with a `Clock` for the
official minute) and the league goal-rate prior `mu` (mean xG per team per minute) that win
probability shrinks towards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.config import ROOT
from core.events import Match


class EventSource(Protocol):
    @property
    def label(self) -> str:
        """Human-readable name of the source, for logs and the bundle."""
        ...

    def load(self) -> tuple[Match, float]:
        """The match and the league xG rate per team per minute."""
        ...


@dataclass(frozen=True)
class SyntheticSource:
    """The seeded simulator's output: data/events.json and data/league_stats.json."""

    data_dir: Path = ROOT / "data"

    @property
    def label(self) -> str:
        return "synthetic (sim/generate.py)"

    def load(self) -> tuple[Match, float]:
        match = Match.model_validate_json(
            (self.data_dir / "events.json").read_text(encoding="utf-8")
        )
        stats = json.loads((self.data_dir / "league_stats.json").read_text(encoding="utf-8"))
        return match, float(stats["mu_xg_per_team_minute"])
