# Expected goals (xG): method and validation

Data: StatsBomb Open Data (statsbomb/open-data on GitHub), used under the StatsBomb Public Data User Agreement. Numbers below come from one run of `python scripts/fit_xg.py` on 2026-11-13 at commit 1a949f7; the full record is `data/xg_fit.json`.

## What the model is

A logistic regression on seven features of a shot, with penalties handled apart (`core/xg.py`):

| feature | meaning |
|---|---|
| intercept | constant |
| dist | distance from the shot to the centre of the goal, metres |
| angle | angle the goal mouth subtends at the shot, radians |
| header | StatsBomb body part "Head" |
| other_body | any body part other than a foot or the head |
| free_kick | StatsBomb shot type "Free Kick" (direct free kicks) |
| under_pressure | StatsBomb's `under_pressure` flag on the shot |

xG = 1 / (1 + exp(-(sum of coefficient x feature))). A penalty gets the penalty conversion rate of the training matches instead. Shot types "Corner" and "Kick Off" are treated as open play. Coordinates are rescaled from StatsBomb's 120 x 80 pitch to 105 x 68 metres (`data_sources.md`).

The same model and coefficients drive the simulator and the StatsBomb adapter. The simulator only knows where a shot was taken, so its shots are scored as open-play shots with the foot, not under pressure.

## Data and split

- Seasons: men's FIFA World Cup 2018 and 2022, UEFA Euro 2020 and 2024 (230 matches). Chosen by hand: recent, complete tournaments tagged the same way.
- Split by match, not by shot: 58 of 230 matches (25%, seed 7) are held out for testing, so no match has shots on both sides.
- Train: 4264 shots (4203 open play, 61 penalties), 425 goals. Test: 1423 shots, 139 goals.
- The penalty shoot-out is not open play and is excluded.
- Fit: maximum likelihood by Newton-Raphson in plain Python, converged in 8 iterations, with an L2 penalty of 1e-6 that only keeps the steps well posed. Standard errors come from the inverse Hessian.

## Coefficients

| feature | coefficient | standard error |
|---|---|---|
| intercept | -1.6029 | 0.3487 |
| dist | -0.0959 | 0.0161 |
| angle | 1.7831 | 0.2691 |
| header | -1.22 | 0.1719 |
| other_body | -1.7899 | 0.6133 |
| free_kick | 0.3857 | 0.412 |
| under_pressure | -0.5047 | 0.1558 |

Penalty conversion rate: 0.6557 (61 penalties).

Every sign is the expected one: xG falls with distance, rises with a wider view of the goal, and is lower for headers, other body parts and shots under pressure. The free-kick coefficient is smaller than its standard error, so the data cannot tell a direct free kick from an open-play shot from the same spot.

## Validation on the held-out matches

Three predictions for the same 1423 test shots: ours; StatsBomb's own xG (`statsbomb_xg`, present on every test shot); and a constant, the training goal rate per shot (0.0997). Lower is better for both scores.

| model | log loss | Brier |
|---|---|---|
| ours | 0.2655 | 0.0755 |
| StatsBomb xG | 0.2442 | 0.0689 |
| constant | 0.3200 | 0.0881 |

Reliability: test shots sorted by predicted xG and cut into ten bins of equal size; a calibrated model has the observed goal rate close to the mean prediction in every bin.

| bin | shots | ours: mean predicted | ours: observed | StatsBomb: mean predicted | StatsBomb: observed |
|---|---|---|---|---|---|
| 1 | 142 | 0.016 | 0.014 | 0.009 | 0.021 |
| 2 | 142 | 0.027 | 0.014 | 0.020 | 0.014 |
| 3 | 142 | 0.036 | 0.028 | 0.030 | 0.021 |
| 4 | 143 | 0.045 | 0.042 | 0.040 | 0.035 |
| 5 | 142 | 0.056 | 0.042 | 0.052 | 0.042 |
| 6 | 142 | 0.069 | 0.063 | 0.066 | 0.014 |
| 7 | 143 | 0.087 | 0.091 | 0.084 | 0.063 |
| 8 | 142 | 0.113 | 0.127 | 0.115 | 0.085 |
| 9 | 142 | 0.164 | 0.197 | 0.177 | 0.261 |
| 10 | 143 | 0.405 | 0.357 | 0.440 | 0.420 |

Reading it: our model beats the constant clearly and is behind StatsBomb's model on both scores, as expected for a model that does not see keeper or defender positions, shot height or the pass before the shot. In every bin its mean prediction is within about 1.2 standard errors of the observed rate. With about 14 goals per bin, an observed rate of 0.1 has a standard error of roughly 0.025, so bin-to-bin wobble of that size (in either model) is noise, not miscalibration.

## Limits

- Men's international tournaments only. Club football, women's football and other levels may convert chances differently; the model has not been tested there.
- One random split. A different seed would give somewhat different scores; no cross-validation or bootstrap intervals yet.
- 139 test goals: enough to rank the three models, not to resolve calibration finely in any one bin.
- No keeper position, defenders, shot height, assist type or game state.
- Penalties are one rate from 61 penalties, with no taker or keeper effect.
- The replayed StatsBomb match (2018 final, match 8658) is in the training set, so xG on that replay is in-sample.
- xG is a model estimate. The verifier checks that an xG claim adds up the event log's xG values correctly; it does not and cannot check that the model is right.
