"""The second pre-registered test (D-33). Median split, 2-session horizon.

Differs from scripts/experiment.py (D-31) in exactly two declared ways:
  - forward label is 2 sessions, not 10 (hold_minutes median ~22h, D-32)
  - the feature is a continuous score split at its own median, not a threshold
    conjunction, so there is no threshold to tune and buckets are balanced

Everything else — neighbourhood, trailing window, point-in-time rules,
population — is unchanged. experiment.py is kept intact so D-31 stays
reproducible.

This is the SECOND test on the same 64 observations. Its nominal significance is
overstated. See D-33.
"""

from __future__ import annotations

import math

import pandas as pd

TRAIL = 60
TOPK = 20
MOVE_WIN = 3
FWD = 2  # D-33: was 10


def main() -> None:
    fires = pd.read_csv("data/fires.csv")
    fires["posted_at"] = pd.to_datetime(fires.posted_at, utc=True, format="ISO8601")
    signal_universe = set(fires.ticker.unique())
    fires = fires[~fires.is_etf].copy()

    bars = pd.read_parquet("data/bars.parquet")
    bars["date"] = pd.to_datetime(bars.timestamp, utc=True).dt.normalize()
    px = bars.pivot_table(index="date", columns="symbol", values="close")
    rets = px.pct_change()
    sessions = px.index

    rows, skipped = [], {"no_bars": 0, "short_history": 0, "no_forward": 0}

    for _, f in fires.iterrows():
        sym, tdate = f.ticker, f.posted_at.normalize()
        if sym not in px.columns:
            skipped["no_bars"] += 1
            continue
        later = sessions[sessions > tdate]
        if len(later) < FWD + 1:
            skipped["no_forward"] += 1
            continue
        t = later[0]
        ti = sessions.get_loc(t)
        if ti < TRAIL + MOVE_WIN:
            skipped["short_history"] += 1
            continue

        win = rets.iloc[ti - TRAIL : ti]
        cand = win[sym]
        if cand.isna().sum() > TRAIL * 0.2:
            skipped["short_history"] += 1
            continue

        pool = win.drop(columns=[c for c in signal_universe if c in win.columns])
        pool = pool.loc[:, pool.notna().sum() >= TRAIL * 0.8]
        neigh = pool.corrwith(cand).dropna().abs().nlargest(TOPK).index

        mv = rets.iloc[ti - MOVE_WIN : ti]
        scale = math.sqrt(MOVE_WIN)
        n_move = (mv[neigh].sum() / (win[neigh].std() * scale)).abs().max()
        c_move = abs(mv[sym].sum() / (cand.std() * scale))

        fwd = px[sym].iloc[ti + FWD] / px[sym].iloc[ti] - 1.0
        rows.append(
            dict(signal_id=f.signal_id, ticker=sym, direction=f.direction, t=t,
                 score=float(n_move - c_move), fwd_ret=float(fwd),
                 hit=bool(fwd > 0) if f.direction == "up" else bool(fwd < 0))
        )

    d = pd.DataFrame(rows)
    cut = d.score.median()
    d["high"] = d.score > cut
    d.to_csv("data/results2.csv", index=False)

    print(f"population (single names): {len(fires)}")
    for k, v in skipped.items():
        print(f"  skipped, {k}: {v}")
    print(f"  evaluable: {len(d)}   horizon: {FWD} sessions\n")
    print(f"score median (split point): {cut:+.3f}\n")
    print(f"base rate (all)          : {d.hit.mean():.1%}  n={len(d)}")

    a, b = d[d.high], d[~d.high]
    print(f"HIGH score (leaders moved, candidate quiet): {a.hit.mean():.1%}  n={len(a)}")
    print(f"LOW  score                                 : {b.hit.mean():.1%}  n={len(b)}")

    diff = a.hit.mean() - b.hit.mean()
    se = math.sqrt(a.hit.mean() * (1 - a.hit.mean()) / len(a)
                   + b.hit.mean() * (1 - b.hit.mean()) / len(b))
    print(f"\ndifference               : {diff:+.1%}")
    print(f"standard error           : {se:.1%}")
    print(f"z (UNCORRECTED, 2nd look): {diff / se:+.2f}" if se else "z: undefined")
    mdd = 2.8 * math.sqrt(0.25 / len(a) + 0.25 / len(b))
    print(f"min detectable (80%)     : {mdd:.1%}")
    print(f"\nmean forward return      : {d.fwd_ret.mean():+.2%}")
    print(f"  high score : {a.fwd_ret.mean():+.2%}")
    print(f"  low  score : {b.fwd_ret.mean():+.2%}")
    print("wrote data/results2.csv")


if __name__ == "__main__":
    main()
