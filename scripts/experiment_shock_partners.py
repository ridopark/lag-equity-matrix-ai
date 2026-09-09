"""D-99: after a shock, do the shocked name's co-movement partners move next day?

Run exactly as pre-registered (docs/spikes/overall.md D-99, commit ee47915),
written before this script existed. Read-only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from lagmatrix import shocks as S

TRAIL_SHOCK, MW, SIG = 60, 3, 2.0
TRAIL_CORR, MIN_CORR = 250, 0.5
# A symbol whose trailing volatility is under 0.1%/day is not trading -- halted,
# stale, or pre-listing padding. EVER sat at vol 3.7e-11 for stretches of 2018,
# so market-excess alone gave it a "response" of 593 million sigma. `sd > 0` is
# not a sufficient guard; a floor is.
MIN_VOL = 1e-3
THRESHOLD = 0.10                      # D-99, declared in advance
COVERAGE = 0.90


def main() -> None:
    bars = pd.read_parquet("data/bars-10y.parquet")
    closes = bars.pivot_table(index="timestamp", columns="symbol", values="close")
    rets = closes.pct_change()
    idx = list(closes.index)
    rets = rets.mask(rets.abs() > 10.0)   # D-100, before anything derives from it
    mkt = rets.mean(axis=1)
    vol = rets.rolling(TRAIL_SHOCK).std()
    rng = np.random.default_rng(0)

    rows = []
    start = TRAIL_CORR + MW
    for ti in range(start, len(idx) - 2, MW):          # non-overlapping episodes
        base = rets.iloc[ti - MW - TRAIL_SHOCK:ti - MW]
        rec = rets.iloc[ti - MW:ti]
        cols = [c for c in closes.columns
                if rec[c].notna().all() and base[c].notna().all()]
        if len(cols) < 50:
            continue
        z = S.standardised_moves(rec, cols, MW, base)
        shocked = {k: v for k, v in z.items() if abs(v) >= SIG}
        if not shocked:
            continue

        # correlation window ends strictly before the episode (D-16)
        win = rets.iloc[ti - MW - TRAIL_CORR:ti - MW]
        ok = win.notna().mean() >= COVERAGE
        w = win.loc[:, ok]
        w = w.sub(w.mean(axis=1), axis=0)               # market-excess
        zw = ((w - w.mean()) / w.std(ddof=0)).fillna(0.0)
        syms = list(zw.columns)
        pos = {s: i for i, s in enumerate(syms)}
        M = zw.to_numpy()
        C = (M.T @ M) / len(M)

        nxt = rets.iloc[ti] - mkt.iloc[ti]              # the single next session
        for x, zx in shocked.items():
            if x not in pos:
                continue
            col = C[pos[x]]
            partners = [(syms[j], col[j]) for j in np.nonzero(np.abs(col) >= MIN_CORR)[0]
                        if syms[j] != x]
            if not partners:
                continue
            want = 1.0 if zx > 0 else -1.0
            for sym, c in partners:
                sd = vol[sym].iloc[ti]
                r = nxt.get(sym, np.nan)
                if np.isfinite(r) and np.isfinite(sd) and sd >= MIN_VOL:
                    rows.append((idx[ti].date(), "partner",
                                 want * np.sign(c) * r / sd))
            pool = [s for s in syms if s != x and abs(col[pos[s]]) < MIN_CORR]
            for sym in rng.choice(pool, size=min(len(partners), len(pool)), replace=False):
                sd = vol[sym].iloc[ti]
                r = nxt.get(sym, np.nan)
                if np.isfinite(r) and np.isfinite(sd) and sd >= MIN_VOL:
                    rows.append((idx[ti].date(), "control", want * r / sd))

    df = pd.DataFrame(rows, columns=["date", "kind", "resp"])
    print(f"episodes span {df.date.min()} .. {df.date.max()}   dates {df.date.nunique():,}")
    print(f"observations: partner {int((df.kind=='partner').sum()):,}   "
          f"control {int((df.kind=='control').sum()):,}")

    per = df.pivot_table(index="date", columns="kind", values="resp", aggfunc="mean").dropna()
    d = per["partner"] - per["control"]
    se = d.std(ddof=1) / np.sqrt(len(d))                # clustered by date (D-71)
    mde = 2.8 * se
    print(f"\n{'':<26}{'mean':>10}{'sd':>10}")
    print(f"{'partner (next session)':<26}{per['partner'].mean():>10.4f}"
          f"{per['partner'].std():>10.3f}")
    print(f"{'control':<26}{per['control'].mean():>10.4f}{per['control'].std():>10.3f}")
    print(f"\nPRIMARY  partner - control = {d.mean():+.4f} ({se:.4f})  "
          f"z = {d.mean()/se:+.2f}   over {len(d):,} date clusters")
    print(f"realised MDE (2.8*SE) = {mde:.4f} sigma   threshold = {THRESHOLD}")
    print(f"  {'UNDERPOWERED' if mde > THRESHOLD else 'adequately powered'}")

    ok_rule = d.mean() >= THRESHOLD and d.mean() / se >= 2
    print("\n" + "=" * 64)
    print(f"PRE-COMMITTED RULE: diff >= {THRESHOLD} AND z >= 2")
    print(f"  VERDICT: {'PROPAGATION CLAIMED' if ok_rule else 'NULL'}"
          + ("  (and underpowered)" if mde > THRESHOLD else ""))
    print("=" * 64)


if __name__ == "__main__":
    main()
