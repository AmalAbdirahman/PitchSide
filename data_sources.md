# Data sources

PitchSide runs on two sources. Both produce the same `Match` model (`core/events.py`) through an `EventSource` (`core/sources.py`).

| source | what it is | where it lives | committed? |
|---|---|---|---|
| Synthetic | seeded simulator, fictional clubs and players (`sim/generate.py`) | `data/events.json`, `data/league_stats.json` | yes |
| StatsBomb Open Data | real event data from StatsBomb's public repository | `data/external/statsbomb/` (raw), `data/external/bundles/` (built) | **no**, both folders are git-ignored |

## StatsBomb Open Data

Data: **StatsBomb Open Data**, https://github.com/statsbomb/open-data. The data is StatsBomb's property and is used under the StatsBomb Public Data User Agreement (last updated 8 September 2023), which ships with the repository as `LICENSE.pdf`.

What the agreement says, in short (read the PDF for the binding text):
- Use is for analysis and research (1.1). No commercial use of the data or of analysis derived from it (1.2.2).
- The data may not be edited, distorted, distributed or reproduced to a third party (1.2.1), or modified, transferred or distributed without StatsBomb's written consent (7).
- Analysis may be shared publicly (preamble), but any publication of analysis must credit StatsBomb with the StatsBomb logo (1.4). The repository README asks for the same: state the source as StatsBomb and use the logo from their media pack.
- StatsBomb asks users to register at statsbomb.com/resource-centre (2.2).

What this repo does about it:
- `scripts/fetch_statsbomb.py` downloads raw files into `data/external/statsbomb/`, which is git-ignored. Raw data is never committed.
- `python -m app.build_bundle --source statsbomb` writes the bundle to `data/external/bundles/<match id>/`, also git-ignored, and refuses to write into `docs/`. A bundle carries the full mapped event log (every pass, carry and shot with coordinates and player names), which is a reformatted copy of StatsBomb's data, so publishing it on GitHub Pages is not covered by the agreement as written.
- Committed results derived from StatsBomb data are aggregates only: the xG coefficients and test scores (`data/xg_fit.json`), and the 192-cell xT surface with its test scores (`data/xt_fit.json`, figures in `docs/methods/`). No event, shot or player record is committed.
- Tests use a small hand-written fixture in StatsBomb's format with invented teams and players (`tests/test_statsbomb.py`), never StatsBomb data.
- Publishing a real-match replay on the site is **not done** (see the README roadmap). It needs a decision on what may be published (for example aggregate analysis only, or written consent) and the StatsBomb logo on the page.

### Default match

2018 FIFA World Cup final, France 4–2 Croatia (competition 43, season 3, match 8658), chosen by hand in `config.yaml`. The league xG rate `mu` that win probability shrinks towards is the mean xG per team per minute over the first 10 other matches of that season by match id (`statsbomb.mu_matches`); the replayed match is excluded. Run on 2026-11-13 with StatsBomb's xG on real shots: mu = 0.01355 (0.01316 with our fitted model instead).

### Mapping decisions (`core/statsbomb.py`)

**Coordinates.** StatsBomb records each event from the acting team's point of view on a 120 x 80 pitch, attacking left to right, with y = 0 on that team's left touchline. That is already our convention, so x is scaled by 105/120 and y by 68/80 (pitch size from `config.yaml`) and nothing is mirrored. Values are rounded to 0.1 m like the simulator's.

**Time.** Each period's timestamps restart at 0. Match time lays periods 1-4 end to end: a period starts at the previous period's start plus its last timestamp rounded down plus 2 s, so match time never goes backwards and every first-half event is at least 0.1 s before the second half starts (the half-time windows rely on that). `meta.clock` stores the period starts; the official minute restarts at 45, 90 and 105 (`Clock.minute`). In the 2018 final the first half ran to 48:26, so the second half starts at match time 2908 s; the goal minutes come out as published (18, 28, 38, 59, 65, 69), checked in `tests/test_statsbomb.py`. Stoppage time is shown as the running minute (a first-half event at 45:30 is the 46th minute, as in StatsBomb's own `minute` field), not as "45+1".

**Penalty shoot-out.** Period 5 is dropped: it is not open play and is not on the match clock.

**Teams and players.** Team codes are the first three letters of the team name in capitals (France -> FRA, Croatia -> CRO); if both sides would get the same code, the away code ends with the name's last letter instead. Player ids are `<team code>_<StatsBomb player id>`. Names come from the lineups: the nickname when StatsBomb gives one, else the full name, with StatsBomb's doubled apostrophes repaired (N''Golo -> N'Golo). Unused substitutes are in the roster.

**Event types kept.**

| StatsBomb | ours | notes |
|---|---|---|
| Pass | `pass` | every pass including set pieces and throw-ins. No `outcome` in StatsBomb means completed (`success`); any outcome (Incomplete, Out, Pass Offside, Unknown, Injury Clearance) is `fail`. `pass_distance` is recomputed in metres. |
| Carry | `carry` | always `success`; losing the ball shows up as a possession change instead. |
| Shot | `shot` | outcome Goal -> `goal`; Saved, Saved To Post -> `on_target`; Off T, Wayward, Post, Saved Off Target -> `off_target`; Blocked -> `blocked`. An unknown outcome stops the build rather than guess. Penalties and free kicks are kept as shots; P7 (roadmap) plans to treat penalties separately. |
| Shot with outcome Goal | `shot` and a `goal` | the goal event has the shot's time and place, as the simulator emits one after a scoring shot. |
| Own Goal Against | `goal`, outcome `own_goal` | credited to the other side as on the scoreboard, with the player who put it in; the location is mirrored into the scoring side's view. Wording says "Own goal, <player>". |
| Own Goal For | dropped | the same goal seen from the other side; keeping it would count it twice. |
| Substitution | `substitution` | `player_id` is the player coming on, `player_off` the one going off. StatsBomb has no location, so the simulator's spot (52.5, 0) is used. |
| Foul Committed / Bad Behaviour with a card | `card` | Yellow Card -> `yellow`, Second Yellow -> `second yellow`, Red Card -> `red`. The foul itself is dropped. |
| Pressure | `pressure` | the pressing side's event, as in the simulator. |
| (derived) | `possession_change` | when the possessing team changes between consecutive StatsBomb possessions within a period, for the side that lost the ball, at its last touch (a pass's end point, else the event's location), timed at the first event of the new possession. Not after a shot, because the simulator does not count those either. |

**Event types dropped:** Ball Receipt, Ball Recovery, Duel, Clearance, Miscontrol, Block, Goal Keeper, Dribble, Dispossessed, Foul Won, Foul Committed without a card, Dribbled Past, Interception, Injury Stoppage, Camera On, Camera off, Half Start, Half End, Starting XI, Shield, Referee Ball-Drop, Player On, Player Off, Error, Tactical Shift, Offside, 50/50, and any type not listed above. None of them feeds a metric in `core/metrics.py`.

In the 2018 final the 2978 StatsBomb events map to 1845 of ours: 846 passes, 617 carries, 254 pressures, 92 possession changes, 23 shots, 6 goals, 5 substitutions, 2 cards.

**Fields we do not have.** StatsBomb open data has no ball, shot or player speeds and no pass difficulty. Those fields stay empty. The `top_shot_speed` metric only counts shots with a measured speed, so on real data it produces no claim at all rather than a guess.

**xG.** Real shots carry StatsBomb's own `statsbomb_xg` (`statsbomb.shot_xg: statsbomb` in `config.yaml`), because it scored better than our fitted model on held-out matches (log loss 0.2442 against 0.2655, `docs/methods/xg.md`). A shot without `statsbomb_xg` falls back to our model, and `shot_xg: ours` uses our model for every shot. The simulator always uses our model, since its shots have only a location. Either way xG is a model estimate: the verifier checks that xG claims add up the event log's values, not that the model is right. With our model, the 2018 final is in its training set, so its xG would be in-sample.

### What the pipeline does with it

`python -m app.build_bundle --source statsbomb` runs the full pipeline with the rule-based drafter (the Scout's prompt and cache describe the synthetic match, so the agent is not used on real data). Run on 2026-11-13 on the 2018 final: 40 drafts (34 rule drafts plus 6 with injected errors), 34 verified, 2 corrected, 4 rejected; all 6 injected errors were caught; 36 facts routed (13 T0, 21 T1, 2 T2); 0 renditions failed the number check.
