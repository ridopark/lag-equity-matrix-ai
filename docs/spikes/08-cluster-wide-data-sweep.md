# 08 — Cluster-wide sweep: the second Postgres, and the accumulation rate

**Question:** Spike 07 only examined one Postgres pod. What else is on the
homelab, and are we accumulating more data than 98 fires suggests?
**Date:** 2026-09-03
**Status:** done (read-only)

## What we tried

Enumerated every pod, namespace, PVC and database on the k3s cluster rather than
the single `copytrade/postgres-0` pod spike 07 stopped at. Then measured the
signal accumulation rate directly.

## What we measured

### There is a second Postgres cluster, and spike 07 missed it

`databases/pg-main-1`, managed by the CloudNativePG operator in `cnpg-system`:

| Database | Size |
|---|---|
| `temporal` | **983 MB** |
| `temporal_visibility` | 550 MB |
| `omo`, `agents`, `app`, `postgres` | ~7.6 MB each (empty) |

Temporal has evidently been **migrated off** `copytrade/postgres-0` — the
`temporal` DB still sitting there is 7.8 MB, versus 983 MB here. `audit_log` did
*not* move; it remains live in `copytrade/postgres-0/orchestrator` with rows
through 2026-09-04.

### But Temporal history is not a usable second dataset

`executions_visibility` holds only recent runs — `CopytradeSignalWorkflow`
appears **16 times, all 2026-09-01 to 09-03**. That is Temporal's retention
window, not the true history. The 983 MB in `temporal` is `history_node`:
serialised protobuf blobs, not SQL-queryable in any practical way.

So the 983 MB is real but inaccessible for our purposes, and the visibility table
covers days, not months. No additional fires here.

### Nothing else is accumulating market data

- `monitoring` — Prometheus retention is **7 days**. Useless as history.
- `messaging/nats-0` — JetStream, no streams enumerable; transport, not storage.
- `logging/loki-0` — logs.
- `apps` — `open-webui`, `cookbook-agentic-loop`; unrelated.
- No non-k8s Docker containers on the node.
- `copytrade/market-data` is a **running service**, but it persists nothing to
  Postgres: spike 07 already established that no table anywhere holds an
  underlying price. It is a live quote feed, not an accumulator.

### The number that actually answers the question

Distinct BTO fires per month:

| Month | distinct signals | **BTO** | tickers |
|---|---|---|---|
| 2026-05 | 53 | 1 | 1 |
| 2026-06 | 111 | **30** | 19 |
| 2026-07 | 128 | **36** | 14 |
| 2026-08 | 81 | **29** | 15 |
| 2026-09 (3 days) | 4 | 2 | 3 |

**The feed produces ~30 BTO fires per month, and has done steadily since June.**
May was ramp-up. September is on pace. The system is alive and accumulating.

Projecting forward at 30/month:

| When | Fires | Minimum detectable effect (80% power) |
|---|---|---|
| now | 98 | **28pp** |
| +3 months | 188 | 20pp |
| +6 months | 278 | 17pp |
| +12 months | 458 | 13pp |
| +24 months | 818 | 10pp |

To detect a 15pp effect needs ~348 fires — **8 more months**. A 10pp effect needs
~784 — **23 more months**.

## Conclusion

The instinct that more data exists was right about the cluster and wrong about
the dataset. There *is* a second, larger Postgres — but its 983 MB is Temporal
workflow history in protobuf, with a visibility window of days. No market data is
being retained anywhere: Prometheus keeps 7 days, and the market-data service is
a live feed that persists nothing.

What the sweep did establish is more useful than another table would have been:
**the fire count is not static, it grows at ~30/month.** That reframes the power
problem from a wall into a schedule.

Three consequences:

1. **Waiting is a real strategy, and its price is now known.** Eight months to a
   15pp test, two years to 10pp. That is a legitimate thing to decide about
   rather than discover later.
2. **The test to run now is directional, not conclusive**, and it must be a
   single pre-registered feature (D-22). At 28pp, anything that looks
   "significant" from a search is an artefact.
3. **Widening the feed beats waiting.** 30/month comes from 10 authors on 2
   channels. Adding sources scales the dataset linearly and is the only lever
   that shortens the timeline — worth more than any modelling improvement.
