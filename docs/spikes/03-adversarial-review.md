# 03 — Adversarial review: the case that LagMatrix does not work

**Question:** If this project fails, why?
**Date:** 2026-09-03
**Status:** done (argument, no data — every claim below is falsifiable and none
has been falsified yet, which is itself the finding)

Written deliberately one-sided. Spikes 01 and 02 made the constructive case;
this one attacks. Prospective hindsight — assume it failed, work backwards.

## 1. The alpha is eighteen years old and published in the Journal of Finance

Cohen & Frazzini (2008) reported 1.45%/month. That paper is not obscure — AQR
hosts it on their own site. Every quant shop has customer-supplier linkage in
its factor library. Whatever is left in 2026 is the residual after eighteen
years of arbitrage, and it must survive transaction costs, borrow costs on the
short leg, and capacity constraints.

The design currently treats the effect's existence as settled and worries about
plumbing. That is backwards.

## 2. There is no graph, and building one is the actual project

Every node downstream of `graph_retriever` assumes a leader→lagger topology
exists. `adapters/arango.py` has `upsert_edge` and nothing that produces edges.
Where do they come from?

- **Compustat / SEC segment disclosures** — the academic source. Firms must
  report customers above 10% of revenue. That data is **annual, lagged by
  months, major-customers-only, and US-only.**
- The graph actually imagined in conversation — CoWoS packaging capacity, HBM
  supply, hyperscaler capex — is **nowhere in that data**. It is hand-curated
  domain knowledge, or extracted from prose with an accuracy nobody has measured.

So the richness that makes the demo compelling is the part with no data source,
and the part with a data source is far coarser than the pitch. Q-01 is not one
open question among eleven. **Q-01 is the project**, and it is unstaffed.

## 3. Point-in-time graph — the bias that kills graph backtests

Build today's supply-chain graph, backtest it over 2018-2025, and every edge
encodes knowledge that did not exist at the time. That NVDA depends on CoWoS is
obvious in 2026 and was not in 2019. A backtest over a present-tense graph is
**look-ahead bias wearing a bow tie**, and it will produce a beautiful equity
curve that means nothing.

Doing this correctly requires a point-in-time graph — edges versioned by when the
relationship became publicly known. Almost nobody does this properly. Nothing in
the current design even has a field for it: `LagEdge` has no `as_of`.

## 4. The signal may be factor beta wearing a costume

The graph selects neighbours *because* they are correlated with the leader. So
"leader moved, therefore lagger will move" may be nothing more than the lagger's
loading on a factor the leader also loads on. Buy the semis complex after NVDA
rallies and you have bought beta and labelled it alpha.

Any claim here requires factor-neutralised returns — at minimum market, size,
value, momentum, and an industry control. Nothing in the design does this. Spike
02 identified the same defect in the confluence inversion; it is the same defect
in the forward pipeline, and it was not fixed by inverting.

## 5. The GraphRAG layer may be decoration

Sharp test: **does the LLM plus news retrieval beat `beta × leader_return`?**

`LagEdge` already carries `correlation`, `beta`, `lag_days`. If those predict the
lagger's move, a two-line linear model captures the signal and the entire vector
half, the fusion node, and the analyst node add cost and latency for nothing.

For the LLM layer to earn its place, there must be an identifiable class of cases
where the news *flips the conclusion* the edge weights imply — a shock that is
idiosyncratic rather than propagating, a link that has been severed, a move
already priced. That class is plausible. It has not been articulated, sized, or
tested. Until it is, the most defensible part of the architecture is also the
least justified.

## 6. Evaluation is listed as question seven of eleven; it should be item one

Q-07 asks how we know a signal was right. Without that answer, points 1-5 are
indistinguishable from each other and from success. And the evaluation is itself
treacherous:

- Overlapping 10-day windows are not independent observations; naive t-stats
  will be inflated.
- Point-in-time graph (§3) or the result is meaningless.
- Survivorship bias in the universe — the laggers that went to zero must be in it.
- Multiple-testing across the parameter surface (`shock_sigma`,
  `shock_lookback_days`, `signal_horizon_days`, `max_lag_hops`) — four knobs is
  enough to overfit any daily-bar dataset.

## 7. The work is ordered riskiest-last

Fifteen decisions logged. Twelve concern infrastructure — database, orchestrator,
model, packaging, hooks. The two assumptions that determine whether the project
has a reason to exist — *can we build a credible point-in-time graph* (Q-01) and
*does the signal survive factor-neutral evaluation* (Q-07) — have zero code and
zero data behind them.

The skeleton has seven nodes, four adapters, two storage roles and an LLM, and
not one implemented function. If the answer to Q-01 or Q-07 is unfavourable,
essentially all of it is wasted.

## 8. As a portfolio artefact, sophistication can cut against you

Anyone with a finance background will ask §1, §2, §4 and §6 within ten minutes.
"I didn't test it" turns an impressive architecture into evidence of building
before validating. A smaller project with a real point-in-time backtest and an
honest negative result is a stronger interview than an elaborate pipeline with
an untested premise.

## Conclusion: invert the order of work

The architecture is not the risk. The premise is. A kill-it-fast sequence:

1. **Get any graph.** Hand-code 50 well-known supplier/customer pairs, or pull
   SEC segment data. Ugly is fine. Add `as_of` to `LagEdge` now — retrofitting
   point-in-time later is painful.
2. **Backtest the dumbest version.** Leader daily return → lagger return over
   1-10 days. Factor-neutral. Point-in-time. No graph traversal beyond one hop,
   no LLM, no vector store.
3. **Read the result honestly.** If there is no signal, that is the finding, and
   it cost days rather than months. If there is, *then* ask what the news layer
   adds — and measure that as a delta over step 2, never in isolation.

Only after step 3 does the rest of the skeleton deserve implementation.
