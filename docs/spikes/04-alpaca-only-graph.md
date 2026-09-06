# 04 — Constraining to Alpaca: what dies, and what it accidentally fixes

**Question:** If every input must come from the Alpaca API, can the leader→lagger
graph exist at all (Q-01)?
**Date:** 2026-09-03
**Status:** done (API capability research; no data pulled, nothing measured)

## What we tried

Read Alpaca's Market Data, News, and Corporate Actions API surfaces against the
data the design assumes. Checked free-tier reach specifically, since a portfolio
project that needs a paid feed to reproduce is a worse portfolio project.

## What we measured

### What Alpaca gives us

| Need | Available | Detail |
|---|---|---|
| Daily bars | **Yes** | ~7 years. Free tier reaches the **SIP** feed (all US exchanges) for historical queries — the free restriction is `end` must be ≥15 min old, which daily bars always are. IEX-only (~2.5% of volume) applies to *real-time*, not to our use. |
| Split/dividend adjustment | **Yes** | `adjustment` parameter: `raw` / `split` / `dividend` / `spin-off` / `all`. Correct return computation is a query parameter, not a project. |
| News with ticker tags | **Yes** | Benzinga-sourced, back to **2015** (~11 years). Each article carries a **`symbols`** field listing the tickers it references, plus headline, summary, content, timestamps. |
| Corporate actions | Partial | Dividends, mergers, spin-offs, splits — but only from **April 2020**. Mostly redundant given `adjustment=all` on bars. |
| **Supply-chain / customer-supplier links** | **No** | Nothing. No Compustat segment data, no SEC customer disclosure, no fundamentals of that kind. |

### What dies

**The supply-chain graph as designed.** `SUPPLIES_TO (TSM -> NVDA)` cannot be
built from Alpaca. The academic source (Compustat/SEC segment disclosure) is not
in the product, and neither is any substitute. The `relation` field on `LagEdge`
cannot be populated from the primary data source.

That is the single biggest consequence and it removes the most narratively
appealing part of the original pitch.

### What survives — two edge sources, both Alpaca-native

**1. Statistical lead-lag edges, from daily bars.** Rolling cross-correlation of
returns at lag k over a trailing window. Populates `correlation`, `beta`,
`lag_days` directly. Needs only `/v2/stocks/bars` with `adjustment=all`.

**2. News co-mention edges, from the News API `symbols` field.** Two tickers
appearing in the same article is a linkage observation, timestamped. There is
literature on exactly this — news co-mention momentum spillover — so it is not
an invented metric.

### The accidental fix: point-in-time comes free

Spike 03 §3 argued that backtesting a present-tense graph is look-ahead bias and
would invalidate every result. **Both Alpaca-native edge sources are
point-in-time by construction:**

- A statistical edge computed on a trailing window at date *t* uses only bars
  dated ≤ *t*.
- A co-mention edge is stamped with the article's publication time.

There is no "today's graph" to leak backwards, because the graph is *recomputed
from timestamped observations at every point in the backtest*. The bias that was
going to be hardest to fix is eliminated by the data constraint rather than by
engineering. Q-13 (`as_of` on `LagEdge`) becomes a natural field rather than a
retrofit.

### And a partial fix for "the LLM is decoration"

Spike 03 §5 argued the GraphRAG layer might add nothing over `beta ×
leader_return`. Under the Alpaca constraint the LLM gets a job a linear model
provably cannot do: **read the co-mention article and label the relationship** —
supplier, customer, competitor, shared-input, same-sector-noise. That recovers
the semantic edge types lost with Compustat, from Alpaca data alone.

This moves the LLM from *signal generation* (where it competes with a two-line
regression and probably loses) to *graph construction* (where nothing else
competes). That is a much more defensible position for it.

### Honest caveats

- **Benzinga tag quality is unmeasured.** How complete and accurate is `symbols`?
  Unknown. Directly determines edge quality. Must be sampled.
- **Sector articles create hub noise.** A "chip stocks rally" piece tagging 25
  tickers generates 300 spurious pairs. Needs a breadth filter — drop or
  down-weight articles above N symbols. This is a real design parameter, and
  another knob that can be overfit (spike 03 §6).
- **Statistical edges do not escape the factor critique.** Spike 03 §4 stands:
  an edge *defined* by return correlation is a correlation exposure. Co-mention
  edges partly escape it — they measure information linkage, not return
  co-movement — which makes the comparison between the two edge types an
  experiment worth running rather than a detail.
- **History bounds the backtest**: ~7 years of bars, ~11 years of news. Enough,
  but it caps how many independent windows exist.

## Conclusion

The constraint is net positive. It costs the supply-chain narrative and buys
correctness, reproducibility, and a single vendor.

Revised plan for the kill-it-fast sequence in spike 03:

1. Pull daily bars (`adjustment=all`) for a few hundred liquid names, 7 years.
2. Build statistical lead-lag edges on trailing windows. Point-in-time by
   construction.
3. Factor-neutral backtest of leader return → lagger return over 1-10 days.
   **Still no news, no LLM, no vector store.**
4. If and only if there is signal: add co-mention edges and measure the delta.
   Then add LLM relationship labelling and measure that delta.

Each step is a measurable increment over the one before, and the project can be
abandoned honestly after any of them.
