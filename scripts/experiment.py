"""The pre-registered test (D-31). One feature, one label, one comparison.

Feature  : leaders moved, candidate has not repriced.
           For candidate C at fire date t, take the 20 names most correlated with
           C over the trailing 60 sessions, drawn from the wide universe and
           excluding the signal's own tickers (D-27). Fires when the largest
           standardised 3-session move among those neighbours is >= 2 sigma while
           C's own standardised 3-session move is < 0.5 sigma.
Label    : C's underlying forward return over the next 10 sessions (D-29),
           sign-matched to the signal direction.
Test     : hit rate, feature-fired vs not.

Nothing here is tuned. Parameters come from D-31 and are not to be swept.
"""

from __future__ import annotations

import math

import pandas as pd

TRAIL = 60      # trailing sessions for correlation and sigma
TOPK = 20       # neighbourhood size
MOVE_WIN = 3    # sessions over which "the leader moved"
LEADER_SIGMA = 2.0
QUIET_SIGMA = 0.5
FWD = 10        # forward sessions for the label


def load():
    fires = pd.read_csv("data/fires.csv")
    fires["posted_at"] = pd.to_datetime(fires.posted_at, utc=True, format="ISO8601")
    fires = fires[~fires.is_etf].copy()          # single names only (D-31)

    bars = pd.read_parquet("data/bars.parquet")
    bars["date"] = pd.to_datetime(bars.timestamp, utc=True).dt.normalize()
    px = bars.pivot_table(index="date", columns="symbol", values="close")
    return fires, px


def main() -> None:
    fires, px = load()
    rets = px.pct_change()
    sessions = px.index
    signal_universe = set(pd.read_csv("data/fires.csv").ticker.unique())

    rows, skipped = [], {"no_bars": 0, "short_history": 0, "no_forward": 0}

    for _, f in fires.iterrows():
        sym, tdate = f.ticker, f.posted_at.normalize()
        if sym not in px.columns:
            skipped["no_bars"] += 1
            continue

        # t = first session strictly after the alert (no same-bar look-ahead)
        later = sessions[sessions > tdate]
        if len(later) < FWD + 1:
            skipped["no_forward"] += 1
            continue
        t = later[0]
        ti = sessions.get_loc(t)
        if ti < TRAIL + MOVE_WIN:
            skipped["short_history"] += 1
            continue

        # trailing window ends the session before t — strictly point-in-time
        win = rets.iloc[ti - TRAIL : ti]
        cand = win[sym]
        if cand.isna().sum() > TRAIL * 0.2:
            skipped["short_history"] += 1
            continue

        # neighbourhood: most correlated, from the wide universe only (D-27)
        pool = win.drop(columns=[c for c in signal_universe if c in win.columns])
        pool = pool.loc[:, pool.notna().sum() >= TRAIL * 0.8]
        corr = pool.corrwith(cand).dropna()
        neigh = corr.abs().nlargest(TOPK).index

        # standardised 3-session moves ending the session before t
        mv = rets.iloc[ti - MOVE_WIN : ti]
        scale = math.sqrt(MOVE_WIN)
        n_move = mv[neigh].sum() / (win[neigh].std() * scale)
        c_move = mv[sym].sum() / (cand.std() * scale)

        fired = bool(n_move.abs().max() >= LEADER_SIGMA and abs(c_move) < QUIET_SIGMA)

        fwd = px[sym].iloc[ti + FWD] / px[sym].iloc[ti] - 1.0
        hit = bool(fwd > 0) if f.direction == "up" else bool(fwd < 0)

        rows.append(
            dict(signal_id=f.signal_id, ticker=sym, direction=f.direction, t=t,
                 fired=fired, leader_sigma=float(n_move.abs().max()),
                 cand_sigma=float(abs(c_move)), fwd_ret=float(fwd), hit=hit)
        )

    d = pd.DataFrame(rows)
    d.to_csv("data/results.csv", index=False)

    print(f"population (single names, D-31): {len(fires)}")
    for k, v in skipped.items():
        print(f"  skipped, {k}: {v}")
    print(f"  evaluable: {len(d)}\n")

    print(f"base rate (all)      : {d.hit.mean():.1%}  n={len(d)}")
    a, b = d[d.fired], d[~d.fired]
    print(f"feature FIRED        : {a.hit.mean():.1%}  n={len(a)}" if len(a)
          else "feature FIRED        : n=0")
    print(f"feature not fired    : {b.hit.mean():.1%}  n={len(b)}" if len(b)
          else "feature not fired    : n=0")

    if len(a) and len(b):
        diff = a.hit.mean() - b.hit.mean()
        se = math.sqrt(a.hit.mean() * (1 - a.hit.mean()) / len(a)
                       + b.hit.mean() * (1 - b.hit.mean()) / len(b))
        print(f"\ndifference           : {diff:+.1%}")
        print(f"standard error       : {se:.1%}")
        if se > 0:
            print(f"z                    : {diff / se:+.2f}")
        mdd = 2.8 * math.sqrt(0.25 / len(a) + 0.25 / len(b))
        print(f"min detectable (80%) : {mdd:.1%}")
    print(f"\nmean forward return  : {d.fwd_ret.mean():+.2%}")
    print("wrote data/results.csv")


if __name__ == "__main__":
    main()
