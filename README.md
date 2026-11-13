# PitchSide

[![tests](https://github.com/AmalAbdirahman/PitchSide/actions/workflows/test.yml/badge.svg)](https://github.com/AmalAbdirahman/PitchSide/actions/workflows/test.yml)

A producer-first control room for a replayed, synthetic football match. Python builds a replay bundle offline; a static web page in `docs/` replays it. An LLM agent drafts claims at build time, a second agent reads each sentence back, deterministic code verifies every one, risk tiers decide what airs on its own and what a human must approve, and each approved fact becomes a lower-third cue, English and Spanish captions, and an audio script.

## Honesty first

- **Synthetic data on the site.** The published replay's clubs (Northbridge, Harlow), players and events come from a seeded simulator. No real match data, names or logos are on the site.
- **Real open data, locally only.** The pipeline also runs on a real match from StatsBomb Open Data (rule-based drafter, no agent). Raw files and the built bundle stay in git-ignored folders and are not published, because StatsBomb's user agreement does not allow redistributing the data. See `data_sources.md` for the licence, every mapping decision and the dropped event types.
- **Replay, not live.** The page replays a pre-built bundle. It has no server and makes no network calls.
- **The model runs at build time only.** Drafts for each moment come from the Scout, a prompt agent on Microsoft Foundry, called by `python -m app.build_bundle --foundry`. Its answers are cached in `data/agent_cache.json`, so the normal build and the site never call a model.
- **The Scout uses structured output, and tools in explore mode.** Its reply must follow a JSON Schema generated from Pydantic models. In explore mode it gets no numbers, only the match state and a flagged moment, and finds numbers and evidence with three read-only tools backed by the metric registry (`list_metrics`, `compute_metric`, `list_events`), which cannot see past the moment. Measured in a live run on 2026-11-13: handed mode 36/38 claims passed first time and none was rejected; explore mode 24/38 first time and 7/38 still rejected after a retry, all for wrongly written numbers that the verifier caught (`docs/methods/scout.md`). The site uses handed mode.
- **A two-agent pipeline, with code as the judge.** A second agent, the Reader, sees only each Scout sentence and the roster and writes down the claim it understood; code compares that with the Scout's claim, and a mismatch fails the verifier's `meaning_matches` check and sends the Scout back once. On 75 hand-labelled sentences (34 with the right numbers but the wrong meaning) the check flagged 27 of the 34 (recall 0.79) and 4 of the 41 correct ones (precision 0.87), in one live run on 2026-11-13 (`docs/methods/meaning.md`). It misses qualifiers such as "on target" or "conceded". Neither agent decides what airs.
- **Template wording for T0.** Goals, substitutions and cards air at once, so they use fixed templates; agent wording is used only for facts that pass through a veto window or a producer (T1/T2).
- **Code decides truth.** A deterministic verifier recomputes every claim from the event log before it can air. The Scout's words are not trusted.
- **Injected errors are test cases.** Six drafts with planted errors (wrong number, player, window, a future event, an intent word, a small-sample overclaim) come from a rule-based drafter, not from the model. They exist so the verifier has something to catch; they say nothing about how any model behaves.
- **Spanish is unreviewed.** It comes from templates and a small glossary and has not been checked by a native speaker.
- **xG is a fitted model estimate.** A logistic regression fitted on StatsBomb Open Data shots from four men's international tournaments and validated on held-out matches: log loss 0.2655 against 0.2442 for StatsBomb's own xG and 0.3200 for a constant (`docs/methods/xg.md`). The simulator uses it. Real StatsBomb shots carry StatsBomb's own xG by default, since it scored better (`statsbomb.shot_xg` in `config.yaml`).
- **xT is a fitted model estimate.** Expected threat is a Markov model on a 16 x 12 grid, fitted on the same StatsBomb Open Data matches and validated on the held-out ones (log loss 0.0765 against 0.0794 for a constant; it runs low in the lower bins, `docs/methods/xt.md`). It ignores defenders and everything off the ball. The site uses it for the `xt_added` metric (net xT gained by completed passes and carries), one half-time moment, and a threat map in the Analysis view.
- **Lane-share hedges come from a Beta-Binomial model, tuned on synthetic matches.** "Clear" needs P(share > league share) of at least 95%, after allowing for attacks in one possession sharing a lane. In a study of 300 simulated matches with no planted overload, the old rule (Wilson interval against 1/3) called a non-existent overload "clear" 6.3 times per match; the new one 0.11. The price: the replay's planted overload now airs as "the numbers suggest…", not as a plain statement (`docs/methods/hedging.md`). Its false-alarm rate on real matches is not measured.
- **Pass networks use inferred receivers.** Neither source records who received a pass, so the receiver is taken to be the next player of the same team on the ball. The Analysis view draws each team's network for the current half (degree, betweenness, a modularity split), and a half-time fact names the most central player (highest betweenness), which the verifier recomputes. In the simulator, who gets the ball next is drawn by position, so the synthetic networks reflect the simulator, not real passing (`docs/methods/networks.md`).
- **Win probability is a simple Poisson model** with stated assumptions, labelled "model estimate", not a prediction about real football. Validated on 300 simulated and 58 held-out StatsBomb Open Data matches: far better than a pre-match constant (Brier 0.490 against 0.672 on real matches) but overconfident, because it weights early xG too heavily and assumes 90 minutes when real regulation averaged 98. A fitted variant (poisson_v2) does better (0.463) and is documented, but the site still uses the original (`docs/methods/winprob.md`).

## Architecture

```
 events.json ──► core/ (xG, shares, Wilson, win prob) ──► moments
                                                            │  one JSON summary per moment
                                                            ▼
                      ┌──────────── Scout (Microsoft Foundry prompt agent, build time) ◄──┐
                      │  claim + English wording + cited event ids                          │ failed checks,
                      ▼                                                                     │ once
               Reader (second Foundry agent: sentence + roster only ──► the claim it read)   │
                      ▼                                                                     │
               Verifier (deterministic Python, incl. meaning_matches) ── rejected? ─────────┘
                      │  verified / corrected            still rejected ──► log (never airs)
                      ▼
               Router  T0 air now · T1 air after 3 s veto · T2 producer approves or it expires
                      ▼
               Renditions (templates, number check)  cue · EN/ES caption · audio script
                      ▼
               docs/data/hero.json + cues.json ──► static page ──► Producer (approve / reject / veto)

 Call failure or timeout ──► rule-based drafter for that moment (the build always completes)
 Every Scout request/response ──► data/agent_cache.json, every Reader one ──► data/reader_cache.json
 (both committed; offline builds replay them)
```

## Run it

```
python -m pip install -e ".[dev]"                  # pydantic, pyyaml, jsonschema plus pytest, ruff, mypy, pre-commit
python -m http.server 8000 --directory docs        # then open http://localhost:8000
pytest -q                                          # no network needed
ruff check . && ruff format --check . && mypy core app
node --test "tests/js/*.test.mjs"                 # routing cases against docs/js/routing.js (Node 22, no npm)
python scripts/eval.py                             # verifier eval + Scout numbers from the cache
python scripts/eval_meaning.py                     # meaning check: precision and recall, replayed
python -m app.build_bundle                         # offline rebuild, replays data/agent_cache.json
```

Dependency groups live in `pyproject.toml`: `dev` (tooling), `data` (open-data analysis; matplotlib draws the xT figures), `agents` (the Foundry client) and `e2e` (Playwright). `pre-commit install` runs ruff and mypy before each commit.

Browser smoke test (serves `docs/` locally, plays to full time at 4x, vetoes one fact, approves one, lets one expire, and fails on any console error):

```
python -m pip install -e ".[dev,e2e]"
python -m playwright install chromium
pytest -m e2e
```

Rebuilding with the agent (needs a Foundry project and a local `.env` with `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL`):

```
python -m pip install -e ".[agents]"
python -m app.build_bundle --foundry
```

Running the pipeline on a real match (StatsBomb Open Data; downloads into `data/external/`, which is git-ignored):

```
python scripts/fetch_statsbomb.py                  # 2018 World Cup final plus 10 matches for the league xG rate
python -m app.build_bundle --source statsbomb      # writes data/external/bundles/8658/, never docs/
```

Limits: 45 s per call, at most 40 calls per build (`config.yaml`). A `--foundry` run reuses cached successful answers to identical requests and only calls for the rest.

## Microsoft technologies used

- **Microsoft Foundry (Agent Service), `gpt-5-mini` deployment, at build time.** The Scout is created with `agents.create_version` (`PromptAgentDefinition`) and called through the project's OpenAI-compatible client. In the committed cache, all 33 calls went through the agent path (`foundry_agent`); none needed the plain-responses fallback.
- **GitHub Pages** is the hosting target: the site is plain static files in `docs/`.
- **GitHub Actions** runs `ruff`, `mypy core app` and `pytest -q` on Python 3.12 for every push and pull request (`.github/workflows/test.yml`). It installs only the runtime dependencies and the `dev` group, so the checks prove the build needs no Azure packages or network. Two more jobs run the routing cases under `node --test` and the Playwright browser smoke test.

## Results (from real runs)

`python scripts/eval.py`, run on 2026-11-13 (after P10 added pass networks):

| Verifier over `tests/seeded_claims.json` | |
|---|---|
| Catch rate (injected claims handled as SPEC section 7 requires) | 42/42 = 100.0% |
| False-reject rate (good claims) | 0/38 = 0.0% |

The seeded claims are generated by our own rule-based drafter, so a perfect score shows the verifier does what it was built to do on these cases, not that it catches every possible error.

Scout agent in handed mode (the site's), replayed from `data/agent_cache.json`; explore mode and duplicates are in `docs/methods/scout.md`:

| | |
|---|---|
| Moments | 38 |
| Passed the verifier first time | 31 |
| Passed after one retry | 7 |
| Still rejected | 0 |
| Fell back to rules | 0 |
| Scout calls / latency (the cached `--foundry` run of 2026-11-13 with the Reader, gpt-5-mini) | 45 calls, median 10.4 s, max 22.0 s |
| Sentences the Reader read back | 36 |

Of the 7 retries, 2 came from `meaning_matches`: in both, the Reader took the named shooter as the subject of a "fastest shot" claim about the match (a false flag), and the reworded retry passed. The other 5 were replies that broke the number-key rule. Before the Reader (P11 run), the same build had 36 first-pass drafts and 2 retries; `docs/methods/meaning.md` explains the difference.

### What the verifier does not catch

The verifier checks numbers, windows, evidence, entities, intent words and hedging, and, through the Reader, five fields of what a sentence means (metric, subject, window, value, and whether any listed metric fits). Player names are matched against the roster as whole words, so a surname on its own counts, and a misspelt or wrongly initialled name is rejected rather than aired. In the first `--foundry` run, the Scout wrote lines like "Northbridge had 69% of centre attacks" when the data meant 69% of Northbridge's attacks went through the centre. Every number was right, so they passed. Tighter instructions fixed this, and the Reader's `meaning_matches` check now catches that kind of error (all three such sentences in the labelled set). It still misses a qualifier the metrics do not measure ("3 shots on target", "0.86 xG from set pieces") and a flipped role ("Northbridge conceded 0.27 xG"), and it does not judge tone or implied causes. Goals, substitutions and cards (T0, which air with no human check) therefore use fixed template wording, such as "GOAL, Northbridge 1–0 Harlow. C. Nkemelu, 63'."; the Scout still drafts and cites the claim, and its own wording is kept in the log.

## Layout

```
sim/      seeded match simulator
core/     typed models (events.py, facts.py), config, metric registry (metrics.py), event sources
          (sources.py, statsbomb.py), fitted xG (xg.py, xg_fit.py), xT (xt.py),
          attack shares, Beta-Binomial hedge (hedge.py), Wilson intervals, win probability
          and its validation (winprob.py, winprob_eval.py), pass networks (network.py),
          moment rules
app/      agents_foundry.py (Scout loop) and scout_*.py, reader.py (Reader), meaning.py (meaning
          check), drafter.py (fallback + injected errors), verifier.py
          (orchestrates checks.py and corrections.py), router.py,
          renditions.py, templates_es.py, build_bundle.py
docs/     static control room (index.html, styles.css, js/ ES modules), data/ and methods/ — published by GitHub Pages
          js/: data, routing (pure; reads meta.routing), state, pitch, queue, analysis, audit, main
design/   prototype.html, the original design mockup (not published)
data/     events.json, league_stats.json, agent_cache.json, reader_cache.json, xg_fit.json, xt_fit.json; external/ (git-ignored) for StatsBomb files
tests/    seeded_claims.json, meaning_cases.json and pytest suites
scripts/  eval.py, build_seeded_claims.py, write_schema.py, foundry_smoke.py, fetch_statsbomb.py, fit_xg.py,
          fit_xt.py, plot_xt.py, eval_scout.py, eval_meaning.py
```

Facts and events are Pydantic models (`core/facts.py`, `core/events.py`). `fact.schema.json` is generated from the fact models by `python scripts/write_schema.py`, and a test fails if the committed file and the models disagree.

See `SPEC.md` for the full contract, `data_card.md` for the simulator's realism checks and `data_sources.md` for the data sources.

## Roadmap

- **Real match on the site and a match selector.** Not done. Publishing a StatsBomb replay needs a decision on what the licence allows (the bundle holds the full event log) and the StatsBomb logo on the page. The front end also assumes simulator-only fields (speeds, pass difficulty) in its analyst labels.
- **Scout on real data.** The agent's instructions and cache describe the synthetic match; real matches use the rule-based drafter.
- **Switch win probability to poisson_v2.** Fitted and validated (`docs/methods/winprob.md`), not used: it would change every impact value and possibly which facts need a producer.
- The rest of the planned work, in order, is in `ROADMAP_PROMPTS.md`.

## How this was built

PitchSide was built for the Microsoft x Premier League "Inside the Game" hackathon, with AI coding assistance. The hackathon is over; the project is now being reworked into a portfolio piece. The planned work, in order, is in `ROADMAP_PROMPTS.md`.

## License

MIT, see `LICENSE`.
