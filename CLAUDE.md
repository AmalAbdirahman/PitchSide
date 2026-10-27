# CLAUDE.md

## What this is
A producer-first control room for a replayed synthetic football match, built for a hackathon. Python builds a replay bundle offline. A static web page replays it. Deterministic code verifies facts, risk tiers decide what airs automatically and what a human approves, and each approved fact becomes a cue, EN and ES captions, and an audio script.

Source of truth: `SPEC.md` and `fact.schema.json`. If code and spec disagree, stop and ask me.
All data is synthetic. Clubs and players are fictional. No real names, logos, crests or real match data. Ever.

## Stack (do not change without asking)
- Python 3.12 standard library plus `pytest` and `jsonschema`. No other dependencies without asking.
- Front end: plain HTML, CSS and JavaScript in `docs/`. No npm, no framework, no build step, no CDN links, no network calls.
- No servers, no database, no cloud services, no API keys, no LLM calls anywhere. One exception: `scripts/foundry_smoke.py`, a manual connectivity check (see below).
- Hosting: GitHub Pages serving the `docs/` folder. Optional: a GitHub Actions workflow that runs `pytest -q`.
- Do not mention or claim any Microsoft or Azure technology in code or docs, except the optional build-time Translator add-on in SPEC section 18, and only when I explicitly ask for it. Even then: it runs only inside the build script on my machine, keys stay in a local `.env`, and nothing in `docs/` ever calls the network.
- Foundry smoke test: `scripts/foundry_smoke.py` checks that my Foundry project and model deployment answer. I run it by hand on my machine; it reads its endpoint and model from `.env`. Nothing in `app/`, `core/`, `sim/`, `docs/` or `tests/` imports or calls it, it is not part of the build, eval or CI, and its packages (`azure-ai-projects`, `azure-identity`, `python-dotenv`) are not project dependencies.

## Layout
```
sim/      generate.py
core/     xg.py shares.py winprob.py wilson.py moments.py
app/      drafter.py verifier.py router.py renditions.py templates_es.py build_bundle.py
docs/     index.html app.js styles.css prototype.html data/
tests/    seeded_claims.json test_*.py
scripts/  eval.py foundry_smoke.py
config.yaml  README.md  data_card.md
```

## Commands
- Simulate: `python -m sim.generate --seed 7`
- Build bundle: `python -m app.build_bundle`
- Test: `pytest -q`
- Eval: `python scripts/eval.py`
- Foundry smoke test (manual, needs `.env`): `python scripts/foundry_smoke.py`
- Serve locally: `python -m http.server 8000 --directory docs`

## Rules
1. Code decides truth. There is no LLM. The drafter is rule-based and injects known errors so the verifier has something to catch.
2. Mark every injected error with `injected: true`.
3. Every fact validates against `fact.schema.json` at creation and after verification.
4. Every number in a rendition must come from the fact's `text.numbers`. Check it in code.
5. Thresholds and timings live in `config.yaml`, not in code.
6. Keep files small and functions short. Type hints everywhere.
7. Write the verifier tests first, using `tests/seeded_claims.json`.
8. Do not add dependencies, files or features that are not in the current task. Ask first.
9. Front end basics: labelled buttons, enough contrast, keyboard operable, respect `prefers-reduced-motion`. It must work from a plain static server with no network.
10. Original work only. Do not copy code or text from other hackathon repos.
11. Never claim in the README something that does not work. Mark unfinished items as roadmap. Report eval numbers only from a real run.
12. Treat the Spanish text as unreviewed and say so in the README.

## Definition of done (every task)
- `pytest -q` passes.
- You gave me the exact command to see it working.
- Changes are committed with a clear message.
- You listed anything you skipped or are unsure about.
