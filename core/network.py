"""Pass networks (P10): who passes to whom, and who holds a team's passing together.

Neither the simulator nor our StatsBomb mapping records a pass's receiver, so it is inferred: the
receiver of a completed pass is the player on the next on-ball event (pass, carry or shot) of the
same team, if no possession change or opposing on-ball event comes first. A pass to oneself
(the same player is next on the ball) is not a link. Every function takes the events of one window.

- degree: passes a player made plus passes they received (weighted degree);
- betweenness: Brandes' algorithm on the directed graph, with distance 1 / passes on a link, so a
  frequently used link is short; normalised by (n - 1)(n - 2);
- communities: greedy modularity merging on the undirected weighted graph (Clauset, Newman and
  Moore 2004), deterministic, for a simple split into groups that pass among themselves.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass

from core.events import Event

ON_BALL = ("pass", "carry", "shot")
EPS = 1e-9


@dataclass(frozen=True)
class Link:
    passer: str
    receiver: str
    event_id: int  # the pass


def _receiver(after: Sequence[Event], team: str) -> str | None:
    for e in after:
        if e.type == "possession_change":
            return None
        if e.type in ON_BALL:
            return e.player_id if e.team == team else None
    return None


def pass_links(events: Sequence[Event], team: str) -> list[Link]:
    """Completed passes of `team` with an inferred receiver other than the passer."""
    evs = sorted(events, key=lambda e: (e.t, e.id))
    out = []
    for i, e in enumerate(evs):
        if e.type != "pass" or e.team != team or e.outcome != "success":
            continue
        to = _receiver(evs[i + 1 :], team)
        if to is not None and to != e.player_id:
            out.append(Link(e.player_id, to, e.id))
    return out


Weights = dict[tuple[str, str], int]


def weights(links: Sequence[Link]) -> Weights:
    w: Weights = {}
    for x in links:
        w[(x.passer, x.receiver)] = w.get((x.passer, x.receiver), 0) + 1
    return w


def players(w: Weights) -> list[str]:
    return sorted({p for edge in w for p in edge})


def degree(w: Weights) -> dict[str, int]:
    out = dict.fromkeys(players(w), 0)
    for (a, b), n in w.items():
        out[a] += n
        out[b] += n
    return out


Adjacency = dict[str, list[tuple[str, float]]]


def _adjacency(w: Weights) -> Adjacency:
    adj: Adjacency = {p: [] for p in players(w)}
    for (a, b), n in sorted(w.items()):
        adj[a].append((b, 1 / n))
    return adj


def _paths(adj: Adjacency, src: str) -> tuple[list[str], dict[str, list[str]], dict[str, float]]:
    """Dijkstra from src: nodes in order of distance, predecessors and shortest-path counts."""
    nodes = list(adj)
    dist, sigma = {src: 0.0}, dict.fromkeys(nodes, 0.0)
    pred: dict[str, list[str]] = {v: [] for v in nodes}
    sigma[src], order, done = 1.0, [], set()
    heap = [(0.0, src)]
    while heap:
        d, v = heapq.heappop(heap)
        if v in done:
            continue
        done.add(v)
        order.append(v)
        for b, length in adj[v]:
            nd = d + length
            if b not in dist or nd < dist[b] - EPS:
                dist[b], sigma[b], pred[b] = nd, sigma[v], [v]
                heapq.heappush(heap, (nd, b))
            elif abs(nd - dist[b]) <= EPS:
                sigma[b] += sigma[v]
                pred[b].append(v)
    return order, pred, sigma


def betweenness(w: Weights) -> dict[str, float]:
    """Share of shortest passing paths between other players that run through each player."""
    adj = _adjacency(w)
    nodes = list(adj)
    out = dict.fromkeys(nodes, 0.0)
    for s in nodes:
        order, pred, sigma = _paths(adj, s)
        delta = dict.fromkeys(nodes, 0.0)
        for v in reversed(order):
            for u in pred[v]:
                delta[u] += sigma[u] / sigma[v] * (1 + delta[v])
            if v != s:
                out[v] += delta[v]
    scale = (len(nodes) - 1) * (len(nodes) - 2)
    return {v: (b / scale if scale else 0.0) for v, b in out.items()}


def rank(scores: dict[str, float], player: str) -> int:
    """1 + the number of other players scoring at least as high, so a tie for the top is rank 2:
    only a unique leader is rank 1."""
    me = scores[player]
    return 1 + sum(s >= me - EPS for p, s in scores.items() if p != player)


def _undirected(w: Weights) -> dict[frozenset[str], int]:
    u: dict[frozenset[str], int] = {}
    for (a, b), n in w.items():
        u[frozenset((a, b))] = u.get(frozenset((a, b)), 0) + n
    return u


def modularity(w: Weights, groups: Sequence[Sequence[str]]) -> float:
    u = _undirected(w)
    total = sum(u.values())
    if not total:
        return 0.0
    deg = degree(w)
    q = 0.0
    for g in groups:
        inside = sum(n for e, n in u.items() if e <= set(g))
        q += inside / total - (sum(deg[p] for p in g) / (2 * total)) ** 2
    return q


def communities(w: Weights) -> list[list[str]]:
    """Merge the pair of groups that raises modularity most until no merge raises it."""
    groups = [[p] for p in players(w)]
    while len(groups) > 1:
        base = modularity(w, groups)
        best: tuple[float, int, int] | None = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                merged = [g for k, g in enumerate(groups) if k not in (i, j)]
                gain = modularity(w, [*merged, groups[i] + groups[j]]) - base
                if gain > EPS and (best is None or gain > best[0] + EPS):
                    best = (gain, i, j)
        if best is None:
            break
        _, i, j = best
        groups = [g for k, g in enumerate(groups) if k not in (i, j)] + [
            sorted(groups[i] + groups[j])
        ]
    return sorted(sorted(g) for g in groups)
