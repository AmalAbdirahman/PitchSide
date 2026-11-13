# Win probability: method and validation

The control room shows a home / draw / away win probability and uses its change after a goal as a fact's impact (routing sends an impact of 0.10 or more to a producer). This note says how the model works, how well it is calibrated, and what a fitted variant changes.

Numbers come from one run of `python scripts/eval_winprob.py` on 2026-11-13 at commit 4fa455c; the full record, with every calibration bucket, is `data/winprob_eval.json`.

## The model in the product (poisson_v1, `core/winprob.py`)

Each side scores its remaining goals as an independent Poisson process. Its rate per minute blends the side's own xG so far with the league mean `mu`, worth `k` = 20 pseudo-minutes:

rate = (xG so far + k · mu) / (minutes played + k), expected further goals = rate · (90 − minutes played).

The win, draw and loss probabilities sum the outcomes up to 10 further goals per side. Minutes played is match time / 60. `k` and the 90-minute length were chosen by hand.

## How it was tested

- **Game states.** Every 5 official minutes from kick-off to the 85th minute (18 per match): the score and each side's xG before that minute. Second-half minutes count from the start of the second half, so first-half stoppage time does not shift them.
- **Outcome.** The result at the end of regulation (90 minutes plus stoppage). Extra time and shoot-outs are ignored, because that is what the model forecasts.
- **Data.**
  - *Synthetic:* 300 training and 300 test matches from the simulator (seeds 300000 to 300599). Its two sides are equally strong and a shot scores with probability equal to its xG.
  - *Real:* StatsBomb Open Data, men's World Cups 2018 and 2022 and Euros 2020 and 2024, 230 matches, split by match as for the xG fit (172 train, 58 test). Real shots carry StatsBomb's xG. `mu` is the training matches' xG per team-minute.
- **Scores**, on test matches only:
  - Brier score: the sum of squared errors over the three outcomes (0 is perfect; always saying one third each scores 0.667).
  - Log loss.
  - Calibration: mean predicted probability against the share of results, by home goal difference and phase of the match.
- **Uncertainty.** States within a match share one result, so the 95% intervals for Brier differences come from resampling whole matches (1000 times).
- **Comparison models.**
  - *constant:* the training matches' share of home wins, draws and away wins at every state (a pre-match constant).
  - *mu_only:* poisson_v1 with k = 100000, which all but ignores in-game xG: score and clock only.
  - *poisson_v2:* see below.

## Results (test matches)

| data | model | Brier | log loss |
|---|---|---|---|
| synthetic (300 test matches, 5400 states) | constant | 0.674 | 1.111 |
| | **poisson_v1** | 0.529 | 0.908 |
| | mu_only | 0.497 | 0.838 |
| | poisson_v2 | 0.498 | 0.839 |
| StatsBomb (58 test matches, 1044 states) | constant | 0.672 | 1.108 |
| | **poisson_v1** | 0.490 | 0.896 |
| | mu_only | 0.472 | 0.836 |
| | poisson_v2 | 0.463 | 0.795 |

Brier differences, 95% match bootstrap:

| data | v1 − constant | v2 − v1 | v2 − mu_only |
|---|---|---|---|
| synthetic | [−0.180, −0.109] | [−0.044, −0.019] | [−0.001, 0.001] |
| StatsBomb | [−0.272, −0.096] | [−0.049, −0.004] | [−0.030, 0.011] |

### Calibration of poisson_v1 (selected buckets)

Predicted mean against observed share, as (home win, draw, away win). The record has all 15 buckets per model and dataset.

| data | phase | home side | states (matches) | v1 predicted | observed | v2 predicted |
|---|---|---|---|---|---|---|
| synthetic | 0-30 | +1 | 244 (86) | 0.79 / 0.14 / 0.07 | 0.60 / 0.22 / 0.18 | 0.65 / 0.22 / 0.13 |
| synthetic | 30-60 | +1 | 457 (110) | 0.78 / 0.16 / 0.05 | 0.66 / 0.26 / 0.08 | 0.70 / 0.22 / 0.08 |
| synthetic | 60-90 | level | 594 (146) | 0.16 / 0.70 / 0.14 | 0.13 / 0.73 / 0.13 | 0.17 / 0.67 / 0.17 |
| StatsBomb | 30-60 | level | 140 (37) | 0.25 / 0.46 / 0.29 | 0.25 / 0.32 / 0.43 | 0.30 / 0.38 / 0.32 |
| StatsBomb | 60-90 | level | 101 (22) | 0.13 / 0.75 / 0.12 | 0.24 / 0.37 / 0.40 | 0.19 / 0.62 / 0.19 |
| StatsBomb | 60-90 | +1 | 65 (15) | 0.89 / 0.10 / 0.01 | 0.86 / 0.14 / 0.00 | 0.82 / 0.15 / 0.02 |

## Reading it

- **poisson_v1 is far better than a constant** on both datasets: it knows the score and the clock.
- **It is overconfident.** A one-goal lead in the first half is called a home win about 79% of the time in the simulator; such leads were held in 60%. On real data, level games late on are called draws 75% of the time; 37% of them ended level.
- **Two causes showed up.**
  1. *In-game xG is weighted too heavily.* With k = 20 pseudo-minutes, a side's early xG soon outweighs the league rate. Early xG is noisy, and a side that has scored usually has its goal's xG on the board too, so the leader's rate is inflated. Dropping in-game xG altogether (mu_only) beats v1 on both datasets. In the simulator that is expected, since its sides are equally strong; on real data it says the shrinkage is too weak.
  2. *Real matches run longer than 90 minutes.* Regulation lasted 98.2 minutes on average in the training matches, so v1 leaves too little time for the score to change, and too many level games become "draws".

## poisson_v2 (validation only)

Same model, three changes, fitted on the training matches of each dataset:

- `length`: the mean length of regulation in the training matches (synthetic 90, StatsBomb 98.2 minutes);
- `k`: picked from a grid by training Brier score (synthetic 100000, i.e. ignore in-game xG; StatsBomb 80);
- `rho`: a Dixon-Coles style factor on the remaining goals (Dixon and Coles 1997), which moves probability between 0-0 / 1-1 and the one-goal results, picked from a grid (synthetic 0.05, StatsBomb 0.10; positive values mean fewer draws).

It improves on v1 on both test sets; the intervals for v2 − v1 exclude zero. On real data it also beats score-and-clock-only (mu_only) on log loss, but the Brier interval against mu_only includes zero, so the evidence that real in-game xG adds to the score and clock is weak at this sample size. The grids are in `config.yaml` (`winprob_eval`); they were chosen by hand and only the listed values were tried.

## What the product uses

The site still uses **poisson_v1**: this task validated it and did not change it. Switching to v2 would change every impact value on the replay and, through the 0.10 impact threshold, possibly which facts go to a producer; that is a separate, visible change (see the README roadmap). The synthetic fit says the best k for the simulator is "ignore in-game xG", which is a property of the simulator (equal sides), not of football.

## Limits

- 58 real test matches: real calibration buckets hold a few dozen matches at most and are noisy; the bootstrap intervals are wide.
- Tournament football only, mostly at neutral venues; "home" is StatsBomb's listing, so there is little home advantage to model.
- States in one match are not independent; only the intervals account for that.
- xG for real shots is StatsBomb's; the model has no team strength beyond xG so far, no red cards and no game-state effects on scoring rates other than through the score.
