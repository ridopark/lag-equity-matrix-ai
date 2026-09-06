# 06 — ETFs as candidates: reversing Q-20

**Question:** Should index-ETF candidates (QQQ, SPY — 21% of the signal set) be
excluded, as spike 05 recommended?
**Date:** 2026-09-03
**Status:** done (literature + API capability research; nothing measured)

## What we tried

Attacked spike 05's own recommendation. Three lines: which side leads in
ETF/constituent price discovery, whether any cross-sectional feature on an index
is documented as predictive, and whether the constituent data is obtainable under
the Alpaca-only constraint (D-16).

## What we measured

### Where spike 05 was right

For SPY and QQQ the weighted constituent sum *is* the index, arbitraged within
milliseconds. So the single-name feature — "my leader already moved and I have
not repriced yet" — is mechanically near-zero on an index ETF. There is no
diffusion lag to harvest. That part stands.

### Where spike 05 was wrong

It concluded "therefore no neighbourhood feature applies." That is a
non-sequitur. The correct conclusion is that **an ETF needs a different
neighbourhood feature**, not none.

**Market breadth is a documented, factor-controlled cross-sectional signal.** A
zero-investment strategy long equal-weighted indices with the highest breadth and
short the lowest returns a mean **1.19% per month** (0.76% for industries), and
the effect survives controls for size, value and skewness (*Herding for profits:
market breadth and the cross-section of global equity returns*, Journal of
Economic Behavior & Organization).

The point that matters for LagMatrix: **breadth cannot be computed from the ETF's
own price.** A 1% QQQ move carried by NVDA alone is a different state from a 1%
move with 80% of constituents participating, and the two are indistinguishable
from the index series. Separating them requires exactly the constituent-graph
traversal this project already has.

So for an ETF the question is not *"did the neighbourhood move"* — it is
**"how many independent things moved?"** Which is the effective-independent-
evidence computation from Q-12, applied where it is most natural. Arguably ETFs
are a *better* fit for the confluence-discounting thesis than single names,
because on an index the naive-confluence failure mode is the whole phenomenon
being measured rather than a bias to correct for.

### Price discovery direction is an empirical question, not a settled one

"Results reveal heterogeneous price discovery patterns across ETFs" — direction
varies by ETF and by conditions. And from the microstructure literature already
gathered in spike 01, nonsynchronous effects are "dramatically stronger in IWM
compared to SPY," so less-liquid ETFs carry more structure than the megacap
proxies. Nothing here supports a blanket exclusion.

### The real cost: Alpaca has no holdings endpoint

Alpaca trades 11,000+ stocks and ETFs but exposes **no constituent/holdings
data**. Its Assets endpoint identifies instruments, it does not decompose them.
This is a genuine conflict with D-16 (Alpaca as sole source) and the first such
conflict found.

Mitigation, and it is cheap: exact holdings are not required. QQQ's top ten are
roughly half the index and are already in the signal universe (NVDA, AAPL, MSFT,
AMZN, GOOGL, META, AVGO, TSLA). A small static weights file covering two or three
ETFs is on the order of a hundred lines.

Point-in-time still bites, but far more gently than for a supply-chain graph:
membership of megacap names changes rarely, and **equal-weighted breadth (the
fraction of constituents up) is largely insensitive to weight drift** in a way
that cap-weighted contribution is not. Preferring breadth measures over
weight-exact decomposition keeps the look-ahead exposure small.

## Conclusion

**Reverse Q-20. Keep ETF candidates**, with a distinct feature path:

| Candidate type | Neighbourhood question | Mechanism |
|---|---|---|
| Single name | Has my leader moved while I have not repriced? | Information diffusion |
| Index ETF | Is this move broad or carried by a few names? | Breadth / dispersion |

Both are neighbourhood computations over a graph; both need the same machinery;
they are different features and must not share a code path or a threshold.

This recovers 211 of 1,003 signals — the usable set goes back toward 284 rather
than ~215 — which matters directly, since spike 05 established we are power-
constrained. Excluding the second and third most-signalled tickers to avoid
thinking about them would have been the expensive kind of tidy.

One clarification so D-27 is not misapplied: it requires single-name
neighbourhoods to be drawn from the wide Alpaca universe rather than the 26-ticker
signal set. For an ETF the constituents *are* the correct neighbourhood by
definition, even though they overlap the signal set. That is not the failure mode
D-27 guards against.
