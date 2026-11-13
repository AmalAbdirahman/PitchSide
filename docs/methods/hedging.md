# Hedging lane-share claims: method and false-alarm study

A lane-overload claim ("Since the 58th minute, 64% of Northbridge attacks have gone down the left") is a statement about a share, from a small and noisy sample. The hedge decides how strongly it may be worded: **early** ("early sign", too few attacks), **suggests** ("the numbers suggest…"), or **clear** (a plain statement). This note covers how that decision is made, what was wrong with the first rule, and the study behind the change.

Numbers come from one run of `python scripts/eval_hedging.py` on 2026-11-13 at commit da95a22; the full record is `data/hedging_eval.json`. All matches in the study are synthetic (the seeded simulator).

## Where claims come from

The moment detector (`core/moments.overload_moments`) looks at each team every 5 minutes from the 10th minute: 17 looks per match. Each look takes the team's attacks over the last 15 minutes (or since its latest substitution), finds the lane with the largest share, and raises an overload if that share is at least 50% from at least 6 attacks, unless it already raised the same lane in the last 15 minutes. Every raised overload becomes a claim, and the hedge decides its wording.

An attack is a pass or carry ending in the final third, or a shot (`core/shares.py`).

## The rule before P9 (Wilson)

A 95% Wilson interval for the top lane's share; "clear" if the interval excludes a baseline of 1/3. Two problems:

1. **Repeated looks.** 17 looks per team per match, each picking the largest of three lanes, so even a correct 5% error rate per look adds up.
2. **Attacks are not independent.** The binomial model behind Wilson treats every attack as a separate lane choice. In practice the attacks in one possession nearly always go down the same side: in the simulator 91% of a possession's attacks share its lane, and an attacking possession has 2.2 attacks on average. Lane counts therefore vary much more than a binomial allows, and every interval is too narrow.

The second problem turned out to be the larger one.

## Measuring the clustering

`core/shares.attack_design_effect` measures how much more lane counts vary than independent attacks would. Over the league's matches it cuts each team's match into consecutive 15-minute windows and, for each lane with league share p, compares the squared deviation (k − np)² with the binomial variance np(1 − p):

deff = Σ (k − np)² / Σ np(1 − p), floored at 1.

- Synthetic league (50 matches, `data/league_stats.json`): **3.56**. Lane counts vary 3.6 times as much as independent attacks would, so 14 attacks carry about as much information as 4.
- StatsBomb Open Data, the 10 other 2018 World Cup matches used for mu: **4.35**. Real football clusters attacks by lane at least as strongly.

A first version counted attacks per possession (Σm²/Σm). It agreed on the simulator (4.5) but gave 13.9 on real data, where long possessions switch sides; the variance-based measure replaced it.

## The Beta-Binomial hedge (`core/hedge.py`)

- Prior: Beta with mean equal to the league's share for that lane (left 0.312, centre 0.356, right 0.332 in the synthetic league, not 1/3), worth `prior_strength` attacks.
- With `clustered`, the counts are first divided by deff: k/deff of n/deff.
- Posterior Beta(a + k, b + n − k). "clear" when P(share > league share + `min_effect`) ≥ `clear_prob`; "early" below 6 attacks; otherwise "suggests".
- The Beta CDF is computed in plain Python (regularised incomplete beta by continued fraction); a test checks it against the exact binomial-sum formula at 42 points.

## The study

- **Null:** 300 simulated matches with no planted overload (seeds 100000 to 100299). Each team still varies its lanes at random, so every "clear" call is a false alarm.
- **Planted (hero):** 300 matches running the hero script (seeds 200000 to 200299). After the home side's substitution (57'25") 75% of its possessions aim down the left, until 75'. In these matches the left share during the overload averaged 0.54 against a league 0.31, from about 22 attacks.
- **Planted (strong):** the same, but 90% of possessions aim left, from the substitution to full time.
- Detection: the first "clear" call for the home side's left lane between the substitution and the first look after the overload ends. Delay is from the substitution.
- Every raised overload is judged by every rule; the detector itself is unchanged. In null matches it raises 12.1 overloads per match, so most claims are worded by the hedge, not suppressed.

## Results

"False clear / match": mean false "clear" calls per null match. "Matches with one": share of null matches with at least one, with a 95% Wilson interval over the 300 matches. Delays in minutes (median).

| rule | prior | clear_prob | min_effect | clustered | false clear / match | matches with one | hero: detected | delay | strong: detected | delay |
|---|---|---|---|---|---|---|---|---|---|---|
| **Wilson vs 1/3 (before)** | – | – | – | no | 6.297 | 300/300 [0.987, 1.000] | 0.510 | 12.6 | 0.933 | 17.6 |
| beta | 10 | 0.95 | 0 | no | 4.730 | 299/300 [0.981, 0.999] | 0.500 | 12.6 | 0.927 | 17.6 |
| beta_effect | 10 | 0.95 | 0.10 | no | 1.020 | 197/300 [0.601, 0.708] | 0.197 | 17.6 | 0.653 | 22.6 |
| beta_bonferroni | 10 | 0.997 | 0 | no | 0.977 | 195/300 [0.594, 0.702] | 0.227 | 17.6 | 0.720 | 22.6 |
| beta_effect_bonferroni | 10 | 0.997 | 0.10 | no | 0.160 | 45/300 [0.114, 0.195] | 0.063 | 22.6 | 0.347 | 27.6 |
| **clustered (after)** | 10 | 0.95 | 0 | yes | 0.110 | 30/300 [0.071, 0.139] | 0.070 | 22.6 | 0.380 | 27.6 |
| clustered_effect | 10 | 0.95 | 0.10 | yes | 0.003 | 1/300 [0.001, 0.019] | 0.000 | – | 0.057 | 27.6 |
| clustered_bonferroni | 10 | 0.997 | 0 | yes | 0.000 | 0/300 [0.000, 0.013] | 0.000 | – | 0.040 | 27.6 |
| clustered_90 | 10 | 0.90 | 0 | yes | 0.393 | 98/300 [0.276, 0.382] | 0.150 | 17.6 | 0.607 | 25.1 |
| clustered_light_prior | 3 | 0.95 | 0 | yes | 0.563 | 129/300 [0.375, 0.487] | 0.167 | 17.6 | 0.600 | 22.6 |
| clustered_light_prior_bonferroni | 3 | 0.997 | 0 | yes | 0.020 | 6/300 [0.009, 0.043] | 0.013 | 22.6 | 0.143 | 27.6 |

clear_prob 0.997 is a Bonferroni split of 5% over the 17 looks (alpha spending in its simplest form).

## Reading it

- **The old rule was badly wrong.** Wilson against 1/3 called a non-existent overload "clear" 6.3 times per match, in every one of 300 matches. Its high detection rate means little at that false-alarm rate.
- **Fixing the baseline alone does little** (beta: 4.7 per match). Fixing the repeated looks alone (Bonferroni, or a 10-point minimum effect) still leaves about one false "clear" per match, because each look still over-counts the evidence about 3.6 times.
- **Accounting for the clustering is the main fix.** It cuts false "clear" calls from 6.3 to 0.11 per match (one in about nine matches) with the same 95% threshold.
- **The price is detection.** The hero overload (about +23 points from roughly 22 attacks, worth about 6 independent ones) is too weak to call "clear" in most matches under any rule that keeps false alarms down: the data cannot tell it from noise that quickly. The strong overload is called clear in 38% of matches, late (median 27.6 minutes after the substitution).
- Delays move in 5-minute steps (the detector looks every 5 minutes) and the detector does not raise the same lane again within 15 minutes of raising it, so an overload first worded "suggests" can only be upgraded 15 minutes later.

## Choice

The product uses **clustered** (`config.yaml` hedge: prior_strength 10, clear_prob 0.95, min_effect 0, clustered true). Reasoning: a missed "clear" costs little, because the overload still airs as "the numbers suggest…"; a false "clear" is a wrong plain statement on air. Among rules with false "clear" calls near or below 0.1 per match, it detects the most. The 0.1 level was chosen by hand while reading this table, not set in advance. Stricter rules (`clustered_effect`, `clustered_light_prior_bonferroni`) are one config change away if fewer false calls matter more than ever upgrading to "clear".

Wilson is still computed for every share claim and shown next to the Beta result: in the Analysis view's attack-lanes panel (bracket = Wilson, P = posterior P(share > league share)) and in each share fact's uncertainty record (`p_above`, `wilson_low`, `wilson_high`). Pass accuracy is not a share against a baseline and keeps its Wilson interval, worded "early" below 6 passes.

## Effect on the replay

On the published synthetic replay, all 12 lane-overload facts are now "suggests"; before, seven were "clear". (The injected small-sample claim is still "early".) The highest P(share > league share) among them is 0.95 to two places, for the planted left overload, just short of the threshold. This matches the study: the hero overload is too weak to call clear in most matches. Routing is unchanged: suggests and clear are both T1.

## Limits

- The study is synthetic: the simulator's lane choices and possession structure decide the result. The league shares and deff come from simulator matches and the study runs other matches of the same simulator, so the rule is chosen and measured on the same model of football.
- On real data the measured deff is 4.35, so the same rule would be at least as cautious there, but its false-alarm rate on real matches has not been measured (that needs many real matches with no overload, which open data cannot label).
- deff is one number for the league, every lane and every window; it does not vary with the game state or the team.
- The prior strength (10) and the 15-minute window are chosen by hand; only the listed combinations were tried.
- 300 matches per scenario; the intervals in the table show the Monte Carlo uncertainty for the share of null matches with a false call.
