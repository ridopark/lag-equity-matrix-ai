# 13 — Widening the signal feed: what the 30/month is actually made of

**Question:** Q-24 — can the signal feed be widened beyond 10 authors / 2
channels, and does that scale the dataset linearly as spike 08 assumed?
**Date:** 2026-09-06
**Status:** done (read-only)

## What we tried

Spike 08 established the accumulation rate (~30 BTO fires/month) and concluded
that "widening the feed beats waiting… adding sources scales the dataset
linearly." It never looked at how the 30/month is distributed across the ten
authors. This spike does that, and reads the upstream ingestion config to
establish what widening would actually involve.

Read-only throughout: `SELECT` against `copytrade/postgres-0/orchestrator` over
SSH, plus a read of `oh-my-tradeagent/services/signal-source-discord`.

## What we measured

### The feed is one person

Distinct BTO fires per author, all time:

| author | BTO | STC | tickers | first | last |
|---|---|---|---|---|---|
| **TradingTheTrend** | **78** | **186** | 19 | 2026-06-01 | 2026-09-04 |
| TB22 | 6 | 25 | 4 | 2026-07-01 | 2026-09-04 |
| Lars | 5 | 17 | 5 | 2026-06-29 | 2026-09-03 |
| beendoubleyou | 3 | 5 | 1 | 2026-05-29 | 2026-06-18 |
| `tenant:prod_real:…` | 3 | 0 | 3 | 2026-08-11 | 2026-08-25 |
| sloth legooman NR100 | 2 | 2 | 2 | 2026-06-24 | 2026-08-19 |
| Shakira T | 2 | 1 | 1 | 2026-08-18 | 2026-08-18 |
| ridopark | 1 | 0 | 1 | 2026-06-29 | 2026-06-29 |
| Donald T | 1 | 2 | 1 | 2026-05-29 | 2026-05-29 |
| `tenant:staging_paper:…` | 1 | 0 | 1 | 2026-08-11 | 2026-08-11 |

**TradingTheTrend is 78 of 102 BTO fires — 76%** — and 186 of 238 STCs (78%).
The same concentration holds in both directions, so it is a property of the
author, not of one action type.

Three of the ten "authors" are not external signal sources at all:
`tenant:prod_real:…`, `tenant:staging_paper:…` and `ridopark` are the system and
its owner, five fires between them.

Month by month, the top author against everyone else combined:

| month | TradingTheTrend | all others | total |
|---|---|---|---|
| 2026-05 | 0 | 2 | 2 |
| 2026-06 | 24 | 5 | 29 |
| 2026-07 | 32 | 4 | 36 |
| 2026-08 | 19 | 10 | 29 |
| 2026-09 (4d) | 3 | 3 | 6 |

The steady ~30/month is ~25 from one account and ~5 from the other nine.

### Widening is mechanically trivial

`services/signal-source-discord` runs one sidecar per channel, taking a single
`DISCORD_CHANNEL_URL` / `DISCORD_OPTIONS_CHAT_CHANNEL_URL` env var
(`chat_main.py:64`, `bootstrap.py:110`). "2 channels" means two deployed
sidecars. There is **no author allowlist or denylist anywhere** in the service —
every author posting a parseable alert in a watched channel is already ingested.

So adding a channel is a deployment with one environment variable. Nothing needs
to be written, and no filter needs relaxing.

### What is in the table besides BTO

| action | distinct signals |
|---|---|
| STC | 238 |
| BTO | 102 |
| *(none)* | 52 |
| AVG | 3 |

The 52 keyless rows are all 2026-05-18 → 2026-05-29 and carry a `signal_id` and
nothing else — an early payload format, before the ramp. Not recoverable.

STC outnumbers BTO 2.3:1, but sells are exits, not directional entries. They do
not extend the candidate set for a corroboration test, though they are the
natural source for a realised-holding-period measurement (Q-25).

### The feed is live

The committed extract holds 98 fires through 2026-09-03; the table now has 102
through 2026-09-04. The four-fire gap is accumulation, not a discrepancy.

## Conclusion

**Spike 08's linearity claim is wrong, and the error mattered.** "Adding sources
scales the dataset linearly" assumed ten roughly comparable authors. The
distribution is Pareto-extreme: one author is three quarters of the feed, and the
median contributing author produces about two fires per *quarter*. Adding a
channel does not add 3 fires/month per author; it adds an unknown draw from a
distribution where almost all the mass sits in a rare, prolific alerter.

What that does to the timeline. Reaching a 15pp-detectable test needs ~348 fires,
250 more than today:

| added source | fires/month | months to 348 |
|---|---|---|
| none (today) | 30 | 8.3 |
| ten more median authors | ~47 | 5.3 |
| **one more TradingTheTrend-class account** | ~55 | **4.5** |

One prolific source is worth roughly ten typical ones. So the action Q-24 implies
is not "watch more channels" but "find the channels where a high-volume alerter
posts" — a targeting problem, not a scaling one.

**A second finding, which matters more than the volume.** The evaluation set is
effectively one trader. Any result from D-31 or D-33 therefore generalises to
TradingTheTrend's selection style, not to "options alerts" — a
single-source validity limit that no amount of waiting fixes, because waiting
accumulates more of the same author. Widening remains the right call, but the
strongest argument for it is independence of sources, not sample size. This is
now Q-28.

Both conclusions are about *where* to widen. The mechanism itself is a one-line
deployment, and that half of Q-24 is a clean yes.
