# 05 — oh-my-tradeagent's audit_log as the upstream signal history

**Question:** Can the homelab Postgres behind oh-my-tradeagent serve as the fire
history the evaluation needs (Q-16), and does that make replay (Q-18) moot?
**Date:** 2026-09-03
**Status:** done — **but its headline number is wrong. See the correction below
and spike 07.**

> ### ⚠ Correction (2026-09-03, spike 07)
>
> This spike reported **284 BTO fires**. That was `count(*)` over `audit_log`
> rows. The table logs the same alert **once per tenant, and there are 6
> tenants**, so rows are not fires.
>
> `count(DISTINCT subject->>'signal_id')` gives the true figures:
> **369 distinct signals, of which 98 are BTO** — not 284. Of those, 60 reached
> an `EntryFilled`.
>
> Power consequence: ~49 per bucket detects roughly **28pp** at 80% power, not
> the ~17-19pp claimed below. Everything in the Conclusion that depends on the
> 284 figure is overstated by ~3x. The qualitative findings — options alerts,
> 10 authors, median 8 DTE, 26-ticker universe, derivable outcomes — are
> unaffected.

## What we tried

Reached the homelab k3s cluster over SSH (`ridopark@192.168.10.123`) and ran
read-only `SELECT`s via `kubectl -n copytrade exec postgres-0 -- psql -U temporal
-d orchestrator`. Only aggregate queries — no row dumps, no writes, no schema
changes. The local `infra/docker-compose.yml` Postgres was checked first and is
not running; production lives on the homelab.

Deliberately not done: computing actual P&L, joining to market data, or any
assessment of signal quality. This spike establishes only whether the dataset
exists and what shape it is.

## What we measured

### The signal is third-party options alerts, not an algorithm

`audit_log` (`orchestrator` DB) holds the event stream:
`SignalReceived → SignalAccepted → OrderSubmitted → EntryFilled → ExitRequested
→ PartialExitFilled → PositionClosed`, correlated by `signal_id`.

The `SignalReceived` subject carries `ticker, action, strike, expiry, right,
price, posted_at, author, signal_id` — **options alerts posted by humans**
(10 distinct authors), parsed from a feed. There is no scoring function.

### Volume — far more than we assumed

| | Count | Range |
|---|---|---|
| SignalReceived | **1,003** | 2026-05-18 → 2026-09-03 |
| — of which **BTO** (entries, the real fires) | **284** | |
| — STC (exits) | 659 | |
| SignalAccepted | 213 | |
| EntryFilled | 251 | |
| PositionClosed | 615 | |

Q-16 was answered "a few dozen." It is **284 entry signals**, ~5-10x that.

### Outcomes are derivable

`EntryFilled` carries `avg_fill_price, filled_qty, option_symbol, signal_id`;
`PartialExitFilled` carries `avg_fill_price, qty_filled, signal_id`;
`PositionClosed` carries `entry_signal_id`. No stored P&L column, but realised
per-signal P&L is computable from the fills. Every fire is labellable.

### Horizon matches D-15 at the median

Days-to-expiry at signal time, BTO only (n=284):

| min | median | p90 | max |
|---|---|---|---|
| **0** | **8** | 51 | 372 |

A median of 8 DTE sits squarely inside the 1-10 trading-day window D-15 targets.
The tail is wide — some 0DTE, some LEAPS — but the centre of the distribution is
a good match. Q-17 answered favourably.

### The universe is the problem

26 distinct tickers. Ranked by signal count:

```
NVDA 154 · QQQ 106 · SPY 105 · AAPL 82 · AMZN 67 · GOOGL 55 · TSLA 50 · MU 50
MSFT 46 · INTC 37 · NOW 28 · SPCX 27 · HOOD 26 · DRAM 21 · AMD 20 · IREN 16
BAC 12 · META 9 · PG 9 · PLTR 8 · PFE 8 · SMCI 6 · AVGO 5 · RDDT 2 · NFLX 1 · CRWV 1
```

Two problems, both structural:

1. **QQQ and SPY are 211 of 1,003 signals (~21%).** An index ETF has no
   leader/lagger neighbourhood — it *is* the aggregate. The graph layer is not
   merely weak on these candidates, it is inapplicable.
2. **The rest is concentrated, mutually-correlated mega-cap tech.** NVDA, AAPL,
   AMZN, GOOGL, MSFT, META, AMD, AVGO, MU, INTC, SMCI, PLTR all load on one
   AI-capex factor. This is spike 02's confluence trap in its purest form: the
   neighbourhood of NVDA *inside this universe* is a set of names that move
   together, so effective independent evidence will be close to 1, not 7.

The mitigation, and it is real: **the 26 tickers constrain the candidates, not
the neighbourhood.** The graph may traverse to any Alpaca-covered name — TSM,
ASML, Vertiv, SK Hynix ADRs, suppliers absent from the alert feed. Cross-
sectional value survives as long as the neighbourhood is drawn from the wide
universe rather than the signal set. That must be an explicit design rule, not
an accident.

## Conclusion

**Q-18 is moot rather than answered.** Replay was only ever needed because we
believed the fire history was a few dozen. It is 284 labelled entries, observed
rather than reconstructed — which is strictly better evidence than a replay
would have been, since these are real decisions with real fills. The signal
*cannot* be replayed (it is human Discord posts, not a function), and it no
longer needs to be. D-20 is superseded.

**Orthogonality (D-21, Q-15) goes from plausible to near-certain.** The upstream
signal is a human's opinion posted to a feed. It shares no inputs whatsoever with
a graph feature computed from bars and co-mention news. The measurement is still
worth running, but the risk that this layer merely re-measures its own input has
largely evaporated — this is the best possible case for the corroboration thesis.

**Three new problems, in order of severity:**

1. **The outcome label is options P&L, not equity return** (Q-21). A 2% move in
   the underlying is +40% or -100% on the contract depending on strike, DTE and
   IV. Testing the graph hypothesis cleanly probably means labelling on the
   *underlying's* forward return over the horizon and treating option P&L as a
   separate, downstream question. That is a real fork in Q-07.
2. **21% of candidates are index ETFs the graph cannot serve** (Q-20). They
   should be excluded up front, which cuts the usable set from 284 toward ~215.
3. **3.5 months, one regime.** May-September 2026 only. No regime diversity, and
   284 fires in a single market environment supports far weaker conclusions than
   284 fires spread across several.

Power, honestly: ~215 usable fires split into corroborated/contradicted gives
~107 per bucket, which detects roughly a 19-percentage-point difference at 80%
power. Better than the ~40pp we faced before, still not enough for a 10pp effect.
Pre-committing to one feature (D-22) is therefore not optional.
