# 09 — The pre-registered test: null, and why it is uninformative

**Question:** Does the D-31 feature separate winning from losing fires?
**Date:** 2026-09-03
**Status:** done — **result is a null, but the test was mis-specified. Treat this
as a pilot that calibrated the design, not as evidence about the world.**

## What we tried

Executed D-31 exactly as written, without deviation:
`scripts/extract_fires.py` -> `scripts/fetch_bars.py` -> `scripts/experiment.py`.
98 fires extracted, 74 single names, bars for 3,211 symbols over 2026-01-20 to
2026-09-03 (`adjustment=all`), point-in-time throughout — every trailing window
ends the session before the fire, and the entry session is the first session
strictly after the alert.

## What we measured

```
population (single names, D-31): 74
  skipped, no_bars: 3          (DRAM has no Alpaca coverage)
  skipped, short_history: 2
  skipped, no_forward: 5       (fired too recently for a 10-session label)
  evaluable: 64

base rate (all)      : 32.8%  n=64
feature FIRED        : 50.0%  n=2
feature not fired    : 32.3%  n=62
difference           : +17.7%   SE 35.9%   z = +0.49
min detectable (80%) : 100.6%
mean forward return  : -2.28%
```

**The feature fired twice in sixty-four fires.** With n=2 in one arm the minimum
detectable difference is 100.6% — the test could not have detected anything. The
+17.7% difference is one extra winning trade out of two.

This is not a finding about lead-lag. It is a finding about the pre-registration.

## Two mis-specifications, one of them serious

### 1. The feature was far too restrictive

Requiring a neighbour move >= 2 sigma **and** the candidate under 0.5 sigma in
the same three sessions is a narrow conjunction. At a 3% firing rate the design
had no chance regardless of the underlying truth. This was foreseeable from the
specification alone and was not foreseen.

### 2. The label horizon was wrong — and this invalidates an earlier answer

Spike 05 closed Q-17 ("does the signal's horizon match the 1-10 day window?")
using **days-to-expiry**, median 8. That was the wrong field. DTE is when the
*option* expires, not how long the position is *held*.

`trade_context.hold_minutes`, on the 20 closed trades that carry it:

| min | median | p90 | max | mean DTE |
|---|---|---|---|---|
| 45 min | **1,334 min (~22 h)** | 2,979 min (~2 sessions) | 23,145 min (~16 d) | 26.7 |

**The median hold is about one trading day.** The test labelled outcomes over
**ten** sessions. We measured a ten-day outcome for a one-day trade.

That also puts the 32.8% base rate and the -2.28% mean forward return in
context: neither describes how these signals actually perform, because neither
is measured over the period they are held.

## Conclusion

The pre-registered test returns a null and the null carries no information. Both
causes are design errors made before the data was seen, which is precisely the
risk pre-registration accepts in exchange for credibility: you commit while you
know least, and sometimes you commit to something that cannot answer.

What the run did buy, and it is not nothing:

- **The pipeline works end to end**, point-in-time, on real data — extraction,
  3,211-symbol universe, correlation neighbourhoods, forward labels.
- **A firing rate**, so a second feature can be specified to fire at a usable
  frequency instead of guessed at.
- **The horizon correction**, which is the most consequential thing found and
  came from `trade_context`, not from the outcome data.

**Honest status of the evidence:** this is a **pilot**, not a test. A second
pre-registration is warranted, but it is informed by having seen a firing rate,
so it is a second look at the same 64 observations and must be discounted
accordingly. Two tests on one small sample is already most of a multiple-testing
problem; a third would end the exercise's credibility.

The horizon fix is separately defensible — it comes from `hold_minutes`, an
input, not from the results — so correcting it is not peeking. Loosening the
feature threshold *is* informed by the results, and that is the part to declare.
