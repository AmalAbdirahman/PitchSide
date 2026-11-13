# Pass networks: method

The Analysis view draws each team's pass network for the current half, and at the end of each half the system can say who was the most central player in a team's passing. This note says how the network is built, what the numbers mean, and how the claim is verified. Code: `core/network.py`; metric: `most_central_player` in `core/metrics.py`.

## Building the network

- **Links.** A link is a completed pass from one player to a team-mate. Neither the simulator nor our StatsBomb mapping records who received a pass, so the receiver is **inferred**: the player on the next on-ball event (pass, carry or shot) of the same team, provided no possession change or opposing on-ball event comes first. If the passer is next on the ball again, the pass is not a link. A completed pass whose receiver cannot be inferred (for example the last event of a window) is left out; the text calls the rest "linked passes".
- **Weights.** The weight of a link from A to B is the number of linked passes from A to B in the window.
- **Positions.** A player's dot is the mean of where they passed from and where they received, in the team's attacking frame (every team drawn attacking left to right).
- **Window.** The view shows the current half, sampled every 5 minutes, never including events after the sample time. The half-time claims use the whole half.

In the simulator the next player on the ball is drawn by position (`sim/generate.py`), not by who the passer aimed at, so the synthetic networks show the simulator's role weighting rather than real passing patterns. StatsBomb data does record recipients; using them is roadmap.

## Measures

- **Degree**: passes a player made plus passes they received (weighted degree).
- **Betweenness**: the share of shortest passing routes between pairs of team-mates that run through the player (Brandes 2001). The graph is directed and the length of a link is 1 / its weight, so a much-used link is short. Normalised by (n − 1)(n − 2) for n players, so it lies between 0 and 1. When two shortest routes tie, each gets half the credit.
- **Communities**: a simple split into groups that pass mostly among themselves. Starting from one group per player, the two groups whose merge raises modularity most are merged, until no merge raises it (greedy modularity, Clauset, Newman and Moore 2004), on the undirected graph with the two directions' weights added. It is deterministic. On the replay it mostly separates defenders and midfielders from forwards.

Tests check betweenness against brute-force enumeration of every path on a small graph, plus chain, star, weighted-route and tied-route cases, and a two-cluster community split.

## The verifiable claim

`most_central_player` is a player's **rank** by betweenness in their team's network over the claim window: 1 means they are the unique most central player. A tie for the top gives both players rank 2, so nobody can be called "the most central" on a tie. The verifier recomputes the network from the event log and requires the claimed rank exactly. The text's numbers are the player's linked passes made or received (k) and the team's linked passes (n), which the verifier also checks.

The half-time moment (`core/moments._pass_hub`) names the leader only if the team linked at least `moments.hub_min_passes` (20, chosen by hand) passes in that half. On the replay that gives four facts, for example "E. Lindqvist was the most central player in Northbridge's passing in the first half: involved in 11 of 38 linked passes." A seeded test claim names a team-mate instead; the verifier rejects it because his recomputed rank is 2.

## Limits

- Inferred receivers, as above. Passes can also be credited to the wrong receiver when a team-mate is next on the ball after a deflection or a loose ball that our events do not show.
- A half's network has only 35 to 60 linked passes, so the ranking can change with a few passes. The claim says "most central", not by how much; the panel shows the betweenness values.
- Betweenness depends on the choice of 1 / weight as distance; other choices (for example −log of the pass share) can give a different leader.
- No validation against an external standard was possible: there is no ground truth for "central". The metric is defined, recomputed and tested, not fitted.
