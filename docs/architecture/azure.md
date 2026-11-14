# Azure deployment design (P15)

**Status: design, plus a thin slice of Bicep that builds but has never been deployed.** Nothing in this note runs on Azure. The local stack in `docker-compose.yml` (see [live mode](../methods/live.md)) is the running implementation; this note maps it to Azure services and says what would have to change. The static site stays on GitHub Pages and never calls Azure.

| | |
|---|---|
| Built and tested | `infra/` Bicep for Event Hubs, Azure Managed Redis and the worker as one Container App (the "thin slice"); `infra/deploy.ps1`; the worker settings the slice needs (`live/adapters.py`: Kafka over SASL_SSL, audit log as JSON lines). `tests/test_live_cloud.py` checks the settings, builds every Bicep file with no warnings, and checks the deploy script deploys nothing by default. |
| Built, not run | The slice itself: never deployed, so never run against real Event Hubs, Redis or Container Apps. |
| Design only | Everything else in this note. |

## Local stack to Azure

| Local (P14) | Azure (design) | In the slice |
|---|---|---|
| Redpanda, topics `events` and `facts` | Event Hubs, Standard tier, Kafka endpoint on port 9093 | yes |
| `live.worker` container | Container Apps, KEDA `kafka` scaler on the consumer group's lag | yes |
| Redis (match state) | Azure Managed Redis | yes |
| `live.api` (FastAPI, WebSocket, `POST /decisions`) | Container App for HTTP, with Web PubSub fanning out to the control rooms and overlays | no |
| Postgres `audit_log` | Azure Database for PostgreSQL Flexible Server (same table and unique index), or Cosmos DB | no; the slice writes the audit log to stdout, which goes to Log Analytics |
| `docs/data/hero.json` built by `app.build_bundle` | Blob Storage, one bundle per match | no |
| Scout and Reader (cached Foundry calls at build time) | Foundry project, called live with a deadline, results still cached | no |
| `.env`, compose passwords | Key Vault and managed identity | no; Container App secrets |
| `scripts/live_latency.py` | Application Insights with OpenTelemetry traces | no |

```
                        Event Hubs namespace (Kafka endpoint :9093)
 replayer / feed ──►  ┌──────────────────────────────┐
  key = match_id      │ events  (P partitions)       │──► worker Container App ──► Azure Managed Redis
                      └──────────────────────────────┘     (KEDA: replicas follow     (state per match:
                      ┌──────────────────────────────┐      lag, at most P)           start, events,
                      │ facts   (P partitions)       │◄──  moment rules, draft,       moments, memory)
                      └──────────────┬───────────────┘     verify, route, render
                                     │                          │        │
                                     ▼                          │        └──► Foundry (Scout, Reader)
                         API Container App ◄── POST /decisions  │             deadline, then rule draft
                          │   │                                 ▼
                          │   └──► Postgres audit_log ◄─────────┘  (fact_ready, decision, ...)
                          ▼
                   Web PubSub (group per match) ──► control room, overlay
                   Blob Storage: bundles and audit exports
                   Key Vault + managed identities;  Application Insights (OpenTelemetry)
  [thin slice = Event Hubs + worker Container App + Redis; audit to stdout -> Log Analytics]
```

## Partitioning by match

Every message carries `match_id`, and the replayer already uses it as the Kafka key (`live/messages.py`). Event Hubs hashes the key to a partition, so all of a match's events land in one partition, in order. One worker replica owns a partition at a time through the Kafka consumer group, so one match is handled by one process.

- The slice uses one partition: one match at a time, which is what it is for.
- For many matches, create the hubs with more partitions. On the Standard tier the partition count is fixed at creation and capped at 32, so choose it up front. Replicas above the partition count sit idle, so `maxReplicas` in `infra/modules/worker.bicep` should equal it.
- Load is tiny: the synthetic match has 606 events over about 95 minutes, roughly 0.1 messages a second. Throughput is not the limit; the worker's CPU is (below).

## Ordering and exactly-once

Kafka (and Event Hubs) deliver at least once. The local stack already turns that into effectively exactly-once results, and the design keeps those rules:

- **Events.** The worker skips an event whose `seq` it has already applied (`live/worker.py`), so a redelivered message or a restart does nothing twice.
- **Facts.** The audit log has one row per `(match_id, kind, fact_id)`. A fact recorded again keeps its first row.
- **Decisions.** The same unique index allows one decision per fact. The first one wins, and the API returns 409 to the second. On Postgres this is the existing `audit_one_per_fact` index. On Cosmos DB it would be a document id `match_id:kind:fact_id` in partition `match_id`, written with *create*, not *upsert*, so a second decision is a 409 conflict.
- **Producers.** The adapters ask for an idempotent Kafka producer (`enable_idempotence=True`). *Unsure:* I have not checked that Event Hubs' Kafka endpoint accepts this on the Standard tier. If it does not, turn it off. Duplicates are still dropped by `seq` on the consumer side.

**Gap (not built):** during a consumer-group rebalance, two replicas can briefly hold the same match. Both would read its state from Redis, and both could append the same event. The audit log would still hold one row per fact, but the Redis event list could hold a duplicate. The fix is a per-match lease (Redis `SET NX` with a TTL, renewed while the replica works), plus writing the event's `seq` with it.

## Back-pressure

- Kafka is pull-based, so a slow worker never blocks the feed. Lag grows on the `events` partition instead.
- KEDA reads that lag and adds replicas, up to the partition count. The slice sets a lag threshold of 50 messages, with `minReplicas = 0` to save cost. The price is a cold start before the first event of a match is handled. For a real match, keep one replica warm.
- The worker's cost per event grows with the match, because the moment rules re-run over all events so far. In the local stack's measured run this was still well under the gap between events (see the latency table in [live.md](../methods/live.md)). It is the first thing to make incremental if a match ever falls behind.
- Ticks (clock and score) can be coalesced when the worker is behind: only the latest one matters. Facts are never dropped.
- The control room should show lag. A producer approving a fact that is minutes late is worse than missing it. Routing already times facts from when they are ready, not from match time.

## When the model is slow or down

Recorded latencies from the cached Foundry calls of commit abf6e22 (gpt-5-mini, build-time calls from a laptop; `ms` in `data/agent_cache.json` and `data/reader_cache.json`):

| agent | calls | median | p95 | max |
|---|---|---|---|---|
| Scout, handed mode | 58 | 10.5 s | 16.7 s | 22.0 s |
| Scout, explore mode | 52 | 22.6 s | 28.3 s | 32.8 s |
| Reader | 84 | 3.4 s | 5.4 s | 8.7 s |

The local worker has fact-ready latency in tens of milliseconds because it only replays caches. A live model call adds seconds per fact, so the design treats the model as an optional upgrade, never a dependency:

1. The rule-based draft is computed first and is always available.
2. The Scout gets a deadline (for example 12 s, set in config), and the Reader gets its own. If either times out, fails or returns something the verifier rejects after its one retry, the rule-based draft goes on instead. **Code still decides truth:** whichever text is used, the verifier recomputes every number from the event log.
3. A circuit breaker stops calling Foundry after repeated failures and retries after a pause, so a model outage costs one deadline, not one per fact.
4. Without a Reader result there is no `meaning_matches` check, so Scout text falls back to the rule draft rather than airing unread.
5. Every live call is cached as at build time, so a match can be replayed and audited later.

None of steps 1 to 5 is built for live calls. Today the live worker only replays the caches, and a cache miss falls back to the rule draft.

## Observability

The design uses OpenTelemetry with the Azure Monitor exporter, sending to Application Insights:

- One trace per event. Spans for: the moment rules, each draft (Scout or rules, with model, `ms`, tool calls and a cache hit or miss), each verifier check (name and pass or fail), the routing decision, the render, and the audit append.
- Trace context passed in Kafka message headers (`traceparent`), so the replayer, worker and API join into one trace.
- Metrics: consumer lag, event-to-fact-ready latency (the `scripts/live_latency.py` measure), verifier rejections by check, and model timeouts.

Not built. The slice only has container logs in Log Analytics, including its audit lines (`"audit": true`).

## Identity and secrets

- **Design:** a user-assigned managed identity per service, with Microsoft Entra ID authentication to Event Hubs (Kafka OAUTHBEARER), Redis, Postgres and Foundry. Anything that still needs a secret goes in Key Vault, referenced from Container Apps. No keys in images or in git.
- **Slice:** the identity is used only to pull the image (AcrPull). The Event Hubs connection string and the Redis key are read in Bicep with `listKeys()` and stored as Container App secrets. They are never output, and `deploy.ps1` never prints them. Public network access is on; private endpoints are design only.

## Cost per match (estimate, not measured)

These are **assumptions**, not prices checked in the Azure pricing calculator. Treat them as order-of-magnitude list prices in USD, pay as you go, for one region. Check current prices before deploying. A match is costed at two hours of running resources.

| resource | assumption | per 2-hour match |
|---|---|---|
| Event Hubs Standard, 1 throughput unit | about $0.03 per hour; ingress about 1,300 messages per match, negligible | about $0.06 |
| Worker Container App, 0.5 vCPU and 1 GiB | consumption plan, about $0.000024 per vCPU-second and $0.000003 per GiB-second, before the monthly free grant | about $0.11 |
| Azure Managed Redis, Balanced B0 | assumed about $0.05 per hour | about $0.10 |
| Container Registry Basic, Log Analytics | registry billed per day; a match's logs are well under 1 MB | a few cents |
| *Design only:* API Container App, Web PubSub, Postgres Flexible (burstable), Blob Storage | similar small sizes | about $0.2 more |
| *Design only:* Foundry calls | about 40 Scout and 40 Reader calls per match; tokens per call are not recorded in the caches | unknown; roadmap: record token usage |

Total: roughly **$0.3 for the slice and $0.5 for the full design per match, plus model tokens**. This only holds if the resources exist only around matches. Left running all month, Event Hubs, Redis and the registry cost tens of dollars a month whether or not a match is played. `deploy.ps1` prints the `az group delete` command for that reason.

## Try the slice (not done)

This has never been run. These are the steps it is meant to take:

```powershell
az login
./infra/deploy.ps1                                   # builds Bicep locally, deploys nothing
./infra/deploy.ps1 -WhatIf -ResourceGroup rg-pitchside
./infra/deploy.ps1 -Deploy -ResourceGroup rg-pitchside -Location westeurope   # costs money
# Publish the synthetic match from the laptop to Event Hubs:
$env:PITCHSIDE_BROKERS = '<namespace>.servicebus.windows.net:9093'
$env:PITCHSIDE_KAFKA_SASL_PASSWORD = (az eventhubs namespace authorization-rule keys list `
  -g rg-pitchside --namespace-name <namespace> -n pitchside-live --query primaryConnectionString -o tsv)
python -m live.replayer --speed 20
# The audit lines are in Log Analytics: ContainerAppConsoleLogs_CL | where Log_s has '"audit": true'
az group delete --name rg-pitchside
```

## What is NOT built

- No deployment has ever been made. No Azure numbers (latency, cost, scaling) exist.
- The API, Web PubSub, Postgres or Cosmos DB, Blob Storage, Key Vault, Application Insights and OpenTelemetry tracing, and private networking. All are design only.
- Live Foundry calls with deadlines, the circuit breaker, and token accounting.
- The per-match lease for rebalances.
- Entra ID authentication to Event Hubs, Redis and Postgres. The slice uses keys.
- The slice's stdout audit log is not durable. A restarted worker forgets which facts it recorded, so the design keeps Postgres.
