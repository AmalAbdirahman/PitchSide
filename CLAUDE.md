# CLAUDE.md

## What this is
PitchSide is a broadcast control room for football match insights. A drafting agent proposes claims, deterministic code recomputes every claim from the event log, risk tiers decide what airs automatically and what a human approves, and each approved fact becomes a cue, captions and an audio script.

The hackathon is over. The goal now is a polished, honest portfolio project: clean architecture, real open data, properly fitted models, and a typed agent pipeline. Work through `ROADMAP_PROMPTS.md` in order, one task per session.

## The one idea that must survive every change
**Code decides truth.** No number, player, team or time reaches a viewer unless deterministic code has recomputed it from the event log. Every new metric must be recomputable by the verifier. A metric the verifier cannot check does not ship.

## Honesty rules (never break these)
1. Never claim in the README or docs something that does not run. Unfinished work goes under "Roadmap".
2. Report numbers (eval, calibration, latency) only from a real run. Say which data and which commit.
3. Say what is synthetic, what is real open data, and what is a model estimate.
4. The project has **two** LLM agents (the Scout drafts, the Reader reads each sentence back) plus deterministic code, which alone decides what airs. "Two-agent pipeline" is accurate; do not claim more agents than exist and are tested.
5. There is no Streamlit app. Live streaming exists only as the local docker compose stack (P14, run in CI and with `live.dev`); it is not deployed. Do not describe it as hosted or production.
6. Spanish text is unreviewed until a native speaker reviews it.

## Current state (read before changing anything)
- `sim/` seeded simulator, `core/` metrics and models, `app/` Scout agent, verifier, router, renditions, bundle builder, `docs/` static site (GitHub Pages), `tests/`, `scripts/`.
- Facts, events and the match are Pydantic models (`core/facts.py`, `core/events.py`). Field order in `Fact` is the serialised order of hero.json. `fact.schema.json` is generated from the models (`python scripts/write_schema.py`); `tests/test_models.py` fails if they disagree. Moments are still plain dicts until P4.
- A match comes from an `EventSource` (`core/sources.py`): `load()` returns our `Match` and the league xG rate. `SyntheticSource` reads the simulator's files; `StatsBombOpenSource` (`core/statsbomb.py`) maps StatsBomb Open Data, documented in `data_sources.md`. Raw and built real-data files stay in git-ignored `data/external/`, and `app.build_bundle` refuses to write a real bundle into `docs/`. Match time runs on across periods; `meta.clock` (`Clock` in `core/events.py`) holds the period starts and gives the official minute. Never compute a minute as `t // 60`; use `match.meta.clock.minute(t)`.
- xG (`core/xg.py`) is fitted by `scripts/fit_xg.py` on StatsBomb shots (raw files via `scripts/fetch_statsbomb.py --xg`); `data/xg_fit.json` is the record and a test fails if `config.yaml`'s coefficients differ from it. Refitting changes the simulation, the bundle and the seeded claims; regenerate all three.
- xT (`core/xt.py`) is fitted by `scripts/fit_xt.py` on the xG training matches; the surface lives in `config.yaml` (`xt.surface`) and a test keeps it equal to `data/xt_fit.json`. `scripts/plot_xt.py` draws the figures in `docs/methods/`. Metric compute functions take a `MetricContext` (pitch and xT surface, built with `MetricContext.of(settings)`), not a bare pitch.
- Config comes from `config.yaml` through PyYAML into a frozen, validated Pydantic `Settings` (`core/config.py`). Entry points call `load()` once and pass `Settings` (or a section of it) down as an argument; no function reads config through a global.
- `REGISTRY` in `core/metrics.py` is the single definition of every metric (kind, subjects, tolerance, compute). The moment rules, the chart series and the verifier all compute through it. Keep that property: a metric with no registry entry is unverifiable and never airs.
- Win probability (P10): the product uses poisson_v1; `core/winprob.outcome_probs` with `PoissonParams` also runs the fitted poisson_v2, which only `scripts/eval_winprob.py` uses (record `data/winprob_eval.json`, note `docs/methods/winprob.md`).
- Win probability (P10): the product uses poisson_v1. `core/winprob.outcome_probs` with `PoissonParams` also runs the fitted poisson_v2, which only `scripts/eval_winprob.py` uses (record `data/winprob_eval.json`, note `docs/methods/winprob.md`).
- Scout (P11): `app/agents_foundry.py` runs the loop, cache and Foundry caller; `scout_schema.py` (Pydantic output models and the strict JSON Schema), `scout_tools.py` (read-only list_metrics / compute_metric / list_events, never past the moment), `scout_memory.py` (aired facts, duplicate rule), `scout_requests.py` and `scout_prompts.py` (handed and explore modes). `config.yaml agents.mode` picks the site's mode (handed). The cache records tool calls and replay re-runs them. `scripts/eval_scout.py` compares the modes (record `data/scout_eval.json`).
- Live mode (P14): `live/` (replayer, worker, api, ports, adapters, dev, static/). `live/pipeline.py` reuses the offline per-moment functions in the offline order, so a full replay equals the bundle minus the planted errors; keep that true (tests/test_live.py, and tests/test_live_stack.py in CI's `live` job). Ports (Bus, StateStore, AuditLog) have in-memory versions; the real adapters import aiokafka, redis and psycopg lazily. Extras group `live`. Latency: `scripts/live_latency.py`, note `docs/methods/live.md`.
- Program output (P13): every cue rendition carries a typed `card` (`core/facts.Card`: lower third, ticker, stat comparison, player card), built by `app/cards.py` and number-checked by `app/renditions.card_numbers_ok`. A stat comparison's other-team value is recomputed through the registry. `docs/overlay.html` draws the score bug and the on-air card; `docs/js/program.js` sends state from the control room over a BroadcastChannel and `docs/js/cards.js` (pure) draws it. Not tested in OBS.
- Azure (P15): `docs/architecture/azure.md` is a design; `infra/` (main.bicep, modules/, deploy.ps1) is a thin slice (Event Hubs, worker Container App, Azure Managed Redis) that builds but has never been deployed. `deploy.ps1` only builds unless given `-WhatIf` or `-Deploy`; never deploy without asking. The slice's settings are env-only in `live/adapters.py` (`PITCHSIDE_KAFKA_SASL_PASSWORD`, `PITCHSIDE_AUDIT=stdout`); defaults keep the compose stack unchanged. `tests/test_live_cloud.py` builds the Bicep when `az` is installed.
- Reader (P12): `app/reader.py` is the second agent: it sees only a Scout sentence and the roster and returns a `core/facts.Reading`; `app/meaning.py` compares it with the claim and `verify(..., reading)` adds the `meaning_matches` check (absent without a reading, and for T0 and rule-based text). Reads are cached in `data/reader_cache.json`. `build_bundle --foundry` adds to both caches and never drops entries, because `eval_scout.py` replays requests the site no longer makes. `scripts/eval_meaning.py` scores the check on `tests/meaning_cases.json` (record `data/meaning_eval.json`; a replay checks the record and does not rewrite it).
- Pass networks (P10): `core/network.py` infers each pass's receiver (next on-ball player of the same team), then degree, betweenness and communities. The `most_central_player` metric is a betweenness rank (1 = unique leader); the `pass_hub` moment names it at half-time if `moments.hub_min_passes` is met. `series.network` carries per-half networks every 5 minutes for the Analysis view.
- Lane-share hedges (P9): `EventSource.load()` returns a `League` (mu, per-lane league shares, `attack_deff`, the variance inflation of lane counts). `core/hedge.judge_share` is the single hedge for lane shares, used by moments, verifier and series; Wilson stays only for comparison and for pass accuracy (no baseline). `scripts/eval_hedging.py` is the false-alarm study behind `config.yaml` `hedge`; `docs/methods/hedging.md` has the results.
- The verifier is split: pure checks in `app/checks.py`, wording corrections in `app/corrections.py`, and `app/verifier.py` orchestrates and records both.
- Colours (R1): every colour is a token on `:root` in `docs/styles.css` (dark default; light under `prefers-color-scheme` and `[data-theme="light"]`, kept equal). JS reads them with `readColours()` into `COL` (`docs/js/data.js`) and redraws on the `themechange` event from `docs/js/theme.js`. Green, amber and red (`--verified`, `--corrected`, `--rejected`) mean verifier outcomes and tiers only; the accent is never a status; team-coloured text uses `--home-text`/`--away-text`; `--air-*` tokens (on-air graphics) are never redefined. `tests/test_theme.py` forbids colour literals elsewhere and checks AA contrast.
- Routing over time is data: `routing_rules` in `app/router.py` builds the table from config and hero.json carries it as `meta.routing`. `app/router.resolve` and `docs/js/routing.js` both interpret it, and `tests/routing_cases.json` runs against both (pytest and `node --test`). The front end is plain ES modules in `docs/js/` with no build step; `state.js` asks `routing.js` for each fact's state rather than keeping its own timers.

## Stack rules
- Python 3.12. Allowed runtime deps: `pydantic>=2`, `pyyaml`, `jsonschema`, `numpy`. Analysis extras: `pandas`, `scipy`, `scikit-learn`, `kloppy`, `statsbombpy`, `matplotlib`. Agent extras: `azure-ai-projects`, `azure-identity`, `python-dotenv`. Live extras (P14, approved): `fastapi`, `uvicorn[standard]`, `aiokafka`, `redis`, `psycopg[binary]`, `httpx`. Ask before adding anything else.
- Tooling: `ruff` (lint and format), `mypy --strict` on `core/` and `app/`, `pytest`. All three run in CI.
- Packaging: one `pyproject.toml` with optional dependency groups (`dev`, `data`, `agents`). No loose requirements files.
- Front end: plain static files in `docs/`, no CDN, no network calls for data. TypeScript is allowed if the compiled output is committed and Pages still serves `docs/` with no build step on GitHub.
- The static site never calls a model or Azure. Agents run at build time; responses are cached and committed.
- No secrets in git. `.env` stays ignored. Never print keys or endpoints.

## Data rules
- Synthetic data: fictional clubs and players only.
- Real data: open datasets only (StatsBomb Open Data, Metrica sample data). Follow each licence: keep attribution, do not commit raw data, download with a script into an ignored folder, and commit only small derived artefacts the licence allows.
- Do not use real club crests, league logos or broadcaster branding.
- Data from the Metrica "More than a Hack" competition must never enter this repo.

## Engineering rules
1. One task per session. Plan first, wait for "go", then implement.
2. Write or update tests first for any behaviour change. `pytest -q` must pass with no network.
3. Keep functions short and typed. No `dict[str, Any]` in new code where a model exists.
4. No hidden globals in new code: pass config and data in as arguments.
5. No `except Exception` without a comment saying why and what happens next.
6. Do not change verifier behaviour and refactor in the same commit.
7. Keep the offline rebuild byte-identical unless the task says outputs change. If outputs change, say exactly why in the commit message.
8. Thresholds and constants live in config, with a comment stating where the value came from (fitted, literature, or chosen by hand).
9. Every model (xG, xT, win probability) ships with a short method note and a validation result: calibration plot or table, and the metric used.
10. Small commits with clear messages. Never rewrite published history.

## Commands
```
python -m pip install -e ".[dev]"            # after P1
pytest -q
ruff check . && ruff format --check .
node --test "tests/js/*.test.mjs"
pytest -m e2e                                # browser smoke test; needs ".[e2e]" and playwright install chromium
mypy core app
python -m sim.generate --seed 7
python -m app.build_bundle                    # offline, replays data/agent_cache.json
python -m app.build_bundle --foundry          # live Scout; needs .env
python scripts/eval.py
python scripts/eval_scout.py                  # P11 Scout modes; --foundry for a live run
python scripts/eval_meaning.py                # P12 meaning check; --foundry for a live run
docker compose up -d --build --wait           # P14 live stack; then docker compose run --rm replayer
python -m live.dev --speed 20                 # P14 live mode in one process, no Docker
PITCHSIDE_STACK=1 pytest -m stack             # P14 stack test (needs the compose stack)
python scripts/eval_winprob.py                # P10 study; needs fetch_statsbomb.py --xg
python -m http.server 8000 --directory docs
```

## Definition of done (every task)
- Tests, ruff and mypy pass locally and in CI.
- You gave me the exact command to see the change working.
- README and docs are updated if behaviour or claims changed.
- You listed anything skipped, uncertain, or left as roadmap.
- Changes are committed.
