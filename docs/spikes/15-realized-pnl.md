# 15 — What the signal feed actually earned

**Question:** Does the upstream signal make money? Never asked before.
**Date:** 2026-09-07
**Status:** done (read-only)

## What we tried

Every experiment in this project tested whether the *graph feature* predicts.
None tested whether the *signal* does. The evidence was in `audit_log` the whole
time: `EntryFilled` and `PartialExitFilled` both carry `avg_fill_price` and a
quantity, joinable per contract.

Three things had to be established by looking before computing:

- **Tenants are separate accounts, not duplicated rows.** `staging_paper` (142
  fills), `dev`, `paper_jinchiul` are paper; `prod_real` (50), `prod-kipark`
  (29), `prod-jinchul` (19) are real money. Pooling them reports paper fills as
  trading performance.
- **Exits are partial and often incomplete** — 11 contracts in, 6 + 3 out. P&L
  is computed on the matched quantity only.
- `filled_qty` / `qty_filled` are contracts; the option multiplier is 100.

## What we measured

**77 real-money round-trips**, median hold 22.4 hours.

| | real money | paper |
|---|---|---|
| trades | 77 | 140 |
| total P&L | **+$37,586** | +$21,118 |
| mean return | +5.9% | +3.9% |
| median return | +7.9% | −3.7% |
| win rate | 68.8% | 45.7% |
| avg win / avg loss | +24.1% / −34.2% | +42.1% / −28.3% |
| payoff ratio | **0.70** | 1.49 |

Profitable — but three findings qualify it heavily.

### It is five trades from breakeven

| | P&L | without them |
|---|---|---|
| top 1 | $12,031 (32%) | $25,555 |
| top 3 | $32,148 (**86%**) | $5,438 |
| top 5 | $43,220 (**115%**) | **−$5,634** |
| top 10 | $57,001 (152%) | −$19,415 |

The five: NVDA +88%, MU +101%, NVDA +91%, MU +79%, AMD +86%. All semiconductors,
all ~+80-100% moves, on positions 2.1x typical size. The concentration is mostly
*return*, not sizing.

### It is not distinguishable from zero

Mean +5.9%, sd 39.4%, n=77, SE 4.5%, **t = +1.31**, 95% CI [−2.9%, +14.7%].
With a 39% per-trade standard deviation, 77 trades cannot separate this from
luck. The payoff ratio of 0.70 means profitability depends entirely on the 68.8%
win rate holding — losses are already larger than wins.

### Index ETFs lose systematically

| | n | mean | win rate | P&L |
|---|---|---|---|---|
| SPY / QQQ | 30 | **−5.9%** | 60.0% | **−$6,867** |
| single names | 47 | +13.4% | 74.5% | +$44,454 |

This is the one pattern *not* driven by outliers: 30 trades, 39% of all activity,
a positive win rate and a negative mean — wins too small to cover losses. It is
also the highest-frequency activity in the feed.

## Conclusion

The feed is profitable in aggregate and unproven statistically. The honest
summary is that it earned $37.6k, that $43.2k of it came from five semiconductor
trades, and that 77 observations at 39% dispersion cannot tell skill from
variance either way.

One actionable result stands on its own evidence rather than on outliers:
**the index-ETF trades lose money over 30 trades**, and dropping them would have
raised total P&L from $37,586 to $44,454. That is a systematic pattern, not a
tail event.

Also worth noting against the rest of the project: D-33 measured the alerts'
*underlying* 2-session return at −0.86% with a 41.8% hit rate. The options are
profitable where the underlying is not — leverage plus a 22-hour median hold.
Any evaluation of this signal on underlying returns, which is what D-29 chose,
measures something other than what the account actually earns.
