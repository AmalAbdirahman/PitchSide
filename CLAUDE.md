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
4. The project has **one** LLM agent (the Scout) plus deterministic code. Do not call it "multi-agent" until a second agent with a distinct role exists and is tested.
5. There is no Streamlit app and no live streaming. Do not describe either as existing.
6. Spanish text is unreviewed until a native speaker reviews it.

## Current state (read before changing anything)
- `sim/` seeded simulator, `core/` metrics and models, `app/` Scout agent, verifier, router, renditions, bundle builder, `docs/` static site (GitHub Pages), `tests/`, `scripts/`.
- Facts, events and the match are Pydantic models (`core/facts.py`, `core/events.py`). Field order in `Fact` is the serialised order of hero.json. `fact.schema.json` is generated from the models (`python scripts/write_schema.py`); `tests/test_models.py` fails if they disagree. Moments are still plain dicts until P4.
- A match comes from an `EventSource` (`core/sources.py`): `load()` returns our `Match` and the league xG rate. `SyntheticSource` reads the simulator's files; `StatsBombOpenSource` (`core/statsbomb.py`) maps StatsBomb Open Data, documented in `data_sources.md`. Raw and built real-data files stay in git-ignored `data/external/`, and `app.build_bundle` refuses to write a real bundle into `docs/`. Match time runs on across periods; `meta.clock` (`Clock` in `core/events.py`) holds the period starts and gives the official minute. Never compute a minute as `t // 60`; use `match.meta.clock.minute(t)`.
- Config comes from `config.yaml` through PyYAML into a frozen, validated Pydantic `Settings` (`core/config.py`). Entry points call `load()` once and pass `Settings` (or a section of it) down as an argument; no function reads config through a global.
- `REGISTRY` in `core/metrics.py` is the single definition of every metric (kind, subjects, tolerance, compute). The moment rules, the chart series and the verifier all compute through it. Keep that property: a metric with no registry entry is unverifiable and never airs.
- The verifier is split: pure checks in `app/checks.py`, wording corrections in `app/corrections.py`, and `app/verifier.py` orchestrates and records both.
- Routing over time is data: `routing_rules` in `app/router.py` builds the table from config and hero.json carries it as `meta.routing`. `app/router.resolve` and `docs/js/routing.js` both interpret it, and `tests/routing_cases.json` runs against both (pytest and `node --test`). The front end is plain ES modules in `docs/js/` with no build step; `state.js` asks `routing.js` for each fact's state rather than keeping its own timers.

## Stack rules
- Python 3.12. Allowed runtime deps: `pydantic>=2`, `pyyaml`, `jsonschema`, `numpy`. Analysis extras: `pandas`, `scipy`, `scikit-learn`, `kloppy`, `statsbombpy`, `matplotlib`. Agent extras: `azure-ai-projects`, `azure-identity`, `python-dotenv`. Ask before adding anything else.
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
python -m http.server 8000 --directory docs
```

## Definition of done (every task)
- Tests, ruff and mypy pass locally and in CI.
- You gave me the exact command to see the change working.
- README and docs are updated if behaviour or claims changed.
- You listed anything skipped, uncertain, or left as roadmap.
- Changes are committed.
