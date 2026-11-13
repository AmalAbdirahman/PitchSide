# The Reader: checking what a sentence means

The verifier proves that every number in a sentence is right. It could not tell whether the sentence *means* the claim: "69% of centre attacks were Northbridge's" and "69% of Northbridge attacks went through the centre" have the same number and different meanings. P12 closes part of that gap with a round trip.

## How it works

1. **The Reader** (`app/reader.py`) is a second prompt agent on Microsoft Foundry, with its own instructions. It receives **only** the sentence and the roster (team codes, names, player ids and names). It does not see the Scout's claim, the moment, the numbers or the event log. It replies, under a strict JSON Schema generated from `core/facts.Reading`, with the claim it understood: subject, metric (or null if no listed metric says what the sentence says), window as the sentence states it (since minute N, at minute N, first half, second half, or unstated) and value.
2. **Code compares** (`app/meaning.py`) the reading with the Scout's structured claim: same metric, same subject, a window consistent with the claim's (using the official match clock; an unstated window passes, since a vaguer sentence is not a wrong one) and a value within the metric's verifier tolerance. A difference fails the new verifier check **`meaning_matches`**, the draft is rejected, and the Scout gets its one existing retry, with the difference as feedback.
3. **The audit trail** records a `Reader` step with the reading between the Scout's draft and the verifier's verdict. The Audit view shows it in the agent trace (hover for the reading) and in the **MM** column.

The Reader reads only sentences the Scout wrote. Goals, substitutions and cards air in fixed template wording written by code, and rule-based drafts are templates too, so they are not read and carry no `meaning_matches` check. Like the Scout, the Reader runs only at build time; its requests and replies are cached in `data/reader_cache.json` and replayed with no network.

## Labelled test set

`tests/meaning_cases.json`: 75 sentences about claims from the synthetic match, labelled by hand.

- **41 "same"**: the 34 sentences the site aired in the P11 run, and 7 paraphrases ("tried his luck 4 times" for 4 shots, "after the break" for the second half).
- **34 "different"**: every number in the sentence is the claim's, but the meaning is not: the lane's share instead of the team's, the wrong lane, team, half or third of the pitch, the complement ("misplaced 67%"), the opposite ("least central"), another statistic ("ended in a shot", "on target", "from set pieces"), xG for xT, conceded for created.

One labeller (the author, working with Claude) wrote and labelled all of them; no second person reviewed the labels. They are written about one synthetic match, so they test the kinds of mistake listed above, not the mistakes a model makes most often.

## Results

From `python scripts/eval_meaning.py --foundry` on 2026-11-13 at commit 11d4358, gpt-5-mini through the Foundry agent path (record `data/meaning_eval.json`). A flag is a failed `meaning_matches`; positive means "different".

| | labelled different | labelled same |
|---|---|---|
| flagged | 27 | 4 |
| not flagged | 7 | 37 |

- **Precision 0.871** (27 of 31 flags were real meaning errors).
- **Recall 0.794** (27 of 34 meaning errors were flagged).
- Every sentence was read; none came back invalid.

This is the first and only run. The Reader's instructions were not tuned on these results, since tuning them on the same 75 sentences would inflate both numbers.

**False flags (4).** Two aired top-shot-speed sentences name the shooter, and the Reader made the player the subject: for "Y. Castell produced the match's fastest shot" this goes against its instructions, which say "the match's fastest" is about the match; "D. Varga hit a 109 km/h shot in the second half" never says the shot was the fastest, so that label is arguable. Two paraphrases were read as no listed metric ("tried his luck 4 times"; "no Harlow player linked more of the passing").

**Misses (7).** The Reader mapped a sentence to the nearest metric and dropped the qualifier: "on target", "from set pieces" and "broken down on the left" read as plain shots, xG and left-lane share; "conceded 0.27 xG" and "allowed 2.11 xG" kept Northbridge as the subject; and two most-central-player sentences with the wrong scope (the match, or a player put in the other team) read as the right claim, because the reading names the player, not the team.

## In the site's build

`python -m app.build_bundle --foundry` on 2026-11-13 (handed mode, gpt-5-mini). Of 38 moments, the Reader read 36 sentences: 34 final drafts, and 2 first drafts that the meaning check rejected. Those two were both the false flag above (the shooter read as the subject of a top-shot-speed claim). The Scout's retries ("The match's fastest shot was 109 km/h.") were read back correctly and verified. No fact was lost: 31 passed first time, 7 after a retry, none rejected.

Because those two facts were rejected on the first pass, the memory sent with later requests changed, so their requests were new and the Scout answered them live again. Five of those new replies broke the handed-mode number-key rule and were retried (all passed). That is why the site now shows 31 first-pass drafts against 36 in the P11 run; the Reader caused 2 of the 7 retries. The P11 comparison of the two modes (`docs/methods/scout.md`, `scripts/eval_scout.py`) runs without the Reader and is unchanged.

## Limits

- The check is only as good as the Reader's reading, which comes from one model at one point in time. A misread can block a correct sentence (it costs a retry) or pass a wrong one.
- It compares five fields. A sentence can match all five and still mislead (tone, emphasis, an implied cause).
- The labelled set is small, synthetic and single-labelled; precision and recall here are counts on 75 sentences, not estimates with small error.
- The site's aired sentences overlap with the labelled set's "same" cases (the set was built from the P11 run's aired wording), so the build reused 27 of its 36 readings from the eval run's cache.
