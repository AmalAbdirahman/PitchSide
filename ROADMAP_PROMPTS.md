# ROADMAP_PROMPTS.md

Paste one prompt per Claude Code session, in order. Each starts with the same preamble.

**Preamble (put at the top of every prompt):**
```
Read CLAUDE.md first. Work in plan mode: give me a short plan (files, steps, tests) and wait for "go".
After the task: run pytest, ruff and mypy, tell me the command to see it working, list anything skipped, and commit.
```

---

## Phase 1: Immediate refactoring (quick wins)

### P0. Repair the published repo (do this first)
```
The GitHub repo is broken on a fresh clone: config.yaml, fact.schema.json, SPEC.md, data_card.md,
requirements-foundry.txt, pytest.ini and .gitignore are missing, so pytest fails at collection and
the README links to files that do not exist.
1. Check which of these exist in this working copy. For any that are missing here too, tell me and stop:
   I will restore them from my original project folder.
2. Add them, plus CLAUDE.md and ROADMAP_PROMPTS.md.
3. Run pytest on a fresh clone in a temp folder to prove it passes with only pytest and jsonschema installed.
4. Check the GitHub Actions workflow would pass. Add a status badge to the README.
5. Add a "How this was built" note to the README: built for the Microsoft x Premier League
   Inside the Game hackathon, with AI coding assistance.
Do not change any behaviour.
```

### P1. Packaging and tooling
```
Add pyproject.toml (project name pitchside, Python >=3.12) with optional groups: dev (pytest, ruff, mypy,
types), data (pandas, scipy, scikit-learn, kloppy, statsbombpy, matplotlib), agents (azure-ai-projects,
azure-identity, python-dotenv). Remove requirements-foundry.txt and update the README.
Configure ruff and mypy. Fix lint errors without changing behaviour. Get mypy passing on core/ and app/
(strict where practical; list any ignores and why).
Update CI: install ".[dev]", run ruff, mypy, pytest. Add pre-commit config.
The offline rebuild must stay byte-identical: prove it with a hash before and after.
```

### P2. Config without hidden globals
```
Replace the hand-written YAML parser in core/config.py with PyYAML and a typed, frozen Pydantic Settings
model (nested: pitch, xg, winprob, verifier, router, moments, sim, agents). Validate on load.
Stop calling load() inside functions in core/: pass the settings (or the specific values) in as arguments.
Keep a single place at the entry points (build_bundle, sim.generate, scripts) that loads config.
Remove the import-time file read in app/verifier.py (the schema load at module import).
Add tests that run core functions with a custom config. Rebuild must stay byte-identical.
```

### P3. Typed domain models
```
Replace dict[str, Any] facts with Pydantic v2 models: Event, Claim, Window, Subject, FactText, Evidence,
Check, Verification, Risk, Approval, Rendition, Audit, Fact. Generate fact.schema.json FROM the models
(one source of truth) and add a test that the committed schema matches.
Migrate verifier, router, renditions, drafter, agents_foundry and build_bundle to the models.
Serialised hero.json and cues.json must stay byte-identical; if key order must change, stop and ask.
```

### P4. Metric registry and verifier split
```
compute_metric is an if-chain, and the verifier dispatches on metric-name strings in three places
(tolerance, _number_ok, calibrate). Introduce a Metric protocol and a registry: each metric declares its
name, kind (count | share | sum | max), tolerance, subject types, compute(events, subject, window), and
how its numbers are checked. The verifier and moment rules both use the registry.
Split the verifier into: pure checks (no mutation), corrections (intent stripping, hedge softening), and
an orchestrator that records both. Replace the substring player-name match in check_entities with a
tokenised match against the roster and add tests for near-miss names.
No behaviour change except the entity matching; list any seeded claim whose result changes.
```

### P5. One source of truth for routing in the UI
```
docs/app.js re-implements the router timing rules that app/router.resolve defines in Python.
1. Export the routing rules (tiers, veto window, expiry, autopilot rule) into hero.json from config.
2. Make the front end read them rather than hard-code them.
3. Split app.js into ES modules: data, state machine, pitch, queue, analysis, audit. No framework.
4. Add a table-driven test: a JSON file of (fact, elapsed, decision) -> expected state, run against
   router.resolve in pytest AND against the JS state machine with node --test.
5. Add a Playwright smoke test (run locally and in CI): load the page, play to full time at 4x,
   approve one, veto one, let one expire, assert no console errors.
```

---

## Phase 2: Feature and maths expansion

### P6. Real open data adapter
```
Add a data source layer so the pipeline runs on real open data as well as the simulator.
- Define an EventSource protocol that yields our Event model in our coordinate convention.
- Implement SyntheticSource (existing sim) and StatsBombOpenSource (via kloppy or statsbombpy).
- scripts/fetch_statsbomb.py downloads chosen matches into data/external/ (git-ignored). Do not commit
  raw data. Add the required StatsBomb attribution to the README and the site footer.
- Map event types, period clocks, coordinates (120x80 to 105x68, attack left to right) and outcomes.
  Document every mapping decision and every event type we drop.
- Run the full pipeline (rules drafter, no agent) on one real match and write a bundle to docs/data/.
- Add a match selector to the site.
Tell me which licences apply and what I may commit before writing anything to docs/.
```

### P7. Fitted xG with validation
```
Replace the hand-set xG coefficients with a model fitted on StatsBomb open shots.
- Features: distance, goal angle, body part, shot type (open play, free kick, penalty handled separately),
  under pressure. Start with logistic regression; keep it interpretable.
- Split by match (not by shot) into train and test. Report log loss, Brier score and a reliability curve
  on the test set, compared with StatsBomb's own xG and with a constant baseline.
- Save coefficients to config with provenance (data, date, commit). The simulator uses the same model.
- Write docs/methods/xg.md with the method, results and limits. Numbers only from a real run.
```

### P8. Expected Threat (xT)
```
Implement xT as a Markov model on a 16x12 grid: per-cell shoot probability, move probability, scoring
probability and the move transition matrix, solved by value iteration. Fit on StatsBomb open data.
- Show convergence (max change per iteration) and the resulting surface as a heatmap.
- Add metrics: xt_added for a player or team over a window (successful passes and carries).
- Register them in the metric registry so the verifier can recompute any xT claim.
- Add an xT panel to the Analysis view and one moment rule that uses it.
- docs/methods/xt.md: method, what xT ignores (defenders, off-ball), and that values depend on the
  training data. If only synthetic data is used anywhere, say it is fitted and measured on the same data.
```

### P9. Calibrated language, done properly
```
The hedge rule uses a Wilson interval, and the moment detector re-tests every checkpoint, so repeated
looks inflate false "clear" calls.
1. Add a Beta-Binomial hedge: prior from league lane shares, posterior P(share > baseline). Thresholds in
   config. Keep Wilson available and compare both.
2. Measure the problem: simulate many matches with NO planted overload and report how often each rule
   announces a "clear" overload per match. Then fix it (alpha spending, a stricter sequential threshold,
   or a minimum effect size) and report the false-alarm rate before and after, plus detection delay on
   matches WITH a planted overload.
3. docs/methods/hedging.md with the results table.
```

### P10. Win probability validation and pass networks
```
A. Win probability: keep poisson_v1 but validate it. On the simulated league and on StatsBomb matches,
   compute calibration by game-state bucket and the Brier score against a baseline (pre-match constant).
   If it is poorly calibrated, add a Dixon-Coles style correction or time-varying rates, and report both.
B. Pass networks: per team and window, build the weighted pass graph; report degree and betweenness
   centrality and a simple community split. Add a network plot to the Analysis view and register
   "most_central_player" as a verifiable metric.
Methods notes for both.
```

### P11. Scout with structured output and tools
```
Today the Scout is handed pre-computed numbers and paraphrases them; moment detection is all rules.
1. Structured output: replace the regex fence-stripping parser with schema-constrained output validated
   by the Pydantic models. Malformed output is a typed error with a retry.
2. Tool calling: give the Scout read-only tools backed by the metric registry: list_metrics(),
   compute_metric(metric, subject, window), list_events(filter). In "explore" mode the Scout proposes
   claims itself from a match-state summary; the verifier still recomputes everything.
3. Memory: pass a compact match-state record (facts already aired, last claim per team) so the Scout
   avoids repeats and can refer back. Store it in the audit trail.
4. Keep the cache, the call budget and the rules fallback. Keep the planted-error eval separate.
5. Eval: report first-pass rate, retry rate, rejection rate and duplicate rate for explore mode versus
   the current handed-numbers mode, from a real run.
Only now may the README describe the Scout as using tools.
```

### P12. Close the meaning gap
```
The verifier checks numbers, not sentence meaning ("69% of centre attacks" vs "69% of attacks went
through the centre"). Add a round-trip check:
1. A Reader agent (second, independent prompt) receives ONLY the sentence and the roster, and returns a
   structured claim (subject, metric, window, value).
2. Deterministic code compares the Reader's claim with the Scout's structured claim. A mismatch fails a
   new check, "meaning_matches", and triggers the existing retry.
3. Build a labelled test set of at least 60 sentences (correct, and wrong in meaning but right in numbers)
   and report precision and recall of the check.
4. Record the Reader step in the audit trail and show it in the Audit view.
With two agents with distinct roles and a tested handoff, the README may now say "two-agent pipeline".
```

---

## Phase 3: Production-grade scaling

### P13. Clean broadcast output
```
Add docs/overlay.html: a transparent-background, 1920x1080 "program output" that shows only the score bug
and lower thirds, driven by the same state as the control room (BroadcastChannel between the two tabs).
It must work as an OBS browser source. Add a short guide and a screenshot to the README.
Add layouts: lower third, stat comparison, player card. Each is a typed cue layout in the schema.
```

### P14. Live mode on a local stack
```
Add a live mode that runs on a laptop with docker compose, so the streaming design is real, not a diagram:
- Redpanda (Kafka API) topic "events": a replayer publishes a match's events in real time or at Nx speed.
- A Python worker consumes events, keeps match state in Redis, runs moment rules, drafts (rules or cached
  Scout), verifies and routes, and publishes facts.
- A small FastAPI service exposes a WebSocket for the control room and overlay, and accepts producer
  decisions. Facts and decisions are appended to an event-sourced audit log (Postgres).
- The static replay mode keeps working unchanged.
- Tests: an end-to-end test that replays a match at high speed and compares the final audit log with the
  offline bundle.
Measure and report end-to-end latency from event to fact-ready.
```

### P15. Azure deployment design (document first, deploy a thin slice)
```
Write docs/architecture/azure.md mapping the local stack to Azure, with a diagram:
Event Hubs (Kafka endpoint) -> Container Apps worker (KEDA scaling on lag) -> Azure Cache for Redis
(match state) -> Web PubSub or SignalR (UI and overlay) -> Postgres or Cosmos DB (audit log) ->
Blob Storage (bundles); Foundry for agents; Key Vault and managed identity; Application Insights with
OpenTelemetry traces across agent calls and verifier checks.
Include: partitioning by match_id, ordering and exactly-once handling of decisions, back-pressure,
what happens when the model is slow or down, cost estimate per match, and what is NOT built.
Then write Bicep for the thin slice only (Event Hubs, one Container App, Redis) and a deploy script.
Do not deploy without asking me. Mark everything not deployed as design, not implementation.
```
