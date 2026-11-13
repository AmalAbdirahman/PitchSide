"""Pass networks (P10): inferred receivers, degree, betweenness, communities and the metric."""

from __future__ import annotations

import itertools

import pytest

from core.config import MetricContext, load
from core.events import Event, Match
from core.facts import Subject, Window
from core.metrics import REGISTRY, compute_metric
from core.network import (
    Weights,
    betweenness,
    communities,
    degree,
    modularity,
    pass_links,
    rank,
    weights,
)

S = load()
CTX = MetricContext.of(S)


def _ev(i: int, kind: str, team: str, pid: str, outcome: str | None = "success") -> Event:
    return Event(
        id=i, t=float(i), period=1, type=kind, team=team, player_id=pid, x=50, y=34, outcome=outcome
    )


def test_receiver_is_the_next_player_on_the_ball_in_the_same_possession() -> None:
    evs = [
        _ev(1, "pass", "A", "a1"),
        _ev(2, "pressure", "B", "b1", None),  # not on the ball: skipped
        _ev(3, "carry", "A", "a2"),  # a1 -> a2
        _ev(4, "pass", "A", "a2"),  # a2 is next on the ball again: no link to oneself
        _ev(5, "pass", "A", "a2"),  # a2 -> a3 (a3 then loses the ball, but received it)
        _ev(6, "pass", "A", "a3", "fail"),  # failed: no link
        _ev(7, "possession_change", "A", "a3", None),
        _ev(8, "pass", "B", "b1"),
        _ev(9, "pass", "A", "a1"),  # the next on-ball event is B's: no link
        _ev(10, "shot", "B", "b2"),
        _ev(11, "pass", "A", "a1"),  # nothing follows: no link
    ]
    found = [(x.passer, x.receiver, x.event_id) for x in pass_links(evs, "A")]
    assert found == [("a1", "a2", 1), ("a2", "a3", 5)]
    assert pass_links(evs, "B") == []  # A has the ball between b1's pass and b2's shot


def test_events_are_read_in_time_order() -> None:
    evs = [_ev(2, "carry", "A", "a2"), _ev(1, "pass", "A", "a1")]
    assert [(x.passer, x.receiver) for x in pass_links(evs, "A")] == [("a1", "a2")]


def test_degree_counts_passes_made_and_received() -> None:
    w: Weights = {("a", "b"): 3, ("b", "a"): 1, ("b", "c"): 2}
    assert degree(w) == {"a": 4, "b": 6, "c": 2}


def test_betweenness_of_a_chain_and_a_star() -> None:
    chain = betweenness({("a", "b"): 1, ("b", "c"): 1})
    assert chain == pytest.approx({"a": 0.0, "b": 0.5, "c": 0.0})
    spokes = ("x", "y", "z")
    star: Weights = {}
    for p in spokes:
        star[("hub", p)] = star[(p, "hub")] = 1
    b = betweenness(star)
    assert b["hub"] == pytest.approx(1.0) and all(b[p] == 0.0 for p in spokes)


def test_betweenness_prefers_the_well_used_route() -> None:
    # a reaches c through d (5 passes each way, length 0.4) rather than through b (length 2).
    b = betweenness({("a", "b"): 1, ("b", "c"): 1, ("a", "d"): 5, ("d", "c"): 5})
    assert b["d"] > 0 and b["b"] == 0.0


def test_betweenness_splits_credit_between_equal_routes() -> None:
    b = betweenness({("a", "b"): 1, ("b", "c"): 1, ("a", "d"): 1, ("d", "c"): 1})
    assert b["b"] == pytest.approx(b["d"]) and b["b"] > 0


def _brute_betweenness(w: Weights) -> dict[str, float]:
    """All simple paths, by enumeration: a check on Brandes for small graphs."""
    nodes = sorted({p for e in w for p in e})
    out = dict.fromkeys(nodes, 0.0)
    for s, t in itertools.permutations(nodes, 2):
        paths = []
        others = [v for v in nodes if v not in (s, t)]
        for r in range(len(others) + 1):
            for mid in itertools.permutations(others, r):
                path = (s, *mid, t)
                if all(e in w for e in zip(path, path[1:], strict=False)):
                    paths.append((sum(1 / w[e] for e in zip(path, path[1:], strict=False)), path))
        if not paths:
            continue
        best = min(d for d, _ in paths)
        short = [p for d, p in paths if abs(d - best) < 1e-9]
        for p in short:
            for v in p[1:-1]:
                out[v] += 1 / len(short)
    n = len(nodes)
    return {v: x / ((n - 1) * (n - 2)) for v, x in out.items()}


def test_betweenness_matches_brute_force_on_a_small_graph() -> None:
    w: Weights = {
        ("a", "b"): 2,
        ("b", "c"): 1,
        ("c", "a"): 3,
        ("b", "d"): 1,
        ("d", "e"): 2,
        ("e", "b"): 1,
        ("c", "e"): 1,
        ("a", "d"): 1,
    }
    assert betweenness(w) == pytest.approx(_brute_betweenness(w))


def test_rank_one_needs_a_unique_leader() -> None:
    assert rank({"a": 0.5, "b": 0.2}, "a") == 1
    assert rank({"a": 0.5, "b": 0.2}, "b") == 2
    assert rank({"a": 0.5, "b": 0.5}, "a") == 2


def test_communities_split_two_groups_joined_by_one_pass() -> None:
    w: Weights = {}
    for g in (("a", "b", "c"), ("x", "y", "z")):
        for p, q in itertools.permutations(g, 2):
            w[(p, q)] = 3
    w[("c", "x")] = 1
    groups = communities(w)
    assert groups == [["a", "b", "c"], ["x", "y", "z"]]
    assert modularity(w, groups) > modularity(w, [["a", "b", "c", "x", "y", "z"]])


def test_weights_count_repeated_links() -> None:
    evs = [
        _ev(1, "pass", "A", "a1"),
        _ev(2, "pass", "A", "a2"),
        _ev(3, "pass", "A", "a1"),
        _ev(4, "carry", "A", "a2"),
    ]
    assert weights(pass_links(evs, "A")) == {("a1", "a2"): 2, ("a2", "a1"): 1}


def test_most_central_player_metric_is_the_rank_with_the_players_passes(match: Match) -> None:
    d = REGISTRY["most_central_player"]
    assert d.subjects == frozenset({"player"}) and d.tolerance(S.verifier) == 0.0
    w = Window(start_t=0, end_t=match.meta.clock.half_t - 0.1)
    team = match.meta.home
    links = pass_links([e for e in match.events if e.t <= w.end_t], team)
    scores = betweenness(weights(links))
    top = max(scores, key=lambda p: scores[p])
    m = compute_metric("most_central_player", Subject(type="player", id=top), w, match.events, CTX)
    assert m is not None and m.value == rank(scores, top)
    mine = [x.event_id for x in links if top in (x.passer, x.receiver)]
    assert (m.n, m.k, m.ids) == (len(links), len(mine), sorted(mine))
    other = next(p for p in scores if p != top)
    m2 = compute_metric(
        "most_central_player", Subject(type="player", id=other), w, match.events, CTX
    )
    assert m2 is not None and m2.value > 1
    nobody = Subject(type="player", id="NOR_99")
    assert compute_metric("most_central_player", nobody, w, match.events, CTX) is None
    team_subject = Subject(type="team", id=team)
    assert compute_metric("most_central_player", team_subject, w, match.events, CTX) is None
