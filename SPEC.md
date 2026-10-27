# SPEC.md: Match control room, GitHub-only build

Hackathon build. All data is synthetic; clubs and players are fictional. Thresholds are tunable defaults, not validated values; keep them in `config.yaml`.

## 1. What it is

A producer-first control room for a replayed synthetic match. Python builds a replay bundle offline. A static web page (GitHub Pages, no server) replays it. Proposed facts are verified by deterministic code, routed by risk tier, approved by a human (or auto-aired), and shown as three renditions: on-screen cue, EN/ES caption, audio script.

No cloud services and no network access at run time. The one model call is the build-time Scout agent on Microsoft Foundry (section 19), run only with `--foundry`; its answers are cached so every other build is offline.

Exception: `scripts/foundry_smoke.py` is a manual check, run by hand on the developer's machine, that a Microsoft Foundry project and model deployment respond. It reads its endpoint and model from a local `.env`. It is not part of the build, the bundle, the eval, the tests or CI, and nothing in the product depends on it.

## 2. Honesty rules (write these into the README)

1. The data is synthetic and the match is a replay, not a live feed.
2. Drafts for each moment come from the Scout, an LLM agent on Microsoft Foundry, at build time only. The deterministic verifier decides what can air. A separate rule-based drafter deliberately includes some wrong numbers, players, windows and intent words; the verifier's job is to catch them. Say this plainly: the injected errors are test cases, not claims about how any model behaves. Report Scout pass, retry and reject counts only from the cache of a real run.
3. Spanish text comes from templates plus a small glossary. It has not been reviewed by a native speaker.
4. Win probability is a simple model with stated assumptions, not a prediction of real matches.
5. Report eval numbers only from an actual run of `scripts/eval.py`.
6. Do not claim any Microsoft or Azure technology unless it really ran. Claim Microsoft Foundry only if the committed `data/agent_cache.json` came from a real `--foundry` run. If the optional build-time Translator add-on (section 18) was run, say exactly that: translations were generated offline at build time.

## 3. Pipeline

```
sim.generate -> events.json
core (xG, shares, win prob, Wilson) + moment rules -> moments
Scout agent (Foundry, build time; cached) -> draft facts, one per moment; rule-based drafter as fallback
drafter -> injected drafts (deliberately wrong)
verifier -> verified / corrected / rejected (a rejected Scout claim gets one retry)
router -> T0 | T1 | T2
renditions -> cue, caption EN, caption ES, audio script
build_bundle -> docs/data/hero.json and docs/data/cues.json
docs/ (static page) replays the bundle
```

## 4. Event contract (minimum)

| field | notes |
|---|---|
| `id` | int, monotonic, the evidence key |
| `t` | match clock seconds |
| `type` | pass, shot, tackle, possession_change, pressure, carry, card, substitution, goal |
| `team`, `player_id` | fictional ids |
| `x`, `y` | metres on a 105 x 68 pitch, normalised so each team attacks left to right |
| `outcome` | success, fail, on_target, off_target, blocked, goal |
| `xg` | shots only |
| `pass_distance`, `pass_difficulty`, `ball_speed`, `shot_speed` | where relevant |

The simulator is seeded. It must plant one story in the hero match: a substitution at 58', then a left-flank overload, a goal at 63', and a late run of Harlow losses in their own third. It also writes `data_card.md` listing the realism checks run and their results.

## 5. Fact contract

See `fact.schema.json`. Every fact has a structured claim (`subject, metric, window, comparator, value`) so code can recompute it, canonical text with a numbers list, evidence event ids, and a verification record. Rejected drafts use the same schema with status `rejected` and appear in the log.

## 6. Drafter

The rule-based drafter is the fallback when the Scout is unavailable and the source of the injected drafts. `draft_facts(moments)` returns drafts in sloppy natural wording. It includes at least these injected errors: a wrong number, a wrong player, a wrong window, a future event, an intent word ("deliberately"), and a small-sample overclaim. Each injected draft is tagged `injected: true` in the bundle so the eval can compare results.

## 7. Verifier (deterministic)

A fact is `verified` only if all checks pass.

| check | rule |
|---|---|
| `recompute` | Recompute the metric from events in the window. Counts must match exactly; shares within 0.5 percentage points. |
| `evidence_exists` | Every cited event exists, lies in the window, and has `t` at or before the fact's `created_t`. |
| `entities` | Team and player appear in the cited events. |
| `numbers_match` | Every number in the text appears in `text.numbers` and in the recomputed result. |
| `intent_stripped` | Remove intent words (deliberately, tried to, wanted to); log a correction. Status becomes `corrected`. |
| `sample_size` | If `n` is below 6, downgrade to hedge `early`, or mark `unverifiable` if it cannot be stated as a plain count. |

## 8. Calibrated language

Compute a Wilson interval (z = 1.96) for share claims, against the claim's baseline (default 1/3 for left/centre/right). Interval excludes the baseline: hedge `clear`. Interval overlaps: `suggests`. n below 6: `early`. A plain event-log fact: `none`. Text must not sound stronger than its hedge ("early sign", "suggests", plain).

## 9. Win probability (`poisson_v1`)

State: minutes played, goals and xG for each side. Rate per side per minute: `(xg + k*mu) / (minutes_played + k)`, with `mu` the simulated league mean and `k = 20`. Remaining goals are Poisson per side; sum outcomes up to a cap of 10. `impact.delta` is the change in the favoured side's win probability caused by the moment. Label it "model estimate" everywhere it appears.

## 10. Risk tiers

| tier | covers | behaviour |
|---|---|---|
| **T0** | Plain facts from the event log: goal, substitution, card | Air at once |
| **T1** | Verified, no corrections, absolute impact under 0.10, hedge `suggests` or `clear` | Air after a 3 second veto window unless vetoed |
| **T2** | Corrected by the verifier; absolute impact 0.10 or more; names a player negatively; hedge `early` | Queue for approve or reject, 12 second expiry, then dropped and never aired |

A rejected draft never reaches the router. Autopilot (a UI toggle used when recording the video) approves T2 facts only if verification was clean and hedge is `clear`.

## 11. Renditions

From each approved fact:
1. `cue`: lower-third text with start time and duration.
2. `caption` EN and ES: ES is built from templates and a glossary (`templates_es.py`).
3. `audio_script`: speakable text with numbers spelled out.

A deterministic check confirms every number in each rendition appears in `text.numbers`; a failed check sets `numbers_preserved` to false and the fact is not shown.

## 12. Bundle format

`docs/data/hero.json`: `{meta, events, score_timeline, facts[], log[], metrics, series}`.
- `events`: at least `id, t, type, team, player`; may also carry the simulator fields the UI draws (`x, y, end_x, end_y, xg`, speeds, `period`, `outcome`). Coordinates are per team, attacking left to right.
- `series`: per-minute values for the charts, all computed in Python by `core/` (win probability, team stats, attack lanes with Wilson interval and hedge). The front end draws them and computes no statistics of its own.
`docs/data/cues.json`: an array of timed cues `{cue_id, fact_id, start_t, duration_s, layout, lang, text, evidence_ids, checked, tier}`, the machine-readable overlay feed. Provide a download link for it in the UI.

## 13. Static UI (`docs/`, no build step)

- Replay clock: play, pause, restart, speed.
- Producer queue with Approve, Reject, Veto, expiry bars; on-air preview with lower-third and score bug; decision log; metric counters.
- Controls: language EN/ES; depth Casual/Analyst (Analyst shows evidence ids, hedge, impact, verifier corrections); Follow player (highlights facts naming that player and filters to them); Autopilot.
- Evidence drawer per fact listing the cited events.
- Accessible: labelled controls, contrast, keyboard operable, `prefers-reduced-motion`.
- Base the look on `docs/prototype.html`.

## 14. Evaluation

`scripts/eval.py` runs the verifier over `tests/seeded_claims.json` (at least 5 per category: wrong number, wrong player, wrong window, future event, intent word, small sample, plus a set of good claims). It prints catch rate and false-reject rate. Copy those exact numbers into the README.

## 15. Acceptance tests (write first)

`test_schema_validates_every_fact`, `test_verifier_rejects_wrong_number`, `test_verifier_rejects_future_event`, `test_intent_words_stripped_and_logged`, `test_small_sample_downgrades_hedge`, `test_number_preservation_en_es`, `test_tier_routing_table`, `test_win_probability_late_goal_moves_more_than_early_goal`.

## 16. Cut order if time runs short

Follow player, then depth toggle, then ES captions, then T1 veto window, then audio script. Never cut: the verifier tests, the bundle, the static page, the README honesty section.

## 18. Optional build-time add-on: Azure AI Translator

Only after the GitHub-only version is complete and submittable.

- `app/translate_azure.py` runs only inside `python -m app.build_bundle --azure`, on the developer's machine. It reads `AZURE_TRANSLATOR_KEY`, `AZURE_TRANSLATOR_REGION` and the endpoint from environment variables or a local `.env`.
- It translates each fact's `canonical_en` into Spanish with a 10 second timeout. A failed call falls back to the template Spanish for that fact.
- Results are cached in `data/translations_cache.json` and written into `hero.json`. The static site never calls Azure and never contains a key.
- The number-preservation check applies to the translated text exactly as it does to template text. A failure falls back to the template.
- Record `translation_source` (`template` or `azure`) per rendition, and show it in Analyst depth.
- Claim Azure AI Translator in the README and pitch only if the bundle in the repo was actually built with `--azure`.
- Do not put any key in `docs/` or in git.

## 17. Publish

Commit `docs/` and enable GitHub Pages (Settings, Pages, deploy from branch `main`, folder `/docs`). Optional: a GitHub Actions workflow that runs `pytest -q` on push.

## 19. Build-time Scout agent on Microsoft Foundry

- `app/agents_foundry.py` runs only inside `python -m app.build_bundle --foundry`, on the developer's machine. It reads `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL` from a local `.env` and creates a prompt agent (`agents.create_version` with `PromptAgentDefinition`), called through `get_openai_client(agent_name=...)`. If the agent API fails, it falls back to a plain `responses.create(model=...)` with the same instructions. Each call records which path ran.
- Input: one moment summary computed by `core/` (subject, metric, window, minutes, numbers, evidence ids, names, hedge). Output: strict JSON with `claim`, `text.canonical_en`, `text.numbers` and `evidence_ids`. Code, not the agent, sets ids, times, the evidence window and impact, and rejects output whose number keys differ from the input.
- Every Scout claim goes through the verifier (section 7). A rejected or unverifiable claim goes back once with the failed checks; if it still fails it stays rejected and is logged.
- Limits (in `config.yaml`): 45 s per call, at most 40 calls per build. Any call failure falls back to the rule-based drafter for that moment; the build always completes.
- Every request and response, including failures, is cached in `data/agent_cache.json` keyed by a hash of the instructions and input. The cache is committed. Without `--foundry` the build replays it with no network and reproduces the same bundle. A `--foundry` run reuses successful answers to identical requests and repeats only the rest.
- Each fact carries `audit.steps` (agent, path, model, ms, attempt, outcome). The Audit view shows the trace; Analyst rows show who drafted the fact.
- T0 metrics (goal, substitution, card) keep the Scout's claim and evidence but use the template wording for `canonical_en` and `text.numbers`, before verification; the Scout's wording stays in `draft_en`. Agent wording is used only for T1/T2 facts.
- Renditions still come from templates (section 11). There is no Editor agent.
