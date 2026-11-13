# The Scout: structured output, tools and memory

The Scout is the project's drafting agent: a prompt agent on Microsoft Foundry that drafts a claim and a broadcast sentence for each moment the rules detect. It runs at build time only; its answers, including every tool call, are cached in `data/agent_cache.json` and replayed with no network. Whatever it writes, the deterministic verifier recomputes from the event log before anything can air. Since P12 a second agent, the Reader, reads each of its sentences back for the verifier's meaning check (`docs/methods/meaning.md`); the results below are from before the Reader and from `scripts/eval_scout.py`, which runs without it.

## Two modes

- **handed** (the site's build, `config.yaml agents.mode`): the Scout is given the moment's numbers, claim value and evidence ids, all computed by code, and words them.
- **explore**: the Scout is given the match state (minute, score, teams, players), the moment as a *lead* (its rule, subject and window, with no numbers or evidence) and its memory. It finds numbers and evidence itself with three read-only tools, and may write about something else the tools show. Moment detection is still done by the rules: they decide *when* the Scout looks, not what it says.

## Structured output

The reply must follow a JSON Schema generated from Pydantic models (`app/scout_schema.py`): a claim (subject, metric, window, comparator, value), one sentence with its numbers, and evidence ids. Foundry is asked for strict structured output with that schema; the reply is then validated against the same models, strictly (a number sent as text is an error). A reply that fails is a typed error (`ScoutOutputError`) and gets one retry with the problem as feedback; a second failure falls back to the rule-based draft. Rules the schema cannot express are checked in code: in handed mode the numbers' keys must equal the input's; in explore mode goals, substitutions and cards may only restate the lead (they air at once, T0); a share claim's league baseline is always set by code.

## Tools (`app/scout_tools.py`)

| tool | what it returns |
|---|---|
| `list_metrics()` | every metric in the registry, with kind and subject types |
| `compute_metric(metric, subject_type, subject_id, start_t, end_t)` | value, n, k and evidence ids from `core/metrics.REGISTRY`, the computation the verifier runs |
| `list_events(type, team, player_id, start_t, end_t)` | matching events, oldest first, capped at `agents.max_events` |

Every tool answers from the event log up to the moment's time and refuses windows that end later, so the Scout cannot see the future. The tools only read. Each call and its output is recorded in the cache; a replay re-runs every recorded call and refuses the record (falling back to the rules) if any output has changed, so a cache cannot go stale silently.

## Memory (`app/scout_memory.py`)

Each request carries the last `agents.memory_size` facts that passed the verifier before the moment (id, minute, metric, subject, sentence) and the last sentence about each team. The fact ids shown are stored in the fact's audit trail (`audit.memory`). A fact counts as a **duplicate** if it repeats the sentence of an earlier fact that passed, or makes a claim on the same metric and subject over an overlapping window.

## Results

From `python scripts/eval_scout.py --foundry` on 2026-11-13 (record `data/scout_eval.json`), on the synthetic replay's 38 moments, gpt-5-mini through the Foundry agent path. The record's commit reads `c269994-dirty`: the eval script and its tests were not yet committed; they are in the commit that adds the record. The first live run lost 13 explore-mode retries to connection errors; a second live run reused every answered call from the cache and repeated only those 13, and the table is from that run. Rates are per moment.

| mode | passed first time | passed after retry | still rejected | fell back to rules | invalid replies | aired | duplicates | tool calls | model responses | median latency |
|---|---|---|---|---|---|---|---|---|---|---|
| handed | 36 (94.7%) | 2 (5.3%) | 0 | 0 | 2 | 38 | 1 of 38 (2.6%) | 0 | 40 | 10.9 s |
| explore | 24 (63.2%) | 7 (18.4%) | 7 (18.4%) | 0 | 0 | 31 | 1 of 31 (3.2%) | 146 | 134 | 22.6 s |

Reading it:

- **Handed mode is the safer product.** With the numbers given, nearly every draft passes first time and none is rejected.
- **Explore mode finds real claims but words numbers worse.** Every one of its rejections failed the number check, not the claim itself: the claimed value, subject, window and evidence came from `compute_metric` and matched. All seven final rejections wrote the window's start minute one too low (600 s is the 11th minute; the Scout wrote "10th"), and two used number keys the verifier does not know (`shots`, `attack_share_centre` instead of `value`). The verifier rejected all of them, so none could air; a minute tool, or the official minute in `compute_metric`'s answer, is the obvious next fix.
- **Duplicates are rare in both modes** (one each) with memory switched on. There is no run without memory to compare against, so this does not show that memory caused it.
- **Explore costs more:** about 3.5 model responses and 3.8 tool calls per moment, and twice the latency.
- No reply in explore mode broke the schema; two handed replies did, and both were retried.

The site's bundle uses handed mode; explore mode exists for this comparison.

## Limits

- One synthetic match, 38 moments per mode: the rates are counts out of 38, not estimates with small error.
- The Scout is one model (gpt-5-mini) at one point in time; another run can give different wording and different rates.
- Explore mode still starts from the rules' moments; the Scout does not decide when to speak.
- The verifier checks numbers, entities, windows, evidence, hedges and intent words, not whether a sentence is interesting or what it means beyond that (README, "What the verifier does not catch").
