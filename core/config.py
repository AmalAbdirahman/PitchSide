"""Typed, validated settings loaded from config.yaml.

Every threshold and constant lives in config.yaml (SPEC.md). `load()` reads it once at an
entry point and the resulting frozen `Settings` is passed down as an argument; no module reads
the file on import or looks the values up through a global.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Pitch(_Section):
    length: int
    width: int
    own_third_x: int
    final_third_x: int


class Sim(_Section):
    hero_match_id: str
    league_matches: int
    half_s: int
    mean_gap_s: int
    lane_weights: tuple[float, float, float]
    overload_lane_weights: tuple[float, float, float]
    pass_logit_intercept: float
    pass_logit_dist: float
    pass_logit_pressure: float
    pass_logit_final_third: float
    press_logit_boost: float
    pressure_prob: float
    press_pressure_prob: float
    tackle_prob: float
    card_prob: float
    shot_prob_box: float
    shot_prob_long: float
    hero_sub_t: int
    hero_goal_t: int
    hero_overload_until_t: int
    hero_press_from_t: int
    hero_away_sub_t: int


class Xg(_Section):
    intercept: float
    dist: float
    angle: float
    header: float
    other_body: float
    free_kick: float
    under_pressure: float
    penalty: float
    goal_width: float


class Winprob(_Section):
    k: int
    goal_cap: int
    match_minutes: int


class WinprobEval(_Section):
    """The P10 win-probability validation (scripts/eval_winprob.py)."""

    every_min: int
    phases: tuple[int, ...]
    sim_seed: int
    sim_train_matches: int
    sim_test_matches: int
    k_grid: tuple[float, ...]
    rho_grid: tuple[float, ...]
    bootstrap: int
    bootstrap_seed: int


class Moments(_Section):
    check_every_s: int
    first_check_s: int
    overload_window_s: int
    overload_min_share: float
    turnover_window_s: int
    turnover_min_count: int
    xt_leader_min: float
    hub_min_passes: int


class Verifier(_Section):
    n_min: int
    z: float
    tol_share_pp: float
    tol_xg: float
    tol_speed: float
    tol_xt: float
    name_max_edits: int
    name_min_length: int
    early_markers: tuple[str, ...]
    suggest_markers: tuple[str, ...]
    intent_patterns: tuple[str, ...]


class Hedge(_Section):
    """Beta-Binomial hedge for share claims (core/hedge.py)."""

    prior_strength: float
    clear_prob: float
    min_effect: float
    clustered: bool  # divide counts by the league's attack design effect before the update


class HedgeRule(Hedge):
    """A named candidate hedge rule for the P9 study."""

    name: str


class HedgeEval(_Section):
    """The P9 false-alarm study (scripts/eval_hedging.py)."""

    null_matches: int
    planted_matches: int
    null_seed: int
    planted_seed: int
    wilson_baseline: float
    strong_lane_weights: tuple[float, float, float]
    strong_until_t: int
    candidates: tuple[HedgeRule, ...]


class Router(_Section):
    veto_window_s: int
    t2_ttl_s: int
    impact_t2: float
    t0_metrics: tuple[str, ...]
    negative_metrics: tuple[str, ...]
    negative_words: tuple[str, ...]


class Renditions(_Section):
    cue_duration_s: int
    ticker_duration_s: int
    ticker_metrics: tuple[str, ...]


class DataCard(_Section):
    goals_per_match: tuple[float, float]
    shots_per_team: tuple[int, int]
    xg_per_shot: tuple[float, float]
    pass_completion: tuple[float, float]
    hero_events: tuple[int, int]


class Agents(_Section):
    scout_name: str
    reader_name: str
    mode: Literal["handed", "explore"]
    max_calls: int
    explore_max_calls: int
    max_tool_rounds: int
    max_events: int
    memory_size: int
    timeout_s: int
    reader_max_calls: int

    def budget(self, mode: str) -> int:
        return self.explore_max_calls if mode == "explore" else self.max_calls


class StatsBomb(_Section):
    base_url: str
    competition_id: int
    season_id: int
    match_id: int
    mu_matches: int
    source_length: int
    source_width: int
    shot_xg: Literal["statsbomb", "ours"]


class Xt(_Section):
    """Expected threat surface: rows across the pitch (row 0 at y = 0), cols along it."""

    cols: int
    rows: int
    surface: tuple[tuple[float, ...], ...]

    @model_validator(mode="after")
    def _shape(self) -> Xt:
        if len(self.surface) != self.rows or any(len(r) != self.cols for r in self.surface):
            raise ValueError(f"xt.surface must be {self.rows} rows of {self.cols} values")
        return self


class XtFit(_Section):
    max_iter: int
    tol: float


class XgFit(_Section):
    seasons: tuple[tuple[int, int], ...]
    test_fraction: float
    split_seed: int
    reliability_bins: int
    max_iter: int
    ridge: float


class Settings(_Section):
    seed: int
    pitch: Pitch
    sim: Sim
    xg: Xg
    winprob: Winprob
    winprob_eval: WinprobEval
    moments: Moments
    verifier: Verifier
    hedge: Hedge
    hedge_eval: HedgeEval
    router: Router
    renditions: Renditions
    data_card: DataCard
    agents: Agents
    statsbomb: StatsBomb
    xg_fit: XgFit
    xt: Xt
    xt_fit: XtFit


@dataclass(frozen=True)
class MetricContext:
    """What a metric needs besides the events: the pitch geometry and the xT surface."""

    pitch: Pitch
    xt: Xt

    @classmethod
    def of(cls, s: Settings) -> MetricContext:
        return cls(s.pitch, s.xt)


def load(path: Path = CONFIG_PATH) -> Settings:
    """Read and validate a config file. Call this once, at an entry point."""
    with path.open(encoding="utf-8-sig") as f:
        return Settings.model_validate(yaml.safe_load(f))
