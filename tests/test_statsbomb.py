"""StatsBomb adapter on a small hand-written fixture in StatsBomb's format.

The fixture is invented (fictional teams and players); no StatsBomb data is committed.
The last test runs the real match if scripts/fetch_statsbomb.py has downloaded it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.build_bundle import build, real_source
from core.config import ROOT, Settings, StatsBomb, load
from core.events import Event, Match
from core.facts import Fact, validate_fact
from core.statsbomb import (
    RAW_DIR,
    SbEvent,
    SbLineup,
    SbMatch,
    Scale,
    StatsBombOpenSource,
    make_clock,
    shot_rows,
    team_code,
    to_match,
    xt_rows,
)
from core.xg import ShotFeatures, xg_of

S = load()
HOME, AWAY = 1, 2
PLAYERS = {
    HOME: [(11, "Ada Quill", None), (12, "Bo Renn", "Bo"), (13, "N''Iko Vale", None)],
    AWAY: [(21, "Cy Moss", None), (22, "Dee Lark", None), (23, "Eli Fenn", None)],
}


def ev(
    index: int, period: int, ts: str, kind: str, team: int, possession: int, **extra: Any
) -> dict[str, Any]:
    names = {1: "Fixture Rovers", 2: "Fixture Athletic"}
    out: dict[str, Any] = {
        "index": index,
        "period": period,
        "timestamp": ts,
        "type": {"id": 0, "name": kind},
        "team": {"id": team, "name": names[team]},
        "possession": possession,
        "possession_team": {"id": extra.pop("poss_team", team), "name": "x"},
    }
    if "player" in extra:
        pid = extra.pop("player")
        out["player"] = {"id": pid, "name": f"player {pid}"}
    out.update(extra)
    return out


RAW = [
    ev(1, 1, "00:00:00.000", "Half Start", HOME, 1),
    ev(
        2,
        1,
        "00:00:01.500",
        "Pass",
        HOME,
        1,
        player=11,
        location=[60.0, 40.0],
        **{"pass": {"end_location": [90.0, 20.0]}},
    ),
    ev(3, 1, "00:00:03.000", "Ball Receipt*", HOME, 1, player=12, location=[90.0, 20.0]),
    ev(
        4,
        1,
        "00:00:04.000",
        "Carry",
        HOME,
        1,
        player=12,
        location=[90.0, 20.0],
        carry={"end_location": [100.0, 30.0]},
    ),
    ev(
        5,
        1,
        "00:00:05.000",
        "Pass",
        HOME,
        1,
        player=12,
        location=[100.0, 30.0],
        **{"pass": {"end_location": [110.0, 40.0], "outcome": {"id": 9, "name": "Incomplete"}}},
    ),
    ev(6, 1, "00:00:06.000", "Pressure", AWAY, 2, player=21, location=[12.0, 40.0]),
    ev(
        7,
        1,
        "00:00:08.000",
        "Shot",
        AWAY,
        2,
        player=22,
        location=[108.0, 36.0],
        shot={
            "end_location": [120.0, 38.0, 1.0],
            "outcome": {"id": 97, "name": "Goal"},
            "statsbomb_xg": 0.31234,
        },
    ),
    ev(
        8,
        1,
        "00:00:20.000",
        "Pass",
        HOME,
        3,
        player=13,
        location=[60.0, 40.0],
        **{"pass": {"end_location": [70.0, 40.0]}},
    ),
    ev(9, 1, "00:10:00.000", "Own Goal Against", HOME, 3, player=13, location=[6.0, 42.0]),
    ev(10, 1, "00:10:00.000", "Own Goal For", AWAY, 3),
    ev(
        11,
        1,
        "00:20:00.000",
        "Foul Committed",
        AWAY,
        4,
        poss_team=HOME,
        player=23,
        location=[50.0, 10.0],
        foul_committed={"card": {"id": 7, "name": "Yellow Card"}},
    ),
    ev(12, 1, "00:46:10.400", "Half End", HOME, 4),
    ev(13, 2, "00:00:00.000", "Half Start", AWAY, 5),
    ev(
        14,
        2,
        "00:05:00.000",
        "Substitution",
        HOME,
        5,
        poss_team=AWAY,
        player=12,
        substitution={"replacement": {"id": 13, "name": "x"}},
    ),
    ev(
        15,
        2,
        "00:06:00.000",
        "Bad Behaviour",
        AWAY,
        5,
        player=21,
        bad_behaviour={"card": {"id": 5, "name": "Red Card"}},
    ),
    ev(16, 2, "00:47:00.500", "Half End", AWAY, 5),
    ev(
        17,
        5,
        "00:00:30.000",
        "Shot",
        HOME,
        6,
        player=11,
        location=[108.0, 40.0],
        shot={
            "end_location": [120.0, 40.0, 1.0],
            "outcome": {"id": 97, "name": "Goal"},
            "statsbomb_xg": 0.78,
        },
    ),
]
ROW = {
    "match_id": 99,
    "home_team": {"home_team_id": HOME, "home_team_name": "Fixture Rovers"},
    "away_team": {"away_team_id": AWAY, "away_team_name": "Fixture Athletic"},
}
LINEUPS = [
    {
        "team_id": t,
        "team_name": {1: "Fixture Rovers", 2: "Fixture Athletic"}[t],
        "lineup": [
            {"player_id": i, "player_name": n, "player_nickname": nick} for i, n, nick in ps
        ],
    }
    for t, ps in PLAYERS.items()
]


def _fixture_match(provider_xg: bool, raw_events: list[dict[str, Any]] = RAW) -> Match:
    return to_match(
        SbMatch.model_validate(ROW),
        [SbEvent.model_validate(e) for e in raw_events],
        [SbLineup.model_validate(t) for t in LINEUPS],
        Scale.of(S.statsbomb, S.pitch),
        S.pitch,
        S.xg,
        provider_xg,
    )


@pytest.fixture(scope="module")
def fx() -> Match:
    return _fixture_match(S.statsbomb.shot_xg == "statsbomb")


def _of(m: Match, etype: str) -> list[Event]:
    return [e for e in m.events if e.type == etype]


def test_meta_codes_names_and_clock(fx: Match) -> None:
    meta = fx.meta
    assert (meta.home, meta.away, meta.synthetic, meta.seed) == ("FIX", "FIC", False, None)
    assert meta.teams["FIX"].players == {
        "FIX_11": "Ada Quill",
        "FIX_12": "Bo",
        "FIX_13": "N'Iko Vale",
    }
    # Period 1 ended at 46:10.4 -> 2770 s, so period 2 starts at 2772; the shoot-out is not on the clock.
    assert meta.clock.period_starts == [0, 2772]
    assert meta.clock.end_t == 2772 + 47 * 60 + 1


def test_coordinates_rescale_without_mirroring(fx: Match) -> None:
    p = _of(fx, "pass")[0]
    assert (p.x, p.y, p.end_x, p.end_y) == (52.5, 34.0, 78.8, 17.0)
    assert (p.outcome, p.pass_distance) == ("success", 31.3)
    assert _of(fx, "pass")[1].outcome == "fail"
    carry = _of(fx, "carry")[0]
    assert (carry.end_x, carry.end_y, carry.outcome) == (87.5, 25.5, "success")


def test_shot_goal_and_xg(fx: Match) -> None:
    shot = _of(fx, "shot")
    assert len(shot) == 1, "the shoot-out shot is dropped"
    assert (shot[0].team, shot[0].outcome, shot[0].xg, shot[0].x) == ("FIC", "goal", 0.312, 94.5)
    goal = _of(fx, "goal")[0]
    assert (goal.team, goal.player_id, goal.t, goal.outcome) == ("FIC", "FIC_22", 8.0, None)


def test_real_shots_use_statsbomb_xg_by_default() -> None:
    assert S.statsbomb.shot_xg == "statsbomb"


def test_our_model_on_request() -> None:
    shot = _of(_fixture_match(provider_xg=False), "shot")[0]
    assert shot.xg == round(xg_of(ShotFeatures(94.5, 30.6), S.pitch, S.xg), 3)


def test_shot_without_statsbomb_xg_falls_back_to_ours() -> None:
    raw = copy.deepcopy(RAW)
    for e in raw:
        if "shot" in e:
            e["shot"].pop("statsbomb_xg")
    shot = _of(_fixture_match(True, raw), "shot")[0]
    assert shot.xg == round(xg_of(ShotFeatures(94.5, 30.6), S.pitch, S.xg), 3)


def test_own_goal_counts_for_the_other_side(fx: Match) -> None:
    og = _of(fx, "goal")[1]
    assert (og.team, og.player_id, og.outcome) == ("FIC", "FIX_13", "own_goal")
    assert (og.x, og.y) == (99.8, 32.3)  # mirrored into the scoring side's view
    assert len(_of(fx, "goal")) == 2, "Own Goal For is not a second goal"


def test_substitution_cards_and_pressure(fx: Match) -> None:
    sub = _of(fx, "substitution")[0]
    assert (sub.player_id, sub.player_off, sub.x, sub.y, sub.t) == (
        "FIX_13",
        "FIX_12",
        52.5,
        0.0,
        3072.0,
    )
    assert fx.meta.clock.minute(sub.t) == 51
    assert [(c.player_id, c.card) for c in _of(fx, "card")] == [
        ("FIC_23", "yellow"),
        ("FIC_21", "red"),
    ]
    assert _of(fx, "pressure")[0].team == "FIC"


def test_possession_change_for_the_side_that_lost_the_ball(fx: Match) -> None:
    changes = _of(fx, "possession_change")
    # Lost by FIX at the end of the incomplete pass. Not after FIC's shot (a goal restart).
    first = changes[0]
    assert (first.team, first.player_id, first.x, first.y, first.t) == (
        "FIX",
        "FIX_12",
        96.2,
        34.0,
        6.0,
    )
    assert all(c.team != "FIC" or c.t > 8.0 for c in changes)


def test_dropped_types_and_monotonic_ids(fx: Match) -> None:
    kept = {e.type for e in fx.events}
    assert kept <= {
        "pass",
        "carry",
        "shot",
        "goal",
        "substitution",
        "card",
        "pressure",
        "possession_change",
    }
    assert [e.id for e in fx.events] == list(range(1, len(fx.events) + 1))
    assert all(a.t <= b.t for a, b in zip(fx.events, fx.events[1:], strict=False))


def test_team_codes_never_collide() -> None:
    assert team_code("Fixture Rovers") == "FIX"
    assert team_code("Fixture Athletic", "FIX") == "FIC"
    assert team_code("1. FC Köln") == "FCK"


def test_clock_ignores_the_shoot_out() -> None:
    raw = [SbEvent.model_validate(e) for e in RAW]
    assert len(make_clock(raw).period_starts) == 2


REAL = RAW_DIR / "events" / f"{S.statsbomb.match_id}.json"


@pytest.mark.skipif(
    not REAL.exists(), reason="run scripts/fetch_statsbomb.py to test the real match"
)
def test_real_match_goal_minutes(settings: Settings) -> None:
    match, league = StatsBombOpenSource(
        settings.statsbomb, settings.pitch, settings.xg, settings.statsbomb.match_id
    ).load()
    goals = [(match.meta.clock.minute(e.t), e.team, e.outcome) for e in _of(match, "goal")]
    # 2018 World Cup final as published: own goal 18', 28', pen 38', 59', 65', 69'.
    assert goals == [
        (18, "FRA", "own_goal"),
        (28, "CRO", None),
        (38, "FRA", None),
        (59, "FRA", None),
        (65, "FRA", None),
        (69, "CRO", None),
    ]
    assert 0.005 < league.mu < 0.03
    assert sum(league.lane_shares.values()) == pytest.approx(1)


def _write_fixture(root: Path, sb: StatsBomb) -> None:
    """The fixture as files in the open-data layout: the match (99) and one more for mu (98)."""
    other = {**ROW, "match_id": 98}
    (root / "matches" / str(sb.competition_id)).mkdir(parents=True)
    (root / "matches" / str(sb.competition_id) / f"{sb.season_id}.json").write_text(
        json.dumps([ROW, other])
    )
    for kind, data in (("events", RAW), ("lineups", LINEUPS)):
        (root / kind).mkdir()
        for mid in (98, 99):
            (root / kind / f"{mid}.json").write_text(json.dumps(data))


def test_bundle_builds_from_statsbomb_files(tmp_path: Path, settings: Settings) -> None:
    _write_fixture(tmp_path, settings.statsbomb)
    source = StatsBombOpenSource(
        settings.statsbomb, settings.pitch, settings.xg, 99, raw_dir=tmp_path
    )
    hero, _ = build(settings, None, use_cache=False, source=source)
    assert hero["meta"]["data_source"] == "StatsBomb Open Data, match 99"
    assert hero["meta"]["synthetic"] is False
    goals = [f["text"]["canonical_en"] for f in hero["facts"] if f["claim"]["metric"] == "goals"]
    assert any("Own goal, N'Iko Vale, 11'" in g for g in goals), goals
    for f in hero["facts"] + hero["log"]:
        validate_fact(Fact.model_validate(f, strict=True))


def test_real_bundles_never_go_to_docs(settings: Settings) -> None:
    with pytest.raises(SystemExit):
        real_source(settings, 99, ROOT / "docs" / "data")


def test_shot_rows_carry_the_model_features() -> None:
    raw = [SbEvent.model_validate(e) for e in RAW]
    raw.append(
        SbEvent.model_validate(
            ev(
                18,
                2,
                "00:10:00.000",
                "Shot",
                HOME,
                7,
                player=11,
                location=[109.0, 40.0],
                under_pressure=True,
                shot={
                    "end_location": [120.0, 40.0],
                    "outcome": {"id": 98, "name": "Off T"},
                    "statsbomb_xg": 0.05,
                    "body_part": {"id": 37, "name": "Head"},
                    "type": {"id": 62, "name": "Free Kick"},
                },
            )
        )
    )
    rows = shot_rows(raw, 99, Scale.of(S.statsbomb, S.pitch))
    assert len(rows) == 2, "the shoot-out shot is not a training row"
    first, second = rows
    assert (first.goal, first.statsbomb_xg, first.features.header) == (True, 0.31234, False)
    f = second.features
    assert (second.goal, f.header, f.free_kick, f.under_pressure, f.penalty) == (
        False,
        True,
        True,
        True,
        False,
    )
    assert (f.x, f.y) == (95.4, 34.0)


def test_xt_rows_from_the_fixture() -> None:
    rows = xt_rows([SbEvent.model_validate(e) for e in RAW], 99, Scale.of(S.statsbomb, S.pitch))
    got = [(r.possession, r.team_id, r.action.kind, r.action.success) for r in rows]
    # pass, carry, failed pass, shot (goal), pass; the shoot-out shot is not an xT action
    assert got == [
        (1, 1, "move", True),
        (1, 1, "move", True),
        (1, 1, "move", False),
        (2, 2, "shot", True),
        (3, 1, "move", True),
    ]
    first = rows[0].action
    assert (first.x, first.y, first.end_x, first.end_y) == (52.5, 34.0, 78.8, 17.0)


def test_penalties_are_not_xt_shots() -> None:
    raw = copy.deepcopy(RAW)
    shot = next(e for e in raw if e["index"] == 7)
    shot["shot"]["type"] = {"id": 88, "name": "Penalty"}
    rows = xt_rows([SbEvent.model_validate(e) for e in raw], 99, Scale.of(S.statsbomb, S.pitch))
    assert all(r.action.kind == "move" for r in rows)
