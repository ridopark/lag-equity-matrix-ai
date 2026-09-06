# 07 — Full sweep of the homelab Postgres, and a correction to spike 05

**Question:** What else is in the database beyond `audit_log`, and does spike
05's fire count survive scrutiny?
**Date:** 2026-09-03
**Status:** done (read-only; aggregate queries only, no row dumps, no writes)

## What we tried

Enumerated every database, every table, every row count. Then chased two things:
any table carrying market state at signal time, and whether `count(*)` on
`audit_log` actually counts decisions.

## What we measured

### The correction: rows are not fires

Spike 05 reported 284 BTO fires from `count(*)`. `audit_log` records the **same
alert once per tenant, and there are 6 tenants.**

| Measure | Rows | Distinct `signal_id` |
|---|---|---|
| SignalReceived | 1,003 | **369** |
| — BTO only | 284 | **98** |
| EntryFilled | 251 | 60 |
| PositionClosed | 615 | 82 (by `entry_signal_id`) |

**The real fire count is 98, not 284.** Detectable effect at 80% power moves from
~17pp to **~28pp**. That is the single most consequential number in the project
and it was wrong by 3x for one turn.

Two joins also fail the obvious way: `PartialExitFilled.signal_id` is the *exit*
signal's id, not the entry's — joining exits to entries requires
`PositionClosed.entry_signal_id`. And `WatchlistExitMeasured` (91 rows, with
`premium_mae`/`premium_mfe`) carries **no `signal_id` at all**, so it cannot be
attributed to a fire without another key.

### The database inventory

| DB | Size | Notable tables |
|---|---|---|
| `orchestrator` | 301 MB | `audit_log` 386,562 rows · `option_symbol_cache` 46 |
| `dashboard` | 182 MB | `options_chat_attachment` 1,175 (172 MB) · `options_chat_message` 4,687 · `trade_context` 31 |
| `exec_alpaca_paper` / `_live` / `exec_tradier_paper` | ~8 MB each | `order_intent_journal` |
| `temporal` | 7.8 MB | workflow engine internals |

### `trade_context` — right shape, wrong size

31 rows, 2026-08-16 → 2026-09-02 (~3 weeks). But the schema is precisely what
this project's evaluation wants:

`entry_premium, entry_bid, entry_ask, entry_spread, entry_iv, entry_delta,
entry_gamma, entry_theta, entry_vega, underlying_spot, dte, moneyness,
mfe_premium, mae_premium, exit_bid, exit_iv, realized_pnl, exit_reason,
hold_minutes, alert_to_fill_latency_ms, slippage_vs_alert_pct`

Two caveats. It is **new** — three weeks, versus `audit_log`'s three and a half
months. And `realized_pnl` is **populated on zero rows** of 31, open or closed;
the column exists, nothing fills it. `underlying_spot` and the MFE/MAE columns
*are* populated on all 31.

Its real value now is as a **schema template**: it proves the platform already
knows how to capture underlying spot and Greeks at entry. If it backfills or
keeps growing it becomes the evaluation table outright.

### Underlying spot is not captured anywhere else

Scanning every `audit_log` kind for price-bearing fields returns only **option
premium** — `SignalReceived.price` (alert premium), `OrderSubmitted.ask` /
`limit_price`, `EntryFilled.avg_fill_price`, `PositionEntered.entry_premium`,
`ChandelierTrailFired.peak_premium`. Nothing holds the underlying's price.

This is fine, and arguably better: the underlying series comes from Alpaca
(D-16), which we control and can slice point-in-time. But it means the evaluation
must fetch it rather than read it, and it settles part of Q-21 — the *only*
outcome we can compute for all 98 fires is the **underlying's** forward return,
because option P&L exists only for the ~60 that filled.

That decouples the evaluation from execution entirely, which is a benefit:
conditioning on fills would bias the sample toward signals the execution system
happened to like.

### `options_chat_message` — a corpus, but a thin one

4,687 messages, **105 authors**, 2 channels, 2026-08-11 → 2026-09-04, mean
content length **50 characters**, 1,487 replies. Plus `options_chat_embed` (25)
and `options_chat_attachment` (1,175 rows / 172 MB, images).

Assessment: weaker than it first looks. It covers **3.5 weeks** against the
signal history's 3.5 months, so most fires have no chat context at all. At 50
characters average these are quick chat lines, not analysis. And 105 authors
versus 10 signal authors means the bulk is community chatter, not the providers'
reasoning.

Worth revisiting only if the capture window extends backwards; not a basis for
the semantic layer today.

## Conclusion

The mine produced one correction that matters more than everything else found:
**98 fires, not 284.** At ~28pp minimum detectable effect, a single
pre-registered feature (D-22) is the only defensible experiment; a feature search
would be guaranteed to produce a false positive.

Three secondary findings:

1. **Evaluate on the underlying's forward return, not option P&L** — it is the
   only label computable for all 98, and it avoids conditioning on fills. This
   largely settles Q-21.
2. **`trade_context` is the schema to grow toward**, not to use yet — 31 rows,
   three weeks, `realized_pnl` unpopulated.
3. **The chat corpus is not yet usable** — three weeks against three and a half
   months of signals, 50-character messages, mostly non-provider authors.

The honest summary of the dataset: ~98 real fires in one regime over 3.5 months,
enough for a directional read and a pre-registered test, not enough to tune
anything.
