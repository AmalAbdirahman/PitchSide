"""R2: npxG and field tilt in the registry, their series, the agents' fixed metric list, and the
bundle's build date."""

from __future__ import annotations

import json
from datetime import date
from typing import get_args

import pytest

from app.reader import reader_schema
from app.scout_schema import output_schema
from app.series import tilt_at
from app.stamp import stamp
from core.config import ROOT, MetricContext, load
from core.events import Event, Match
from core.facts import AgentMetricName, MetricName, Subject, Window
from core.metrics import REGISTRY, require_metric

S = load()
CTX = MetricContext.of(S)
LINE = S.pitch.final_third_x
WHOLE = Window(start_t=0.0, end_t=5400.0)


def _shot(i: int, team: str, xg: float, penalty: bool | None = None) -> Event:
    return Event(
        id=i, t=float(i), period=1, type="shot", team=team, player_id=f"{team}_9",
        x=95, y=34, outcome="saved", xg=xg, penalty=penalty,
    )  # fmt: skip


def _pass(i: int, team: str, x: float) -> Event:
    return Event(
        id=i, t=float(i), period=1, type="pass", team=team, player_id=f"{team}_8",
        x=x, y=34, end_x=x + 5, end_y=34, outcome="success",
    )  # fmt: skip


def _team(team: str) -> Subject:
    return Subject(type="team", id=team)


def test_npxg_leaves_out_penalties() -> None:
    evs = [_shot(1, "NOR", 0.2), _shot(2, "NOR", 0.79, penalty=True), _shot(3, "NOR", 0.1)]
    m = require_metric("npxg_sum", _team("NOR"), WHOLE, evs, CTX)
    assert m.value == pytest.approx(0.3) and m.ids == [1, 3]
    assert require_metric("xg_sum", _team("NOR"), WHOLE, evs, CTX).value == pytest.approx(1.09)


def test_npxg_equals_xg_on_the_synthetic_match(match: Match) -> None:
    # The simulator awards no penalties; the KPI card and the docs say so.
    assert not any(e.penalty for e in match.events)
    for team in (match.meta.home, match.meta.away):
        npxg = require_metric("npxg_sum", _team(team), WHOLE, match.events, CTX).value
        assert npxg == require_metric("xg_sum", _team(team), WHOLE, match.events, CTX).value


def test_field_tilt_counts_both_teams_final_third_passes() -> None:
    evs = [_pass(1, "NOR", LINE), _pass(2, "NOR", LINE + 10), _pass(3, "NOR", LINE - 1)]
    evs += [_pass(4, "HAR", LINE + 2), _pass(5, "HAR", 10)]
    nor = require_metric("field_tilt", _team("NOR"), WHOLE, evs, CTX)
    har = require_metric("field_tilt", _team("HAR"), WHOLE, evs, CTX)
    assert (nor.k, nor.n, nor.ids) == (2, 3, [1, 2])
    assert nor.value + har.value == pytest.approx(100)


def test_field_tilt_is_for_teams_only_and_respects_the_window() -> None:
    evs = [_pass(10, "NOR", LINE), _pass(500, "HAR", LINE)]
    d = REGISTRY["field_tilt"]
    assert d.compute(evs, Subject(type="player", id="NOR_8"), WHOLE, CTX) is None
    early = Window(start_t=0.0, end_t=100.0)
    assert require_metric("field_tilt", _team("NOR"), early, evs, CTX).value == 100
    assert tilt_at(evs, "NOR", 1000.0, 300, CTX) is None  # no final-third passes in the window


def test_the_bundle_carries_the_new_series() -> None:
    hero = json.loads((ROOT / "docs" / "data" / "hero.json").read_text(encoding="utf-8"))
    series, home, away = hero["series"], hero["meta"]["home"], hero["meta"]["away"]
    assert series["tilt_window_s"] == S.series.tilt_window_s
    for team in (home, away):
        assert len(series["teams"][team]["npxg_sum"]) == len(series["t"])
        assert len(series["teams"][team]["field_tilt"]) == len(series["t"])
    pairs = zip(
        series["teams"][home]["field_tilt"], series["teams"][away]["field_tilt"], strict=True
    )
    assert all(a is None and b is None or abs(a + b - 100) < 0.01 for a, b in pairs)
    assert date.fromisoformat(hero["meta"]["built"])


def test_agents_keep_their_fixed_metric_list() -> None:
    # Growing the agents' list would change every cached Scout and Reader request.
    agent = set(get_args(AgentMetricName))
    assert {"npxg_sum", "field_tilt"} <= set(get_args(MetricName)) - agent
    assert len(agent) == 15
    for schema in (json.dumps(output_schema()), json.dumps(reader_schema())):
        assert "npxg_sum" not in schema and "field_tilt" not in schema


def test_build_date_is_kept_when_nothing_else_changed() -> None:
    hero = {"meta": {"home": "NOR"}, "facts": [1]}
    first = stamp(hero, None, date(2026, 11, 14))
    assert first["meta"]["built"] == "2026-11-14"
    assert stamp(hero, first, date(2026, 12, 1))["meta"]["built"] == "2026-11-14"
    changed = {"meta": {"home": "NOR"}, "facts": [1, 2]}
    assert stamp(changed, first, date(2026, 12, 1))["meta"]["built"] == "2026-12-01"
