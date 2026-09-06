# 10 — The second pre-registered test: directionally right, statistically silent

**Question:** With the horizon corrected and the threshold removed (D-33), does
the neighbourhood feature separate winning from losing fires?
**Date:** 2026-09-03
**Status:** done — **inconclusive, and that is the final answer on this dataset**

## What we tried

`scripts/experiment2.py`, executing D-33 exactly. Two declared changes from D-31:
a 2-session forward label instead of 10, and a continuous score split at its own
median instead of a threshold conjunction. Neighbourhood, trailing window,
point-in-time rules and population unchanged. `scripts/experiment.py` was left
untouched so D-31 stays reproducible.

## What we measured

```
evaluable: 67   horizon: 2 sessions
score median (split point): +0.565

base rate (all)                            : 41.8%  n=67
HIGH score (leaders moved, candidate quiet): 45.5%  n=33
LOW  score                                 : 38.2%  n=34

difference               : +7.2%
standard error           : 12.0%
z (UNCORRECTED, 2nd look): +0.60
min detectable (80%)     : 34.2%

mean forward return      : -0.86%   (high +-0.83%, low -0.90%)
```

## What this does and does not say

**It does not say the effect is real.** z = +0.60 is p ≈ 0.55 two-sided before
any correction, and this is the second look at the same 64-67 observations, so
the true evidential value is lower still. A +7.2pp point estimate with a 12.0pp
standard error is a coin landing slightly off-centre.

**It does not say the effect is absent.** The minimum detectable difference is
**34.2pp**. An effect of the size anyone would actually trade — 5 to 10pp — was
never within reach of this sample. Reporting this null as evidence against the
hypothesis would be the same error as reporting the +7.2pp as evidence for it.

**What is mildly encouraging, and no more:** the point estimate has the sign the
hypothesis predicts, and the design fixes worked. The horizon correction lifted
the base rate from 32.8% at 10 sessions to 41.8% at 2 — labelling closer to the
actual holding period measures something less noisy. The median split produced
33/34 buckets, extracting the maximum power the sample can give.

**One confound worth recording rather than chasing:** the base rate is 41.8%,
below a coin flip, and mean forward return is -0.86%. The signal set is 786 calls
to 165 puts — overwhelmingly bullish — so a sub-50% direction-matched hit rate is
what you would expect if these names drifted down over the sample. That is a
property of the period, not necessarily of the signals. Testing it would require
a market-relative label, which is a *third* look at this data and will not be run.

## Conclusion

Two pre-registered tests, both inconclusive, for a reason established before
either was run: **67 observations cannot resolve a 5-10pp effect.** Nothing about
the feature, the graph, or the premise has been learned, and honestly reporting
that is the result.

No third test. The stopping rule was declared in D-33 and holds; a third look at
these same observations would produce a number nobody should believe, including
us.

What has been established instead, and it is not nothing:

- A working, point-in-time, end-to-end evaluation pipeline: 98 fires extracted
  from production, 3,211-symbol universe, correlation neighbourhoods, forward
  labels, and two pre-registered comparisons — all reproducible from three
  scripts.
- Two design errors found and corrected in the open (the 2σ conjunction, the
  DTE-versus-hold-time confusion), with the wrong answers left in the log
  alongside the right ones.
- A schedule rather than a verdict: at ~30 fires/month (spike 08), ~350 fires
  and a 15pp-detectable test arrive around **May 2027**. Re-running then costs
  nothing — the scripts already exist and the pre-registration is already
  written.

The correct posture is to stop analysing and start accumulating. The single
highest-value action remains Q-24: widen the feed, because every added source
moves that May 2027 date closer and nothing else does.
