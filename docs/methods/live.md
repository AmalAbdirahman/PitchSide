# Live mode on a local stack

The published site replays a pre-built bundle. Live mode runs the same pipeline on a stream of events, on a laptop, with `docker compose`. It is not deployed anywhere.

```
 replayer ──► Redpanda "events" ──► worker ──────────────► Redpanda "facts" ──► API ──► WebSocket ──► live control room
 (match time    (Kafka API, key =    │ state in Redis                        │ FastAPI          │        (live/static)
  or N x)        match id, 1 part.)  │ moment rules, rule draft,             │ POST /decisions  └──► BroadcastChannel ──► overlay
                                     │ cached Scout + Reader,                │                                         (docs/overlay.html)
                                     │ verify, route, render                 │
                                     └──► Postgres audit_log ◄───────────────┘ decisions
                                          (event-sourced: match_start, fact_ready, fact_rejected, decision, match_end)
```

- **Replayer** (`live/replayer.py`) publishes a Start message (match metadata and league priors), every event in order, at match time or N times faster, then End at the final whistle.
- **Worker** (`live/worker.py`, logic in `live/pipeline.py`) runs the moment rules on the events so far after each event. Every moment whose time has come is drafted, verified, routed and rendered by the same functions as the offline build, with the Scout and Reader replaying their caches. It publishes a tick (clock and score) per event and each fact, and appends facts to the audit log. Its state (events, moments handled, the Scout's memory) is in Redis, so a restarted worker resumes; repeated messages are skipped by sequence number and a fact recorded twice keeps its first row.
- **API** (`live/api.py`) reads the facts topic from the start, so a restarted API rebuilds its view. It serves the live control room, a WebSocket with a snapshot and every change, and `POST /decisions`. Decisions are checked against the same routing rules as the replay (`app/router.resolve_state`), timed in real seconds from when the fact was ready, and appended to the audit log. A unique index allows one decision per fact, so the first one wins and a second gets 409.
- **Live control room** (`live/static/`) shows the facts with their routing state and the producer's buttons, and feeds `docs/overlay.html` over the same BroadcastChannel as the static control room.

## Same result as the offline build

Moment detection is causal: on every prefix of the event log it finds the same moments, with the same content and order, as on the whole match (checked over all 606 events of the synthetic match). The worker handles moments in that order, with the Scout's memory built the same way as `app/agents_foundry.run_scout`, so the cached Scout and Reader requests are identical and replay.

`tests/test_live.py` replays the whole match at full speed through the worker on in-memory adapters and checks that the audit log's facts equal the offline bundle's, field for field (38 facts; none of the unplanted drafts is rejected by the verifier). `tests/test_live_stack.py` does the same against the docker compose stack; it runs in CI (job `live`), which starts the stack, publishes the match to Redpanda at 20x, waits for the worker container to record the final whistle in Postgres, compares, and checks the API.

**Not in live mode:** the six planted test errors of the offline bundle (`app/drafter.injected_drafts`). They are built from the whole match, which a live worker does not have, so the comparison leaves them out. Because the bundle's two T2 facts are planted ones, the live match has no T2 facts.

## Latency

Event to fact-ready: wall clock from the replayer sending the event that made a moment due to the worker having that fact verified, routed and rendered. At 20x, events arrive about 0.45 s apart on average, far more than the worker needs, so nothing queues and the figure is what real-time play would see. Twelve moments fall due together at the final whistle (half- and full-time summaries) and are handled one after another, which is why that row is slower.

| run | setup | facts | median | p95 | max |
|---|---|---|---|---|---|
| CI run 43534999209 (GitHub Actions), commit 9158f7d, 2026-11-14 | docker compose on a GitHub-hosted Ubuntu runner; replayer on the runner, worker in its container; read from Postgres | during play, 26 | 26.0 ms | 62.0 ms | 63.2 ms |
| | | at the final whistle, 12 | 110.3 ms | 118.1 ms | 118.1 ms |
| local, `data/live_latency.json`, commit 69bfacb plus the uncommitted P14 code, 2026-11-14 | one process, in-memory adapters (no Redpanda, Redis or Postgres), Windows 11 laptop, Python 3.13 | during play, 26 | 45.2 ms | 89.4 ms | 89.5 ms |
| | | at the final whistle, 12 | 171.6 ms | 172.2 ms | 172.2 ms |

The stack run includes the hops through Redpanda, Redis and Postgres and was still faster than the laptop run without them, so these numbers say more about the machines than about the infrastructure. Both are single runs of one synthetic match. Most of the time goes into re-running the moment rules over all events so far after every event, which grows with the match; the Scout and Reader cost nothing here because they replay caches.

## Limits

- The Scout and Reader only replay their caches in live mode; a request that is not cached falls back to the rule-based draft. Calling a model live would add seconds per fact.
- One partition per topic and one worker: fine for one match at a time, not a design for many matches.
- The API has no authentication and the compose passwords are throwaway; it is for a laptop, not a network.
- Replaying the same match again is a no-op while Redis remembers it; `docker compose down -v` resets.
- The live control room is a small page (facts, states, decisions, on-air, log), not the full replay control room with pitch, analysis and audit views.
- Not tested in OBS (see the README's overlay section).
