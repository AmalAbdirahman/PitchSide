"""Typed, validated settings loaded from config.yaml.

Every threshold and constant lives in config.yaml (SPEC.md). `load()` reads it once at an
entry point and the resulting frozen `Settings` is passed down as an argument; no module reads
the file on import or looks the values up through a global.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

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
    goal_width: float


class Winprob(_Section):
    k: int
    goal_cap: int
    match_minutes: int


class Moments(_Section):
    check_every_s: int
    first_check_s: int
    overload_window_s: int
    overload_min_share: float
    turnover_window_s: int
    turnover_min_count: int


class Verifier(_Section):
    n_min: int
    z: float
    lane_baseline: float
    tol_share_pp: float
    tol_xg: float
    tol_speed: float
    early_markers: tuple[str, ...]
    suggest_markers: tuple[str, ...]
    intent_patterns: tuple[str, ...]


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
    max_calls: int
    max_retries: int
    timeout_s: int


class Settings(_Section):
    seed: int
    pitch: Pitch
    sim: Sim
    xg: Xg
    winprob: Winprob
    moments: Moments
    verifier: Verifier
    router: Router
    renditions: Renditions
    data_card: DataCard
    agents: Agents


def load(path: Path = CONFIG_PATH) -> Settings:
    """Read and validate a config file. Call this once, at an entry point."""
    with path.open(encoding="utf-8-sig") as f:
        return Settings.model_validate(yaml.safe_load(f))
