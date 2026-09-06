# 14 — The shipped feature, tested properly, does not predict

**Question:** Q-31 — has the pipeline's actual statistic ever been evaluated?
**Date:** 2026-09-06
**Status:** done

## What we tried

D-31 and D-33 pre-registered and tested `max |neighbour z| - |candidate z|`.
The pipeline computes something else: an independence-weighted, direction-matched
sum of evidence, which is the project's distinctive idea. Q-31 recorded that this
statistic had never been evaluated, so both prior nulls were silent about what
actually ships.

D-63 pre-registered a test of the shipped feature on synthetic candidates —
random (symbol, date) pairs rather than the signal feed — so the test would not
be gated on the 14-month power timeline (D-58) or on one author (D-56).

The evaluator calls the production nodes in order and reads their verdict:
`retrieve_neighbourhood → leader_state → fuse_evidence → assess`. Nothing is
reimplemented; reimplementing is the mistake Q-31 exists to record.

Design fixed before any multi-year bar was fetched: market-excess returns,
standard errors clustered by date, and a 10bp economic threshold declared in
advance so a large panel could not manufacture a result.

## What we measured

Panel: 2,582,435 daily bars, 2,183 non-fund names, 2021-09-02 → 2026-09-04.
Sample: 19,867 evaluable candidate-dates over 1,194 distinct sessions.

| verdict | n | mean 2-session excess | hit rate |
|---|---|---|---|
| corroborated | 3,186 | −2.56 bp | 49.2% |
| neutral | 13,353 | −6.42 bp | 47.2% |
| contradicted | 3,328 | **+2.02 bp** | 48.1% |

Primary test, corroborated − contradicted: **−4.57 bp**, clustered SE 6.39 bp
across 1,110 clusters, z = −0.72. Below the 10bp threshold, not significant, and
pointing the wrong way.

**This null is powered.** 95% CI [−17.1, +7.9] bp; minimum detectable effect at
80% power is 17.9 bp. D-31's was 100.6pp and D-33's 34.2pp — neither could
distinguish anything. This one rules out any effect above roughly 18bp.

### Two checks that changed how much we trust it

**Leakage.** Corrupting every price from the assessment session onward left
25/25 verdicts unchanged, so the feature cannot see the future. Run *before*
reading any result.

**Small samples lie.** A 400-pair smoke run gave corroborated +81bp, contradicted
−34bp, difference **+114.90 bp at z = +3.89** — monotonic across all three groups
and, taken alone, a spectacular result. The full sample reversed it. Same code,
same pre-registered design, 122 rows versus 19,867.

**Survivorship**, declared as a limitation in D-63, does not explain the result.
A bias would decay with recency; the by-year differences instead flip sign
(−88, +12, −24, +48, −0.3, −46 bp for 2021…2026), with three of six individually
past |z|>1.96 in contradictory directions. That is noise, and it also suggests
year-level SEs are understated, since date-clustering does not absorb
autocorrelation between adjacent sessions.

## Conclusion

**The shipped feature has no unconditional predictive content** for 2-session
market-excess returns in liquid US equities, 2021-2026, at effect sizes above
~18bp. Taken with D-59/D-60, which found no intraday lead-lag either, the
mechanism has now failed at both horizons the upstream account actually trades.

What survives, honestly stated: this tested random (symbol, date) pairs, which
are **not** the population the product serves. Real alerts are on names where
something is happening, and the feature could still carry information
*conditional* on an alert-worthy setup. That conditional claim is exactly what
D-31/D-33 were meant to test and are too underpowered to resolve.

So the hypothesis is not dead, but it is much narrower than it was this morning:
not "neighbourhood state predicts returns", only "neighbourhood state predicts
returns for the kind of name a trader has just flagged". That is a weaker,
harder claim, and nothing currently in hand can test it.

Also tested only one horizon (2 sessions), one direction convention, one asset
class, one vendor, and a universe selected on 2026 liquidity.
